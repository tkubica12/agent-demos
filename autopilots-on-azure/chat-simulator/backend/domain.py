import copy
import html
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from microsoft_agents.activity import Activity
from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from . import db
from .auth import Identity
from .config import Settings


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def user_json(user: db.User):
    value = {"id": user.id, "displayName": user.display_name, "upn": user.upn, "roles": user.roles}
    if user.source == "agent":
        value.update({"isAgent": True, "agentId": user.subject})
    return value


def register(session: Session, identity: Identity):
    user = session.scalar(select(db.User).where(
        db.User.source == identity.source, db.User.subject == identity.subject,
        db.User.tenant_id == identity.tenant_id,
    ))
    if user is None:
        candidate = session.scalar(select(db.User).where(
            db.User.source == identity.source, db.User.upn == identity.upn,
            db.User.tenant_id == identity.tenant_id,
        ))
        if candidate and candidate.subject is not None:
            raise HTTPException(403, "User principal already bound to another immutable identity")
        user = candidate or db.User(source=identity.source, tenant_id=identity.tenant_id)
        user.subject = identity.subject
    collision = session.scalar(select(db.User).where(
        db.User.source == identity.source, db.User.upn == identity.upn, db.User.id != user.id,
    ))
    if collision and collision is not user:
        raise HTTPException(403, "User principal already registered")
    user.upn = identity.upn
    user.display_name = identity.display_name
    user.roles = list(identity.roles)
    session.add(user)
    session.commit()
    return user


def require_team(session: Session, team_id: str, user_id: str, owner=False):
    team = session.get(db.Team, team_id)
    if team is None:
        raise HTTPException(404, "Team not found")
    if session.get(db.TeamMember, (team_id, user_id)) is None:
        raise HTTPException(403, "Team membership required")
    if owner and team.owner_id != user_id:
        raise HTTPException(403, "Team owner required")
    return team


def conversation_users(session: Session, conversation: db.Conversation):
    if conversation.team_id:
        return set(session.scalars(select(db.TeamMember.user_id).where(
            db.TeamMember.team_id == conversation.team_id,
        )))
    chat_id = session.scalar(select(db.Chat.id).where(db.Chat.conversation_id == conversation.id))
    return set(session.scalars(select(db.ChatMember.user_id).where(db.ChatMember.chat_id == chat_id)))


def can_read(session: Session, conversation_id: str, user_id: str):
    conversation = session.get(db.Conversation, conversation_id)
    return conversation is not None and user_id in conversation_users(session, conversation)


def require_conversation(session: Session, conversation_id: str, user_id: str):
    conversation = session.get(db.Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(404, "Conversation not found")
    if user_id not in conversation_users(session, conversation):
        raise HTTPException(403, "Conversation membership required")
    return conversation


def require_message(session: Session, message_id: str, user_id: str, own=False):
    message = session.scalar(select(db.Message).where(db.Message.id == message_id))
    if message is None:
        raise HTTPException(404, "Message not found")
    require_conversation(session, message.conversation_id, user_id)
    if own and message.author_id != user_id:
        raise HTTPException(403, "Only the author may change this message")
    return message


def require_file(session: Session, file_id: str, user_id: str):
    file = session.get(db.FileRecord, file_id)
    if file is None:
        raise HTTPException(404, "File not found")
    require_conversation(session, file.conversation_id, user_id)
    return file


def attachable_files(session, file_ids, conversation_id, user_id, message_id=None):
    if len(set(file_ids)) != len(file_ids):
        raise HTTPException(422, "Duplicate attachment IDs")
    files = []
    for file_id in file_ids:
        file = require_file(session, file_id, user_id)
        if file.conversation_id != conversation_id or file.uploader_id != user_id:
            raise HTTPException(403, "Only your uploads in this conversation may be attached")
        if file.message_id is not None and file.message_id != message_id:
            raise HTTPException(409, "File is already attached to a message")
        files.append(file)
    return files


def file_response(storage, file):
    if not storage.exists(file.id):
        raise HTTPException(404, "Stored file content not found")
    return StreamingResponse(
        storage.read_chunks(file.id), media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename=\"download\"; filename*=UTF-8''{quote(file.name, safe='')}",
                 "Content-Length": str(file.size), "X-Content-Type-Options": "nosniff"},
    )


def update_message(session, message, activity):
    if message.deleted:
        raise HTTPException(409, "Deleted messages cannot be edited")
    message.activity = validated_activity(activity)
    message.updated_at = now()
    revision(session, message, change_activity(message, "messageUpdate"))


def delete_message(session, message):
    if not message.deleted:
        message.activity = change_activity(message, "messageDelete")
        message.deleted = True
        message.updated_at = now()
        session.execute(delete(db.Reaction).where(db.Reaction.message_id == message.id))
        revision(session, message, message.activity)


def channel_json(channel: db.Channel):
    return {
        "id": channel.id, "name": channel.name,
        "conversationId": channel.conversation_id, "teamId": channel.team_id,
    }


def team_json(session: Session, team: db.Team):
    return {
        "id": team.id, "name": team.name, "ownerId": team.owner_id,
        "channels": [channel_json(c) for c in session.scalars(
            select(db.Channel).where(db.Channel.team_id == team.id).order_by(
                case((db.Channel.name == "General", 0), else_=1), db.Channel.name, db.Channel.id,
            )
        )],
    }


def chat_json(session: Session, chat: db.Chat):
    conversation = session.get(db.Conversation, chat.conversation_id)
    return {
        "id": chat.id, "name": chat.name, "conversationId": chat.conversation_id,
        "memberIds": sorted(conversation_users(session, conversation)),
        "conversationType": conversation.kind,
    }


def file_json(file: db.FileRecord):
    return {
        "id": file.id, "name": file.name, "size": file.size, "contentType": file.content_type,
        "conversationId": file.conversation_id, "sha256": file.sha256,
        "downloadUrl": f"/api/files/{file.id}/content",
    }


def message_visible(message):
    data = message.activity.get("channelData", {})
    return message.activity.get("type") != "typing" and not data.get(
        "typingPlaceholder", False
    ) and not data.get("clientAction", {}).get("hidden", False)


def message_json(session: Session, message: db.Message):
    # Keep the journal watermark and all message fields in one SQLite read snapshot.
    with session.begin_nested():
        session.refresh(message)
        return _message_json(session, message)


def _message_json(session: Session, message: db.Message):
    activity = message.activity
    entities = activity.get("entities", [])
    grouped = {}
    for reaction in session.scalars(select(db.Reaction).where(
        db.Reaction.message_id == message.id,
    ).order_by(db.Reaction.emoji, db.Reaction.user_id)):
        grouped.setdefault(reaction.emoji, []).append(session.get(db.User, reaction.user_id))
    value = {
        "id": message.id, "conversationId": message.conversation_id,
        "replyToId": message.reply_to_id, "text": activity.get("text", ""),
        "textFormat": activity.get("textFormat", "markdown"),
        "author": user_json(session.get(db.User, message.author_id)),
        "createdAt": message.created_at, "updatedAt": message.updated_at, "deleted": message.deleted,
        "snapshotRevision": session.scalar(select(func.coalesce(func.max(db.ActivityRevision.sequence), 0)).where(
            db.ActivityRevision.message_id == message.id,
        )),
        "mentions": [
            {"userId": e["mentioned"]["id"], "displayName": e["mentioned"]["name"]}
            for e in entities if e.get("type") == "mention"
        ],
        "attachments": [] if message.deleted else [
            file_json(f) for f in session.scalars(select(db.FileRecord).where(
                db.FileRecord.message_id == message.id,
            ).order_by(db.FileRecord.id))
        ],
        "reactions": [
            {"emoji": emoji, "count": len(users), "userIds": [u.id for u in users],
             "users": [user_json(u) for u in users]}
            for emoji, users in grouped.items()
        ],
    }
    if session.get(db.User, message.author_id).source == "agent":
        value["activityAttachments"] = [] if message.deleted else activity.get("attachments", [])
        value["suggestedActions"] = None if message.deleted else activity.get("suggestedActions")
    client_action = activity.get("channelData", {}).get("clientAction")
    if client_action:
        value["text"] = "" if message.deleted else client_action["displayText"]
        value["isHidden"] = client_action["hidden"]
    if activity.get("type") == "typing" or activity.get("channelData", {}).get("typingPlaceholder", False):
        value["text"] = ""
        value["isHidden"] = True
    return value


def validated_activity(value: dict):
    return Activity.model_validate(value).model_dump(mode="json", by_alias=True, exclude_none=True)


def mention_content(session, conversation, text, mentions):
    members = conversation_users(session, conversation)
    entities = []
    seen = set()
    for mention in mentions:
        if mention.userId in seen:
            raise HTTPException(422, "Duplicate mention")
        seen.add(mention.userId)
        if mention.userId not in members:
            raise HTTPException(422, "Mentioned user is not a conversation member")
        user = session.get(db.User, mention.userId)
        tag = f"<at>{html.escape(user.display_name)}</at>"
        supplied = f"<at>{mention.displayName}</at>"
        if supplied in text:
            text = text.replace(supplied, tag)
        elif tag not in text:
            text += f" {tag}"
        entities.append({"type": "mention", "mentioned": {"id": user.id, "name": user.display_name}, "text": tag})
    return text, entities


def make_activity(session, settings: Settings, conversation, author, message_id, text, text_format,
                  reply_to_id=None, entities=None, attachments=None):
    conversation_id = conversation.id
    if reply_to_id and conversation.kind == "channel":
        conversation_id += f";messageid={reply_to_id}"
    value = {
        "type": "message", "id": message_id, "timestamp": now(),
        "channelId": settings.channel_id,
        "from": {"id": author.id, "name": author.display_name, "aadObjectId": author.subject},
        "conversation": {
            "id": conversation_id, "conversationType": conversation.kind,
            "name": conversation.name, "tenantId": settings.tenant_id,
        },
        "text": text, "textFormat": text_format, "entities": entities or [],
        "attachments": attachments or [],
        "channelData": {"tenant": {"id": settings.tenant_id}},
    }
    if reply_to_id:
        value["replyToId"] = reply_to_id
    if conversation.team_id:
        channel = session.scalar(select(db.Channel).where(db.Channel.conversation_id == conversation.id))
        value["channelData"].update({
            "team": {"id": conversation.team_id},
            "channel": {"id": channel.id},
        })
    return validated_activity(value)


def revision(session, message, activity):
    session.add(db.ActivityRevision(
        message_id=message.id, conversation_id=message.conversation_id,
        activity=validated_activity(activity),
    ))


def change_activity(message, kind, author=None, emoji=None, remove=False):
    value = copy.deepcopy(message.activity)
    value["type"] = kind
    value["timestamp"] = now()
    if author:
        value["from"] = {"id": author.id, "name": author.display_name}
    if emoji:
        value.pop("text", None)
        value.pop("attachments", None)
        value.pop("entities", None)
        value["replyToId"] = message.id
        value["reactionsRemoved" if remove else "reactionsAdded"] = [{"type": emoji}]
    if kind == "messageDelete":
        value["text"] = ""
        value["attachments"] = []
        value["entities"] = []
    return validated_activity(value)
