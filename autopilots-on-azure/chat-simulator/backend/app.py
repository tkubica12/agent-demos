import asyncio
import contextlib
import copy
import json
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.websockets import WebSocketDisconnect

from . import db, domain as d, schemas
from .auth import Identity, TokenVerifier
from .channel import AgentChannel, require_agent
from .config import ROOT, TEST_PERSONAS, Settings
from .connector import register_routes
from .demo import DemoAuth, DemoExpiry, DemoLogin
from .registration import registrations, seed_agents
from .realtime import Connection, Hub
from .storage import LocalFileStorage
from .uploads import receive_file


class SecurityHeaders:
    def __init__(self, app, settings):
        self.app = app
        self.settings = settings
        connect = "'self' https://login.microsoftonline.com https://*.msauth.net https://*.msftauth.net"
        if settings.test_idp:
            connect += f" {settings.test_issuer}"
        self.csp = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; "
            f"connect-src {connect}; frame-src https://login.microsoftonline.com; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secured(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                policy = [
                    (b"content-security-policy", self.csp.encode("ascii")),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (b"cache-control", b"no-store"),
                ]
                names = {name for name, _ in policy}
                message["headers"] = [(name, value) for name, value in headers if name.lower() not in names] + policy
            await send(message)

        await self.app(scope, receive, secured)


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    declared_agents = registrations(settings)
    storage = LocalFileStorage(settings.storage_path)
    engine, sessions = db.database(settings.database_url)
    seed_agents(sessions, declared_agents, settings)
    channel_verifier = TokenVerifier(settings)
    verifier = DemoAuth(settings) if settings.auth_mode == "demo" else channel_verifier
    hub = Hub(sessions)
    channel_service = AgentChannel(settings, sessions, channel_verifier, hub, storage)

    @asynccontextmanager
    async def lifespan(_):
        yield
        for connection in tuple(hub.connections):
            await connection.socket.close(code=1001)
        await channel_service.close()
        await verifier.close()
        if channel_verifier is not verifier:
            await channel_verifier.close()
        engine.dispose()

    app = FastAPI(title="Agent Chat Simulator", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.sessions = sessions
    app.state.verifier = verifier
    app.state.hub = hub
    app.state.storage = storage
    app.state.channel = channel_service
    if settings.auth_mode == "demo":
        app.add_middleware(DemoExpiry, settings=settings)
    app.add_middleware(SecurityHeaders, settings=settings)
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
                       allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"])

    async def session():
        with sessions() as value:
            yield value

    async def identity(request: Request):
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "Bearer access token required", headers={"WWW-Authenticate": "Bearer"})
        return await verifier.verify(token)

    async def current_user(claims: Identity = Depends(identity), session=Depends(session)):
        try:
            return d.register(session, claims)
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "Identity registration conflicted; retry the request") from None

    def commit(session):
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "Resource already exists or was changed concurrently") from None

    def new_channel(session, team, name):
        channel_id = db.new_id()
        conversation_id = f"19:{channel_id}@thread.tacv2"
        session.add(db.Conversation(id=conversation_id, kind="channel", name=name, team_id=team.id))
        session.flush()
        channel = db.Channel(id=channel_id, team_id=team.id, name=name, conversation_id=conversation_id)
        session.add(channel)
        return channel

    def installable_agent(session, user):
        if user.source == "agent":
            require_agent(session, user.subject, enabled=True)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/config")
    async def config():
        value = {"authMode": settings.auth_mode,
                 "tenantId": settings.tenant_id, "clientId": settings.client_id, "scope": settings.scope,
                 "redirectUri": settings.redirect_uri, "channelId": settings.channel_id,
                 "agentChannel": {"clientId": settings.channel_client_id, "serviceUrl": settings.service_url,
                                  "tenantId": settings.tenant_id}}
        if settings.test_idp:
            value["testAuth"] = {
                "issuer": settings.test_issuer, "tokenEndpoint": f"{settings.test_issuer}/token",
                "personas": TEST_PERSONAS,
            }
        if settings.auth_mode == "demo":
            value["demo"] = {
                "expiresAt": settings.demo_expires_at,
                "banner": f"DEMO MODE \u2013 no real authentication \u2013 test data only \u2013 expires {settings.demo_expires_at}",
            }
            if time.time() >= settings.demo_expiry:
                return JSONResponse(value, status_code=410)
        return value

    @app.post("/api/demo/login")
    async def demo_login(body: DemoLogin, request: Request):
        if settings.auth_mode != "demo":
            raise HTTPException(404, "Demo authentication is not enabled")
        return verifier.login(body, request.client.host if request.client else "unknown")

    @app.get("/api/me")
    async def me(user=Depends(current_user), claims=Depends(identity)):
        return {**d.user_json(user), "authSource": claims.source}

    @app.get("/api/users")
    async def users(user=Depends(current_user), session=Depends(session)):
        return [d.user_json(u) for u in session.scalars(select(db.User).order_by(db.User.display_name, db.User.id))]

    @app.post("/api/users", status_code=201)
    async def preregister(body: schemas.Preregister, user=Depends(current_user), session=Depends(session)):
        if "Admin" not in user.roles:
            raise HTTPException(403, "Admin role required")
        existing = session.scalar(select(db.User).where(db.User.source == "entra", db.User.upn == body.upn))
        if existing:
            return d.user_json(existing)
        registered = db.User(source="entra", tenant_id=settings.tenant_id,
                             upn=body.upn, display_name=body.displayName, roles=[])
        session.add(registered)
        commit(session)
        return d.user_json(registered)

    @app.get("/api/teams")
    async def teams(user=Depends(current_user), session=Depends(session)):
        values = session.scalars(select(db.Team).join(db.TeamMember).where(
            db.TeamMember.user_id == user.id,
        ).order_by(db.Team.name, db.Team.id))
        return [d.team_json(session, t) for t in values]

    @app.post("/api/teams", status_code=201)
    async def create_team(body: schemas.Named, user=Depends(current_user), session=Depends(session)):
        team = db.Team(name=body.name, owner_id=user.id)
        session.add(team)
        session.flush()
        session.add(db.TeamMember(team_id=team.id, user_id=user.id))
        new_channel(session, team, "General")
        commit(session)
        value = d.team_json(session, team)
        hub.publish({"type": "membership.changed", "teamId": team.id}, {user.id})
        return value

    @app.get("/api/teams/{teamId}")
    async def get_team(teamId: str, user=Depends(current_user), session=Depends(session)):
        return d.team_json(session, d.require_team(session, teamId, user.id))

    @app.post("/api/teams/{teamId}/channels", status_code=201)
    async def create_channel(teamId: str, body: schemas.Named, user=Depends(current_user), session=Depends(session)):
        team = d.require_team(session, teamId, user.id, owner=True)
        channel = new_channel(session, team, body.name)
        commit(session)
        member_ids = set(session.scalars(select(db.TeamMember.user_id).where(db.TeamMember.team_id == teamId)))
        hub.publish({"type": "membership.changed", "teamId": teamId}, member_ids)
        conversation = session.get(db.Conversation, channel.conversation_id)
        agents = channel_service.agents(session, conversation)
        if agents:
            await channel_service.installation(
                session, conversation, user, [session.get(db.User, a.user_id) for a in agents],
            )
        return d.channel_json(channel)

    @app.get("/api/teams/{teamId}/members")
    async def get_members(teamId: str, user=Depends(current_user), session=Depends(session)):
        d.require_team(session, teamId, user.id)
        return [d.user_json(u) for u in session.scalars(select(db.User).join(
            db.TeamMember, db.TeamMember.user_id == db.User.id,
        ).where(db.TeamMember.team_id == teamId).order_by(db.User.display_name, db.User.id))]

    @app.post("/api/teams/{teamId}/members")
    async def add_member(teamId: str, body: schemas.Member, user=Depends(current_user), session=Depends(session)):
        d.require_team(session, teamId, user.id, owner=True)
        member = session.get(db.User, body.userId)
        if member is None:
            raise HTTPException(404, "Registered directory user not found")
        installable_agent(session, member)
        added = session.get(db.TeamMember, (teamId, body.userId)) is None
        if added:
            session.add(db.TeamMember(team_id=teamId, user_id=body.userId))
            commit(session)
        member_ids = set(session.scalars(select(db.TeamMember.user_id).where(db.TeamMember.team_id == teamId)))
        hub.publish({"type": "membership.changed", "teamId": teamId}, member_ids)
        if added:
            general = session.scalar(select(db.Channel).where(
                db.Channel.team_id == teamId, db.Channel.name == "General",
            ))
            conversation = session.get(db.Conversation, general.conversation_id)
            await channel_service.installation(session, conversation, user, [member])
        return d.user_json(member)

    @app.delete("/api/teams/{teamId}/members/{userId}", status_code=204)
    async def remove_member(teamId: str, userId: str, user=Depends(current_user), session=Depends(session)):
        team = d.require_team(session, teamId, user.id, owner=True)
        if userId == team.owner_id:
            raise HTTPException(409, "Cannot remove the team owner")
        member = session.get(db.TeamMember, (teamId, userId))
        if member is None:
            raise HTTPException(404, "Team member not found")
        removed_agents = list(session.scalars(select(db.Agent).where(db.Agent.user_id == userId)))
        session.delete(member)
        commit(session)
        member_ids = set(session.scalars(select(db.TeamMember.user_id).where(db.TeamMember.team_id == teamId)))
        hub.publish({"type": "membership.removed", "teamId": teamId}, {userId})
        hub.publish({"type": "membership.changed", "teamId": teamId}, member_ids)
        general = session.scalar(select(db.Channel).where(
            db.Channel.team_id == teamId, db.Channel.name == "General",
        ))
        await channel_service.installation(session, session.get(db.Conversation, general.conversation_id),
                                           user, [session.get(db.User, userId)], removed=True,
                                           removed_agents=removed_agents)
        return Response(status_code=204)

    @app.get("/api/chats")
    async def chats(user=Depends(current_user), session=Depends(session)):
        return [d.chat_json(session, chat) for chat in session.scalars(select(db.Chat).join(
            db.ChatMember,
        ).where(db.ChatMember.user_id == user.id).order_by(db.Chat.name, db.Chat.id))]

    @app.post("/api/chats", status_code=201)
    async def create_chat(body: schemas.ChatCreate, user=Depends(current_user), session=Depends(session)):
        member_ids = set(body.memberIds) | {user.id}
        if len(member_ids) < 2:
            raise HTTPException(422, "A chat requires at least two people")
        members = [session.get(db.User, uid) for uid in sorted(member_ids)]
        if any(member is None for member in members):
            raise HTTPException(422, "All chat participants must be registered directory users")
        for member in members:
            installable_agent(session, member)
        kind = "personal" if len(member_ids) == 2 else "groupChat"
        if kind == "groupChat" and (not body.name or not body.name.strip()):
            raise HTTPException(422, "A group chat requires a name")
        if kind == "personal":
            for existing in session.scalars(select(db.Chat).join(db.Conversation).where(
                db.Conversation.kind == "personal",
            )):
                conversation = session.get(db.Conversation, existing.conversation_id)
                if d.conversation_users(session, conversation) == member_ids:
                    return d.chat_json(session, existing)
        name = body.name or ", ".join(member.display_name for member in members)
        conversation = db.Conversation(id=db.new_id(), kind=kind, name=name)
        session.add(conversation)
        session.flush()
        chat = db.Chat(name=name, conversation_id=conversation.id)
        session.add(chat)
        session.flush()
        for member_id in sorted(member_ids):
            session.add(db.ChatMember(chat_id=chat.id, user_id=member_id))
        commit(session)
        hub.publish({"type": "chat.created", "chatId": chat.id,
                     "conversationId": chat.conversation_id}, member_ids)
        await channel_service.installation(session, conversation, user, members)
        return d.chat_json(session, chat)

    @app.get("/api/chats/{chatId}")
    async def get_chat(chatId: str, user=Depends(current_user), session=Depends(session)):
        chat = session.get(db.Chat, chatId)
        if chat is None:
            raise HTTPException(404, "Chat not found")
        d.require_conversation(session, chat.conversation_id, user.id)
        return d.chat_json(session, chat)

    def member_chat(session, chat_id, user_id, mutable=False):
        chat = session.get(db.Chat, chat_id)
        if chat is None:
            raise HTTPException(404, "Chat not found")
        conversation = d.require_conversation(session, chat.conversation_id, user_id)
        if mutable and conversation.kind != "groupChat":
            raise HTTPException(409, "Personal chat membership cannot be changed")
        return chat, conversation

    @app.get("/api/chats/{chatId}/members")
    async def chat_members(chatId: str, user=Depends(current_user), session=Depends(session)):
        """Any participant can list members; personal chats remain read-only."""
        chat, _ = member_chat(session, chatId, user.id)
        return [d.user_json(member) for member in session.scalars(select(db.User).join(
            db.ChatMember, db.ChatMember.user_id == db.User.id,
        ).where(db.ChatMember.chat_id == chat.id).order_by(db.User.display_name, db.User.id))]

    @app.post("/api/chats/{chatId}/members")
    async def add_chat_member(chatId: str, body: schemas.Member,
                              user=Depends(current_user), session=Depends(session)):
        """Any current group-chat participant may add a registered person or enabled agent."""
        chat, conversation = member_chat(session, chatId, user.id, mutable=True)
        member = session.get(db.User, body.userId)
        if member is None:
            raise HTTPException(404, "Registered directory user not found")
        installable_agent(session, member)
        if session.get(db.ChatMember, (chatId, body.userId)) is None:
            session.add(db.ChatMember(chat_id=chat.id, user_id=member.id))
            commit(session)
            hub.publish({"type": "membership.changed", "chatId": chat.id,
                         "conversationId": conversation.id}, d.conversation_users(session, conversation))
            await channel_service.installation(session, conversation, user, [member], resource_id=chat.id)
        return d.user_json(member)

    @app.delete("/api/chats/{chatId}/members/{userId}", status_code=204)
    async def remove_chat_member(chatId: str, userId: str,
                                 user=Depends(current_user), session=Depends(session)):
        """Any current group-chat participant may remove another participant or leave themselves."""
        chat, conversation = member_chat(session, chatId, user.id, mutable=True)
        membership = session.get(db.ChatMember, (chatId, userId))
        if membership is None:
            raise HTTPException(404, "Chat member not found")
        member = session.get(db.User, userId)
        removed_agents = list(session.scalars(select(db.Agent).where(db.Agent.user_id == userId)))
        session.delete(membership)
        commit(session)
        hub.publish({"type": "membership.removed", "chatId": chat.id}, {userId})
        hub.publish({"type": "membership.changed", "chatId": chat.id,
                     "conversationId": conversation.id}, d.conversation_users(session, conversation))
        await channel_service.installation(session, conversation, user, [member], removed=True,
                                           removed_agents=removed_agents, resource_id=chat.id)
        return Response(status_code=204)

    @app.get("/api/conversations/{conversationId}/messages")
    async def messages(conversationId: str, user=Depends(current_user), session=Depends(session)):
        d.require_conversation(session, conversationId, user.id)
        return [d.message_json(session, m) for m in session.scalars(select(db.Message).where(
            db.Message.conversation_id == conversationId,
        ).order_by(db.Message.sequence)) if d.message_visible(m)]

    @app.post("/api/conversations/{conversationId}/messages", status_code=201)
    async def create_message(conversationId: str, body: schemas.MessageCreate,
                             user=Depends(current_user), session=Depends(session)):
        conversation = d.require_conversation(session, conversationId, user.id)
        if not body.text.strip() and not body.attachmentIds:
            raise HTTPException(422, "A message requires text or an attachment")
        root_id = None
        if body.replyToId:
            parent = d.require_message(session, body.replyToId, user.id)
            if parent.conversation_id != conversationId:
                raise HTTPException(422, "Reply must belong to this conversation")
            root_id = parent.reply_to_id or parent.id
        files = d.attachable_files(session, body.attachmentIds, conversationId, user.id)
        text, entities = d.mention_content(session, conversation, body.text, body.mentions)
        message_id = db.new_id()
        activity = d.make_activity(
            session, settings, conversation, user, message_id, text, body.textFormat, root_id, entities,
            [{"contentType": "application/vnd.microsoft.teams.file.download.info", "name": f.name,
              "content": {"downloadUrl": f"/api/files/{f.id}/content", "uniqueId": f.id,
                          "fileType": f.name.rsplit(".", 1)[-1], "size": f.size}}
             for f in files],
        )
        message = db.Message(
            id=message_id, conversation_id=conversationId, author_id=user.id, reply_to_id=root_id,
            created_at=activity["timestamp"], activity=activity,
        )
        session.add(message)
        session.flush()
        for file in files:
            file.message_id = message_id
        d.revision(session, message, activity)
        commit(session)
        value = d.message_json(session, message)
        hub.publish({"type": "message.created", "conversationId": conversationId, "message": value})
        await channel_service.message(session, conversation, user, activity)
        return value

    @app.get("/api/messages/{messageId}")
    async def get_message(messageId: str, user=Depends(current_user), session=Depends(session)):
        return d.message_json(session, d.require_message(session, messageId, user.id))

    @app.patch("/api/messages/{messageId}")
    async def edit_message(messageId: str, body: schemas.MessageEdit,
                           user=Depends(current_user), session=Depends(session)):
        message = d.require_message(session, messageId, user.id, own=True)
        if message.deleted:
            raise HTTPException(409, "Deleted messages cannot be edited")
        if not body.text.strip() and not message.activity.get("attachments"):
            raise HTTPException(422, "A message requires text or an attachment")
        conversation = session.get(db.Conversation, message.conversation_id)
        text, entities = d.mention_content(session, conversation, body.text, body.mentions)
        value = copy.deepcopy(message.activity)
        value.update({"type": "message", "text": text, "textFormat": body.textFormat, "entities": entities})
        d.update_message(session, message, value)
        commit(session)
        value = d.message_json(session, message)
        hub.publish({"type": "message.updated", "conversationId": message.conversation_id, "message": value})
        return value

    @app.delete("/api/messages/{messageId}")
    async def delete_message(messageId: str, user=Depends(current_user), session=Depends(session)):
        message = d.require_message(session, messageId, user.id, own=True)
        d.delete_message(session, message)
        commit(session)
        value = d.message_json(session, message)
        hub.publish({"type": "message.deleted", "conversationId": message.conversation_id, "message": value})
        return value

    async def change_reaction(session, message, user, emoji, remove):
        if message.deleted:
            raise HTTPException(409, "Deleted messages cannot receive reactions")
        reaction = session.get(db.Reaction, (message.id, user.id, emoji))
        changed = False
        if remove and reaction:
            session.delete(reaction)
            changed = True
        elif not remove and not reaction:
            session.add(db.Reaction(message_id=message.id, user_id=user.id, emoji=emoji))
            changed = True
        if changed:
            d.revision(session, message, d.change_activity(message, "messageReaction", user, emoji, remove))
            commit(session)
        value = d.message_json(session, message)
        hub.publish({"type": "reaction.changed", "conversationId": message.conversation_id, "message": value,
                     "actor": d.user_json(user), "occurredAt": d.now()})
        if changed:
            await channel_service.reaction(session, message, user, emoji, remove)
        return value

    @app.post("/api/messages/{messageId}/reactions")
    async def add_reaction(messageId: str, body: schemas.ReactionCreate,
                           user=Depends(current_user), session=Depends(session)):
        return await change_reaction(session, d.require_message(session, messageId, user.id), user, body.emoji, False)

    @app.delete("/api/messages/{messageId}/reactions/{emoji}")
    async def remove_reaction(messageId: str, emoji: str, user=Depends(current_user), session=Depends(session)):
        message = d.require_message(session, messageId, user.id)
        try:
            schemas.validate_emoji(emoji)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return await change_reaction(session, message, user, emoji, True)

    @app.get("/api/conversations/{conversationId}/files")
    async def files(conversationId: str, user=Depends(current_user), session=Depends(session)):
        d.require_conversation(session, conversationId, user.id)
        return [d.file_json(f) for f in session.scalars(select(db.FileRecord).where(
            db.FileRecord.conversation_id == conversationId,
        ).order_by(db.FileRecord.name, db.FileRecord.id))]

    @app.post("/api/conversations/{conversationId}/files", status_code=201, openapi_extra={
        "requestBody": {"required": True, "content": {"multipart/form-data": {
            "schema": {"type": "object", "required": ["file"],
                       "properties": {"file": {"type": "string", "format": "binary"}}}
        }}}
    })
    async def upload(conversationId: str, request: Request, user=Depends(current_user), session=Depends(session)):
        d.require_conversation(session, conversationId, user.id)
        session.rollback()  # No read transaction is held while a client streams a file.
        file_id = db.new_id()
        try:
            metadata = await receive_file(request, storage, file_id, settings.max_file_bytes)
            d.require_conversation(session, conversationId, user.id)
            file = db.FileRecord(id=file_id, conversation_id=conversationId, uploader_id=user.id, **metadata)
            session.add(file)
            commit(session)
            hub.publish({"type": "file.created", "conversationId": conversationId, "fileId": file.id})
            return d.file_json(file)
        except BaseException:
            session.rollback()
            storage.discard(file_id)
            raise

    @app.get("/api/files/{fileId}")
    async def file_metadata(fileId: str, user=Depends(current_user), session=Depends(session)):
        return d.file_json(d.require_file(session, fileId, user.id))

    @app.get("/api/files/{fileId}/content")
    async def file_content(fileId: str, user=Depends(current_user), session=Depends(session)):
        file = d.require_file(session, fileId, user.id)
        return d.file_response(storage, file)

    @app.get("/api/activities/export")
    async def export(user=Depends(current_user), session=Depends(session)):
        authorized = {
            c.id for c in session.scalars(select(db.Conversation))
            if d.can_read(session, c.id, user.id)
        }
        return [r.activity for r in session.scalars(select(db.ActivityRevision).where(
            db.ActivityRevision.conversation_id.in_(authorized),
        ).order_by(db.ActivityRevision.sequence))]

    @app.websocket("/api/ws")
    async def websocket(socket: WebSocket):
        if socket.headers.get("origin") not in settings.allowed_origins:
            await socket.close(code=1008, reason="Origin not allowed")
            return
        if socket.query_params:
            await socket.close(code=1008, reason="Query parameters are not accepted")
            return
        await socket.accept()
        connection = None
        writer = None
        try:
            raw = await asyncio.wait_for(socket.receive_text(), timeout=5)
            if len(raw) > 25 * 1024:
                raise HTTPException(401, "Invalid authentication frame")
            frame = json.loads(raw)
            if not isinstance(frame, dict) or set(frame) != {"token"}:
                raise HTTPException(401, "Authentication frame required")
            claims = await verifier.verify(frame["token"])
            with sessions() as session:
                user = d.register(session, claims)
                connection = Connection(socket, user.id, claims)
            hub.connections.add(connection)
            await socket.send_json({"type": "ready"})
            writer = asyncio.create_task(hub.writer(connection))
            while True:
                # The G2 protocol is server-push only after its authentication frame.
                await socket.receive_text()
        except (HTTPException, ValueError, TypeError, KeyError, asyncio.TimeoutError, IntegrityError):
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await socket.close(code=1008, reason="Invalid access token or authentication frame")
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if connection:
                hub.connections.discard(connection)
            if writer:
                writer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await writer

    register_routes(app, channel_service, session, current_user, commit, change_reaction)

    dist = ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
