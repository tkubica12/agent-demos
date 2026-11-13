import base64
import binascii
import copy
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy import select

from . import db, domain as d, schemas
from .channel import (
    ADAPTIVE_CARD, FILE_DOWNLOAD, REACTIONS, account, agent_json, bounded_json, delivery_json,
    require_agent, resolve_conversation,
)
from .config import validate_transport_url
from .uploads import store_bytes


def register_routes(app, channel, session_dependency, current_user, commit, change_reaction):
    router = APIRouter()
    session_dep = Depends(session_dependency)
    user_dep = Depends(current_user)
    settings, storage, hub = channel.settings, channel.storage, channel.hub

    async def admin(user=user_dep):
        if "Admin" not in user.roles:
            raise HTTPException(403, "Admin role required")
        return user

    async def connector_agent(request: Request, session=session_dep):
        if "demoKey" in request.path_params:
            return channel.demo_agent(session, request.path_params["demoKey"])
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "Bearer application token required", headers={"WWW-Authenticate": "Bearer"})
        claims = await channel.auth.verify(token, settings.channel_client_id)
        agent = session.scalar(select(db.Agent).where(db.Agent.client_id == claims["azp"],
                                                      db.Agent.enabled.is_(True)))
        if agent is None:
            raise HTTPException(401, "Calling application is not an enabled registered agent")
        return agent

    agent_dep = Depends(connector_agent)
    admin_dep = Depends(admin)

    @router.get("/api/agents")
    async def agents(_=admin_dep, session=session_dep):
        return [agent_json(session, agent) for agent in session.scalars(select(db.Agent).order_by(db.Agent.id))]

    @router.post("/api/agents", status_code=201)
    async def create_agent(body: schemas.AgentCreate, _=admin_dep, session=session_dep):
        if settings.auth_mode == "demo":
            raise HTTPException(409, "Register demo agents declaratively with demoKey and restart")
        try:
            client_id = str(UUID(body.clientId))
            endpoint = validate_transport_url(body.endpointUrl, settings.environment)
        except (ValueError, AttributeError):
            raise HTTPException(422, "A valid agent clientId and secure endpointUrl are required") from None
        if client_id in {settings.client_id, settings.channel_client_id}:
            raise HTTPException(422, "Agent must have its own Entra application")
        agent_id, user_id = db.new_id(), db.new_id()
        user = db.User(id=user_id, source="agent", subject=agent_id, tenant_id=settings.tenant_id,
                       upn=f"{agent_id}@agents.simulator", display_name=body.name, roles=[])
        session.add(user)
        session.flush()
        agent = db.Agent(id=agent_id, user_id=user_id, client_id=client_id,
                         endpoint_url=endpoint, enabled=body.enabled)
        session.add(agent)
        commit(session)
        hub.publish({"type": "directory.changed"})
        return agent_json(session, agent)

    @router.get("/api/agents/{agentId}")
    async def get_agent(agentId: str, _=admin_dep, session=session_dep):
        return agent_json(session, require_agent(session, agentId))

    @router.patch("/api/agents/{agentId}")
    async def edit_agent(agentId: str, body: schemas.AgentEdit, _=admin_dep, session=session_dep):
        agent = require_agent(session, agentId)
        if not body.model_fields_set or any(getattr(body, field) is None for field in body.model_fields_set):
            raise HTTPException(422, "Supply non-null agent fields to update")
        if body.name is not None:
            if not body.name.strip():
                raise HTTPException(422, "Name must not be blank")
            session.get(db.User, agent.user_id).display_name = body.name
        if body.endpointUrl is not None:
            try:
                agent.endpoint_url = validate_transport_url(body.endpointUrl, settings.environment)
            except ValueError:
                raise HTTPException(422, "A secure endpointUrl is required") from None
        if body.enabled is not None:
            agent.enabled = body.enabled
        commit(session)
        hub.publish({"type": "directory.changed"})
        return agent_json(session, agent)

    @router.get("/api/agents/{agentId}/deliveries")
    async def deliveries(agentId: str, _=admin_dep, session=session_dep):
        require_agent(session, agentId)
        return [delivery_json(value) for value in session.scalars(select(db.AgentDelivery).where(
            db.AgentDelivery.agent_id == agentId,
        ).order_by(db.AgentDelivery.created_at, db.AgentDelivery.id))]

    @router.post("/api/agents/{agentId}/deliveries/{deliveryId}/retry")
    async def retry(agentId: str, deliveryId: str, _=admin_dep, session=session_dep):
        require_agent(session, agentId)
        delivery = session.get(db.AgentDelivery, deliveryId)
        if delivery is None or delivery.agent_id != agentId:
            raise HTTPException(404, "Agent delivery not found")
        if delivery.activity["type"] == "invoke":
            raise HTTPException(409, "Invoke actions must be resubmitted by the authorized user")
        await channel.deliver(session, deliveryId)
        return delivery_json(session.get(db.AgentDelivery, deliveryId))

    @router.get("/api/agent-files/{fileId}/content")
    async def agent_file(fileId: str, capability: str = "", session=session_dep):
        if not 32 <= len(capability) <= 128:
            raise HTTPException(403, "A valid single-file download capability is required")
        grant = session.get(db.AgentFileCapability, hashlib.sha256(capability.encode()).hexdigest())
        if grant is None or grant.file_id != fileId or grant.expires_at <= time.time():
            raise HTTPException(403, "Download capability is invalid or expired")
        agent = require_agent(session, grant.agent_id, enabled=True)
        file = d.require_file(session, fileId, agent.user_id)
        if file.message_id:
            message = d.require_message(session, file.message_id, agent.user_id)
            if message.deleted:
                raise HTTPException(403, "The attached message was deleted")
        return d.file_response(storage, file)

    def normalize_activity(body):
        try:
            return d.validated_activity(body)
        except (ValidationError, TypeError, ValueError):
            raise HTTPException(422, "Invalid Activity Protocol body") from None

    def target_message(session, conversation, activity_id, agent, own=False):
        message = d.require_message(session, activity_id, agent.user_id, own=own)
        if message.conversation_id != conversation.id:
            raise HTTPException(403, "Activity belongs to another conversation")
        return message

    def attachment_files(session, conversation, agent, attachments, message_id=None):
        if not isinstance(attachments, list) or len(attachments) > 20:
            raise HTTPException(422, "At most 20 attachments are supported")
        file_ids, normalized = [], []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                raise HTTPException(422, "Invalid attachment")
            if attachment.get("contentType") == ADAPTIVE_CARD:
                content = attachment.get("content")
                if not isinstance(content, dict) or content.get("type") != "AdaptiveCard":
                    raise HTTPException(422, "Adaptive Card content is required")
                normalized.append(copy.deepcopy(attachment))
                continue
            content_url = attachment.get("contentUrl")
            if not isinstance(content_url, str):
                raise HTTPException(422, "Files must use an authenticated Connector upload/view URL")
            service_url = channel.service_url(agent)
            parsed, origin = urlparse(content_url), urlparse(service_url)
            path = unquote(parsed.path)
            prefix, suffix = f"{origin.path}/v3/attachments/", "/views/original"
            if (
                (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc)
                or parsed.query or parsed.fragment
                or not path.startswith(prefix) or not path.endswith(suffix)
            ):
                raise HTTPException(403, "Attachment URL is not an original view on this channel")
            file_id = path[len(prefix):-len(suffix)]
            file = d.require_file(session, file_id, agent.user_id)
            file_ids.append(file_id)
            normalized.append({
                "contentType": FILE_DOWNLOAD, "name": file.name,
                "content": {"uniqueId": file.id, "downloadUrl": f"/api/files/{file.id}/content",
                            "fileType": file.name.rsplit(".", 1)[-1], "size": file.size},
            })
        files = d.attachable_files(session, file_ids, conversation.id, agent.user_id, message_id)
        return files, normalized

    def validate_suggested_actions(session, conversation, suggested):
        actions = suggested["actions"]
        if len(actions) > 20:
            raise HTTPException(422, "At most 20 suggested actions are supported")
        if set(suggested.get("to", [])) - d.conversation_users(session, conversation):
            raise HTTPException(403, "Suggested-action recipients must be conversation members")
        for action in actions:
            kind = action["type"]
            if kind not in {"imBack", "postBack", "messageBack", "openUrl", "Action.Submit"}:
                raise HTTPException(422, "Unsupported suggested-action type")
            if kind == "Action.Submit":
                value = action.get("value")
                if set(action) != {"type", "title", "value"} or not isinstance(action.get("title"), str) or (
                    not action["title"].strip()
                ) or not isinstance(value, dict) or set(value) != {
                    "documentActionToken", "documentActionChoice",
                } or any(not isinstance(field, str) or not field.strip() or len(field) > 28000 for field in value.values()):
                    raise HTTPException(422, "Suggested Action.Submit requires a title and exact document-action string value")
            if kind in {"imBack", "postBack"} and not isinstance(action.get("value"), str):
                raise HTTPException(422, "imBack and postBack require string values")
            if kind in {"imBack", "postBack"} and len(action["value"]) > 28000:
                raise HTTPException(422, "Suggested-action text exceeds the 28000 character limit")
            if kind == "openUrl":
                value = action.get("value")
                if not isinstance(value, str):
                    raise HTTPException(422, "openUrl requires an HTTP(S) URL")
                try:
                    url = urlparse(value)
                    port = url.port
                except ValueError:
                    raise HTTPException(422, "openUrl requires a safe HTTP(S) URL") from None
                if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password or (
                    "\\" in value or any(ord(c) <= 32 for c in value)
                ) or (port is not None and not 1 <= port <= 65535):
                    raise HTTPException(422, "openUrl requires a safe HTTP(S) URL")
            for field in ("text", "displayText", "title"):
                if len(action.get(field, "")) > 28000:
                    raise HTTPException(422, "Suggested-action text exceeds the 28000 character limit")

    def outgoing_activity(session, conversation, agent, body, activity_id, root_id, route_root=None):
        author = session.get(db.User, agent.user_id)
        body = normalize_activity(body)
        if body.get("type") not in {"message", "typing"}:
            raise HTTPException(422, "Connector supports message and typing activities")
        if "suggestedActions" in body:
            if body["type"] != "message":
                raise HTTPException(422, "Suggested actions require a message activity")
            validate_suggested_actions(session, conversation, body["suggestedActions"])
        supplied_conversation = body.get("conversation", {}).get("id")
        thread = root_id or route_root
        valid_ids = {conversation.id, f"{conversation.id};messageid={thread}"} if thread else {conversation.id}
        if supplied_conversation is not None and supplied_conversation not in valid_ids:
            raise HTTPException(403, "Activity conversation does not match the authorized route")
        text = body.get("text", "")
        if not isinstance(text, str) or len(text) > 28000:
            raise HTTPException(422, "Message text exceeds the 28000 character limit")
        value = d.make_activity(session, settings, conversation, author, activity_id, text,
                                body.get("textFormat", "markdown"), root_id)
        for key in ("type", "summary", "value", "importance", "inputHint", "speak", "attachmentLayout",
                    "suggestedActions"):
            if key in body:
                value[key] = body[key]
        value["serviceUrl"] = settings.service_url
        value["locale"] = body.get("locale", "en-US")
        value["from"] = account(author, session)
        mentions = []
        for entity in body.get("entities", []):
            if entity.get("type") == "mention":
                try:
                    mentioned_id = entity["mentioned"]["id"]
                    if mentioned_id.startswith("28:"):
                        mentioned_agent = session.scalar(select(db.Agent).where(db.Agent.client_id == mentioned_id[3:]))
                        if mentioned_agent is None:
                            raise HTTPException(422, "Mentioned agent is not registered")
                        mentioned_id = mentioned_agent.user_id
                    mentions.append(schemas.Mention(userId=mentioned_id,
                                                    displayName=entity["mentioned"].get("name", "Member")))
                except (KeyError, TypeError, AttributeError, ValidationError):
                    raise HTTPException(422, "Invalid mention entity") from None
        value["text"], value["entities"] = d.mention_content(session, conversation, text, mentions)
        return value, body

    async def send(conversationId, request, agent, session, replyToId=None):
        conversation, root_id = resolve_conversation(session, conversationId, agent.user_id)
        body = normalize_activity(await bounded_json(request))
        reply_id = replyToId or body.get("replyToId")
        if reply_id:
            parent = session.scalar(select(db.Message).where(db.Message.id == reply_id))
            if parent is not None:
                parent = target_message(session, conversation, reply_id, agent)
                reply_root = parent.reply_to_id or parent.id
                if root_id and root_id != reply_root:
                    raise HTTPException(403, "Reply belongs to another thread")
                root_id = reply_root
            else:
                event_query = select(db.AgentDelivery).where(db.AgentDelivery.activity["id"].as_string() == reply_id)
                event = session.scalar(event_query.where(
                    db.AgentDelivery.agent_id == agent.id, db.AgentDelivery.conversation_id == conversation.id,
                ))
                if event is None and session.scalar(event_query.with_only_columns(db.AgentDelivery.id)) is not None:
                    raise HTTPException(403, "Reply event belongs to another agent or conversation")
                if event is not None:
                    _, _, event_root = event.activity["conversation"]["id"].partition(";messageid=")
                    if root_id and root_id != (event_root or None):
                        raise HTTPException(403, "Reply event belongs to another thread")
                    if event_root:
                        _, root_id = resolve_conversation(session, event.activity["conversation"]["id"], agent.user_id)
                else:
                    # Native continuation events have fresh UUIDs, not persisted message/thread IDs.
                    try:
                        UUID(reply_id)
                    except (ValueError, TypeError, AttributeError):
                        raise HTTPException(422, "Unknown reply references must be native continuation UUIDs") from None
        activity_id = db.new_id()
        value, body = outgoing_activity(session, conversation, agent, body, activity_id, root_id)
        author = session.get(db.User, agent.user_id)
        if value["type"] == "typing":
            # Native callers retain this ResourceResponse ID for subsequent message updates.
            value["channelData"]["typingPlaceholder"] = True
            message = db.Message(id=activity_id, conversation_id=conversation.id, author_id=agent.user_id,
                                 reply_to_id=root_id, created_at=value["timestamp"], activity=normalize_activity(value))
            session.add(message)
            session.flush()
            d.revision(session, message, message.activity)
            commit(session)
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
            hub.publish({"type": "typing", "conversationId": conversation.id,
                         "replyToId": root_id if conversation.kind == "channel" else None,
                         "actor": d.user_json(author), "expiresAt": expires_at})
            return {"id": activity_id}
        files, value["attachments"] = attachment_files(session, conversation, agent, body.get("attachments", []))
        if not value.get("text", "").strip() and not value["attachments"]:
            raise HTTPException(422, "A message requires text or an attachment")
        message = db.Message(id=activity_id, conversation_id=conversation.id, author_id=agent.user_id,
                             reply_to_id=root_id, created_at=value["timestamp"], activity=normalize_activity(value))
        session.add(message)
        session.flush()
        for file in files:
            file.message_id = message.id
        d.revision(session, message, message.activity)
        commit(session)
        hub.publish({"type": "message.created", "conversationId": conversation.id,
                     "message": d.message_json(session, message)})
        return {"id": activity_id}

    @router.post("/v3/conversations/{conversationId}/activities")
    async def send_activity(conversationId: str, request: Request, agent=agent_dep, session=session_dep):
        return await send(conversationId, request, agent, session)

    @router.post("/v3/conversations/{conversationId}/activities/{replyToId}")
    async def reply_activity(conversationId: str, replyToId: str, request: Request,
                             agent=agent_dep, session=session_dep):
        return await send(conversationId, request, agent, session, replyToId)

    @router.put("/v3/conversations/{conversationId}/activities/{activityId}")
    async def update_activity(conversationId: str, activityId: str, request: Request,
                              agent=agent_dep, session=session_dep):
        conversation, route_root = resolve_conversation(session, conversationId, agent.user_id)
        message = target_message(session, conversation, activityId, agent, own=True)
        if route_root and (message.reply_to_id or message.id) != route_root:
            raise HTTPException(403, "Activity belongs to another thread")
        body = await bounded_json(request)
        value, body = outgoing_activity(session, conversation, agent, body, message.id, message.reply_to_id, route_root)
        if value["type"] != "message":
            raise HTTPException(422, "Only messages can be updated")
        files, value["attachments"] = attachment_files(
            session, conversation, agent, body.get("attachments", []), message.id,
        )
        if not value.get("text", "").strip() and not value["attachments"]:
            raise HTTPException(422, "A message requires text or an attachment")
        value["timestamp"] = message.created_at
        was_typing = message.activity.get("type") == "typing"
        d.update_message(session, message, value)
        # Removed attachments remain downloadable to members but no longer belong to this message.
        for file in session.scalars(select(db.FileRecord).where(db.FileRecord.message_id == message.id)):
            file.message_id = None
        for file in files:
            file.message_id = message.id
        commit(session)
        hub.publish({"type": "message.created" if was_typing else "message.updated", "conversationId": conversation.id,
                     "message": d.message_json(session, message)})
        return {"id": message.id}

    @router.delete("/v3/conversations/{conversationId}/activities/{activityId}", status_code=204)
    async def delete_activity(conversationId: str, activityId: str, agent=agent_dep, session=session_dep):
        conversation, root_id = resolve_conversation(session, conversationId, agent.user_id)
        message = target_message(session, conversation, activityId, agent, own=True)
        if root_id and (message.reply_to_id or message.id) != root_id:
            raise HTTPException(403, "Activity belongs to another thread")
        was_typing = message.activity.get("type") == "typing"
        d.delete_message(session, message)
        commit(session)
        if not was_typing:
            hub.publish({"type": "message.deleted", "conversationId": conversation.id,
                         "message": d.message_json(session, message)})
        return Response(status_code=204)

    async def reaction(conversationId, activityId, reactionType, agent, session, removed):
        conversation, root_id = resolve_conversation(session, conversationId, agent.user_id)
        message = target_message(session, conversation, activityId, agent)
        if root_id and (message.reply_to_id or message.id) != root_id:
            raise HTTPException(403, "Activity belongs to another thread")
        emoji = "👀" if reactionType == "1f440_eyes" else REACTIONS.get(reactionType, reactionType)
        try:
            schemas.validate_emoji(emoji)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        await change_reaction(session, message, session.get(db.User, agent.user_id), emoji, removed)
        return Response(status_code=204)

    @router.put("/v3/conversations/{conversationId}/activities/{activityId}/reactions/{reactionType}", status_code=204)
    async def add_reaction(conversationId: str, activityId: str, reactionType: str,
                           agent=agent_dep, session=session_dep):
        return await reaction(conversationId, activityId, reactionType, agent, session, False)

    @router.delete("/v3/conversations/{conversationId}/activities/{activityId}/reactions/{reactionType}",
                   status_code=204)
    async def remove_reaction(conversationId: str, activityId: str, reactionType: str,
                              agent=agent_dep, session=session_dep):
        return await reaction(conversationId, activityId, reactionType, agent, session, True)

    @router.get("/v3/conversations/{conversationId}/members")
    async def members(conversationId: str, agent=agent_dep, session=session_dep):
        conversation, _ = resolve_conversation(session, conversationId, agent.user_id)
        return [account(session.get(db.User, uid), session) for uid in sorted(d.conversation_users(session, conversation))]

    @router.post("/v3/conversations")
    async def create_conversation(request: Request, agent=agent_dep, session=session_dep):
        body = await bounded_json(request)
        if not isinstance(body.get("bot", {}), dict):
            raise HTTPException(422, "bot must be a ChannelAccount object")
        if "id" in body.get("bot", {}) and not isinstance(body["bot"]["id"], str):
            raise HTTPException(422, "bot.id must be a string")
        initial = body.get("activity")
        if initial is not None:
            if not isinstance(initial, dict):
                raise HTTPException(422, "Initial activity must be an Activity object")
            initial = normalize_activity(initial)
            if initial.get("type") not in {"message", "typing"}:
                raise HTTPException(422, "Only message or typing is supported as an initial activity")
        unsupported = set(body) - {"bot", "members", "isGroup", "topicName", "tenantId", "channelData", "activity"}
        if unsupported:
            raise HTTPException(422, "Unsupported ConversationParameters fields")
        if body.get("tenantId", settings.tenant_id) != settings.tenant_id:
            raise HTTPException(403, "Conversation tenant does not match")
        if body.get("bot", {}).get("id", agent.user_id) not in {agent.user_id, f"28:{agent.client_id}"}:
            raise HTTPException(403, "Only the authenticated agent can create its conversation")
        channel_data = body.get("channelData", {})
        if not isinstance(channel_data, dict) or any(
            not isinstance(channel_data.get(key, {}), dict) for key in ("tenant", "team", "channel")
        ):
            raise HTTPException(422, "channelData must be an object")
        if any("id" in channel_data.get(key, {}) and not isinstance(channel_data[key]["id"], str)
               for key in ("tenant", "team", "channel")):
            raise HTTPException(422, "channelData IDs must be strings")
        if channel_data.get("tenant", {}).get("id", settings.tenant_id) != settings.tenant_id:
            raise HTTPException(403, "Conversation tenant does not match")
        if channel_data.get("channel") or channel_data.get("team"):
            channel_id = channel_data.get("channel", {}).get("id")
            existing = session.get(db.Channel, channel_id) if channel_id else None
            if existing is None:
                raise HTTPException(404, "Registered channel not found")
            conversation = d.require_conversation(session, existing.conversation_id, agent.user_id)
            if channel_data.get("team", {}).get("id", conversation.team_id) != conversation.team_id:
                raise HTTPException(403, "Channel does not belong to the specified team")
        else:
            members = body.get("members")
            if not isinstance(members, list) or not 1 <= len(members) <= 99 or any(
                not isinstance(u, dict) or not isinstance(u.get("id"), str) for u in members
            ):
                raise HTTPException(422, "Registered member ChannelAccounts are required")
            member_ids = {u["id"] for u in members} | {agent.user_id}
            users = [session.get(db.User, uid) for uid in sorted(member_ids)]
            if len(member_ids) < 2 or any(user is None for user in users):
                raise HTTPException(422, "Conversation requires registered directory members")
            if any(user.source == "agent" and user.id != agent.user_id for user in users):
                raise HTTPException(403, "Agents cannot install another agent proactively")
            group = body.get("isGroup", len(member_ids) > 2)
            if type(group) is not bool or (not group and len(member_ids) != 2):
                raise HTTPException(422, "Invalid isGroup for the requested members")
            kind = "groupChat" if group else "personal"
            conversation = None
            if kind == "personal":
                for existing in session.scalars(select(db.Conversation).where(db.Conversation.kind == kind)):
                    if d.conversation_users(session, existing) == member_ids:
                        conversation = existing
                        break
            if conversation is None:
                name = body.get("topicName") or ", ".join(user.display_name for user in users)
                if not isinstance(name, str) or not 1 <= len(name) <= 200:
                    raise HTTPException(422, "A bounded conversation topicName is required")
                conversation = db.Conversation(id=db.new_id(), kind=kind, name=name)
                session.add(conversation)
                session.flush()
                chat = db.Chat(name=name, conversation_id=conversation.id)
                session.add(chat)
                session.flush()
                for uid in sorted(member_ids):
                    session.add(db.ChatMember(chat_id=chat.id, user_id=uid))
                commit(session)
                hub.publish({"type": "chat.created", "chatId": chat.id, "conversationId": conversation.id}, member_ids)
        result = {"id": conversation.id, "serviceUrl": channel.service_url(agent)}
        if initial is not None:
            # Use the same send path, including file/author authorization.
            class InitialActivity:
                async def stream(self):
                    yield json.dumps(initial).encode()
            try:
                result["activityId"] = (await send(conversation.id, InitialActivity(), agent, session))["id"]
            except HTTPException as error:
                raise HTTPException(error.status_code, {
                    "code": "initial_activity_failed", "resourceId": conversation.id,
                    "resourceCommitted": True, "reason": error.detail,
                }) from None
        return result

    @router.post("/v3/conversations/{conversationId}/attachments")
    async def upload_attachment(conversationId: str, request: Request, agent=agent_dep, session=session_dep):
        conversation, _ = resolve_conversation(session, conversationId, agent.user_id)
        body = await bounded_json(request, ((settings.max_file_bytes + 2) // 3) * 4 + 64 * 1024)
        if set(body) - {"name", "type", "originalBase64", "thumbnailBase64"} or body.get("thumbnailBase64"):
            raise HTTPException(422, "Only an original attachment view is supported")
        encoded = body.get("originalBase64")
        if not isinstance(encoded, str):
            raise HTTPException(422, "originalBase64 is required")
        if len(encoded) > ((settings.max_file_bytes + 2) // 3) * 4:
            raise HTTPException(413, "File exceeds the 25 MB (26214400 bytes) limit")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(422, "Invalid originalBase64 attachment data") from None
        file_id, conversation_id, user_id = db.new_id(), conversation.id, agent.user_id
        session.rollback()
        try:
            metadata = store_bytes(storage, file_id, payload, settings.max_file_bytes,
                                   body.get("name"), body.get("type"))
            d.require_conversation(session, conversation_id, user_id)
            file = db.FileRecord(id=file_id, conversation_id=conversation_id, uploader_id=user_id, **metadata)
            session.add(file)
            commit(session)
            hub.publish({"type": "file.created", "conversationId": conversation_id, "fileId": file.id})
            return {"id": file.id}
        except BaseException:
            session.rollback()
            storage.discard(file_id)
            raise

    @router.get("/v3/attachments/{attachmentId}")
    async def attachment_info(attachmentId: str, agent=agent_dep, session=session_dep):
        file = d.require_file(session, attachmentId, agent.user_id)
        return {"name": file.name, "type": file.content_type, "views": [{"viewId": "original", "size": file.size}]}

    @router.get("/v3/attachments/{attachmentId}/views/{viewId}")
    async def attachment_view(attachmentId: str, viewId: str, agent=agent_dep, session=session_dep):
        file = d.require_file(session, attachmentId, agent.user_id)
        if viewId != "original":
            raise HTTPException(404, "Attachment view not supported")
        return d.file_response(storage, file)

    def card_objects(cards):
        objects = []
        def walk(value):
            if isinstance(value, dict):
                objects.append(value)
                for key, child in value.items():
                    if key not in {"data", "value"}:
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(cards)
        return objects

    async def suggested_action(session, message, conversation, agent, user, body):
        action = body.action
        suggested = message.activity.get("suggestedActions")
        if not suggested or action not in suggested["actions"]:
            raise HTTPException(403, "Action does not belong to the current stored suggested replies")
        if suggested.get("to") and user.id not in suggested["to"]:
            raise HTTPException(403, "Suggested reply is addressed to another participant")
        if body.inputs:
            raise HTTPException(422, "Native suggested replies do not accept card inputs")
        kind = action["type"]
        if kind == "Action.Submit":
            text = ""
        else:
            text = action.get("text", "") if kind == "messageBack" else action["value"]
        display = action.get("displayText", text) if kind == "messageBack" else text
        if kind in {"postBack", "Action.Submit"}:
            display = ""
        activity_id = db.new_id()
        root_id = (message.reply_to_id or message.id) if conversation.kind == "channel" else None
        activity = d.make_activity(session, settings, conversation, user, activity_id, text, "plain", root_id)
        activity["replyToId"] = message.id
        activity["recipient"] = {"id": f"28:{agent.client_id}"}
        if kind in {"messageBack", "Action.Submit"} and "value" in action:
            activity["value"] = copy.deepcopy(action["value"])
        activity["channelData"]["clientAction"] = {
            "type": kind, "sourceMessageId": message.id, "displayText": display, "hidden": not bool(display),
        }
        submitted = db.Message(id=activity_id, conversation_id=conversation.id, author_id=user.id,
                               reply_to_id=root_id, created_at=activity["timestamp"], activity=normalize_activity(activity))
        session.add(submitted)
        session.flush()
        d.revision(session, submitted, submitted.activity)
        updated = copy.deepcopy(message.activity)
        updated.pop("suggestedActions", None)
        d.update_message(session, message, updated)
        commit(session)
        source = d.message_json(session, message)
        visible = d.message_json(session, submitted) if d.message_visible(submitted) else None
        hub.publish({"type": "message.updated", "conversationId": conversation.id, "message": source})
        if visible:
            hub.publish({"type": "message.created", "conversationId": conversation.id, "message": visible})
        ids = channel.queue(session, conversation, user, activity, [agent])
        await channel.flush(session, ids, submitted.id)
        return {"activityId": activity_id, "invokeResponse": None, "message": source, "submittedMessage": visible}

    @router.post("/api/messages/{messageId}/actions")
    async def card_action(messageId: str, body: schemas.CardAction, user=user_dep, session=session_dep):
        """Invoke only an exact stored Adaptive Card or native suggested action with the user's identity."""
        message = d.require_message(session, messageId, user.id)
        if message.deleted:
            raise HTTPException(409, "Deleted messages cannot receive actions")
        agent = session.scalar(select(db.Agent).where(db.Agent.user_id == message.author_id))
        if agent is None:
            raise HTTPException(422, "Card actions require an agent-authored message")
        require_agent(session, agent.id, enabled=True)
        conversation = d.require_conversation(session, message.conversation_id, agent.user_id)
        if len(json.dumps(body.model_dump()).encode()) > 64 * 1024:
            raise HTTPException(413, "Card action data is too large")
        # The bridge's suggested-action spelling carries value, not Adaptive Card data.
        if body.action.get("type") in {"imBack", "postBack", "messageBack"} or (
            body.action.get("type") == "Action.Submit" and "value" in body.action
        ):
            return await suggested_action(session, message, conversation, agent, user, body)
        objects = card_objects([a.get("content") for a in message.activity.get("attachments", [])
                                if a.get("contentType") == ADAPTIVE_CARD])
        original_attachments = copy.deepcopy(message.activity.get("attachments", []))
        action = body.action
        if action.get("type") not in {"Action.Submit", "Action.Execute"}:
            raise HTTPException(422, "Only Action.Submit and Action.Execute are channel actions")
        if action not in objects:
            raise HTTPException(403, "Action does not belong to the current message card")
        allowed_inputs = {o["id"] for o in objects if str(o.get("type", "")).startswith("Input.") and "id" in o}
        if set(body.inputs) - allowed_inputs:
            raise HTTPException(422, "Unknown card input ID")
        data = action.get("data") or {}
        if not isinstance(data, dict):
            raise HTTPException(422, "Channel card actions require object data")
        data = {**data, **body.inputs}
        activity_id = db.new_id()
        root_id = message.reply_to_id or message.id if conversation.kind == "channel" else None
        activity = d.make_activity(session, settings, conversation, user, activity_id, "", "plain", root_id)
        activity["replyToId"] = message.id
        activity["recipient"] = {"id": f"28:{agent.client_id}"}
        invoke = action["type"] == "Action.Execute"
        if invoke:
            activity.update({"type": "invoke", "name": "adaptiveCard/action",
                             "value": {"action": {**action, "data": data}, "trigger": "manual"}})
        else:
            activity["value"] = data
            submitted = db.Message(id=activity_id, conversation_id=conversation.id, author_id=user.id,
                                   reply_to_id=root_id, created_at=activity["timestamp"],
                                   activity=normalize_activity(activity))
            session.add(submitted)
            session.flush()
            d.revision(session, submitted, activity)
            commit(session)
            hub.publish({"type": "message.created", "conversationId": conversation.id,
                         "message": d.message_json(session, submitted)})
        ids = channel.queue(session, conversation, user, activity, [agent])
        try:
            response = await channel.deliver(session, ids[0])
        except HTTPException as error:
            raise HTTPException(error.status_code, {
                "code": "agent_delivery_failed", "resourceId": message.id if invoke else activity_id,
                "resourceCommitted": True, "failures": [error.detail],
            }) from None
        message = d.require_message(session, messageId, user.id)
        d.require_conversation(session, message.conversation_id, agent.user_id)
        if invoke and response["type"] == ADAPTIVE_CARD:
            if message.deleted:
                raise HTTPException(409, "Message was deleted while the action ran")
            if message.activity.get("attachments", []) != original_attachments:
                raise HTTPException(409, "Card changed while the action ran; refresh before submitting again")
            value = copy.deepcopy(message.activity)
            for attachment in value["attachments"]:
                if attachment.get("contentType") == ADAPTIVE_CARD and action in card_objects(attachment["content"]):
                    attachment["content"] = response["value"]
                    break
            d.update_message(session, message, value)
            commit(session)
            hub.publish({"type": "message.updated", "conversationId": conversation.id,
                         "message": d.message_json(session, message)})
        return {"activityId": activity_id, "invokeResponse": response, "message": d.message_json(session, message)}

    app.include_router(router)
    if settings.auth_mode == "demo":
        demo_routes = APIRouter()
        demo_routes.routes = [route for route in router.routes if route.path.startswith("/v3/")]
        app.include_router(demo_routes, prefix="/demo/{demoKey}")
