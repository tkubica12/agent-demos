import uuid

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def new_id():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("source", "subject"),
        UniqueConstraint("source", "upn"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String)
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String)
    upn: Mapped[str] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String)
    roles: Mapped[list] = mapped_column(JSON, default=list)


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class TeamMember(Base):
    __tablename__ = "team_members"
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), nullable=True)


class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    name: Mapped[str] = mapped_column(String)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), unique=True)


class Chat(Base):
    __tablename__ = "chats"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), unique=True)


class ChatMember(Base):
    __tablename__ = "chat_members"
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)


class Message(Base):
    __tablename__ = "messages"
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String, unique=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reply_to_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str | None] = mapped_column(String, nullable=True)
    deleted: Mapped[bool] = mapped_column(default=False)
    activity: Mapped[dict] = mapped_column(JSON)


class ActivityRevision(Base):
    __tablename__ = "activity_revisions"
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    activity: Mapped[dict] = mapped_column(JSON)


class FileRecord(Base):
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    uploader_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    name: Mapped[str] = mapped_column(String)
    size: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String)


class Reaction(Base):
    __tablename__ = "reactions"
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    emoji: Mapped[str] = mapped_column(String, primary_key=True)


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    client_id: Mapped[str] = mapped_column(String, unique=True)
    endpoint_url: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(default=True)
    registration_name: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    demo_key: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)


class AgentDelivery(Base):
    __tablename__ = "agent_deliveries"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    activity: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String)


class AgentFileCapability(Base):
    __tablename__ = "agent_file_capabilities"
    digest: Mapped[str] = mapped_column(String, primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    expires_at: Mapped[int] = mapped_column(Integer)


def database(url: str):
    if not url.startswith("sqlite:"):
        raise ValueError("G2 supports SQLite only")
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)
