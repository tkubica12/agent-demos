import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Named(Input):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Name must not be blank")
        return value


class Preregister(Input):
    upn: str = Field(min_length=3, max_length=320)
    displayName: str = Field(min_length=1, max_length=200)

    @field_validator("upn")
    @classmethod
    def principal(cls, value):
        if not re.fullmatch(r"[^\s@]+@[^\s@]+", value):
            raise ValueError("A valid UPN is required")
        return value.lower()


class Member(Input):
    userId: str = Field(min_length=1, max_length=100)


class ChatCreate(Input):
    name: str | None = Field(default=None, max_length=200)
    memberIds: list[str] = Field(min_length=1, max_length=100)


class Mention(Input):
    userId: str = Field(min_length=1, max_length=100)
    displayName: str = Field(min_length=1, max_length=200)


class MessageEdit(Input):
    text: str = Field(max_length=28000)
    textFormat: Literal["markdown", "plain"] = "markdown"
    mentions: list[Mention] = Field(default_factory=list, max_length=100)


class MessageCreate(MessageEdit):
    replyToId: str | None = Field(default=None, max_length=100)
    attachmentIds: list[str] = Field(default_factory=list, max_length=20)


def validate_emoji(value: str):
    if not 1 <= len(value) <= 32 or len(value.encode("utf-8")) > 128:
        raise ValueError("Use a single emoji or bounded emoji sequence")
    subdivision_flag = value.startswith("\U0001f3f4") and value.endswith("\U000e007f")
    if any(
        unicodedata.category(c).startswith("C") and c != "\u200d"
        and not (subdivision_flag and 0xE0020 <= ord(c) <= 0xE007F)
        for c in value
    ):
        raise ValueError("Invalid emoji characters")
    if not any(
        0x1F000 <= ord(c) <= 0x1FAFF or 0x2300 <= ord(c) <= 0x27FF or 0x2194 <= ord(c) <= 0x2199
        or ord(c) in {
            0xA9, 0xAE, 0x203C, 0x2049, 0x20E3, 0x2122, 0x2139, 0x21A9, 0x21AA,
            0x2934, 0x2935, 0x2B05, 0x2B06, 0x2B07, 0x2B1B, 0x2B1C, 0x2B50, 0x2B55,
            0x3030, 0x303D, 0x3297, 0x3299,
        }
        for c in value
    ):
        raise ValueError("Use an emoji, not a text label")
    if any(c.isalpha() and ord(c) < 0x2300 and ord(c) not in {0xA9, 0xAE, 0x2122, 0x2139} for c in value):
        raise ValueError("Use an emoji, not a text label")
    return value


class ReactionCreate(Input):
    emoji: str

    @field_validator("emoji")
    @classmethod
    def emoji_value(cls, value):
        return validate_emoji(value)


class AgentCreate(Named):
    clientId: str
    endpointUrl: str = Field(min_length=1, max_length=2048)
    enabled: bool = True


class AgentEdit(Input):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    endpointUrl: str | None = Field(default=None, min_length=1, max_length=2048)
    enabled: bool | None = None


class CardAction(Input):
    action: dict
    inputs: dict = Field(default_factory=dict)
