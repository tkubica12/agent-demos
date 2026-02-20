# Copyright (c) Microsoft. All rights reserved.

"""
Compatibility shim for agent_framework API changes.

The agent_framework package renamed several classes:
  ChatAgent -> Agent
  ChatMessage -> Message
  ChatMessageStoreProtocol -> BaseHistoryProvider

This module patches the old names back so that downstream packages
(e.g. microsoft_agents_a365) that still import the old names continue to work.

Import this module BEFORE importing any microsoft_agents_a365 packages.
"""

import agent_framework

if not hasattr(agent_framework, "ChatAgent"):
    _OriginalAgent = agent_framework.Agent

    class _ChatAgentCompat(_OriginalAgent):
        """Compat wrapper: translates old 'chat_client' kwarg to 'client'."""
        def __init__(self, *args, **kwargs):
            if "chat_client" in kwargs and "client" not in kwargs:
                kwargs["client"] = kwargs.pop("chat_client")
            super().__init__(*args, **kwargs)

    agent_framework.ChatAgent = _ChatAgentCompat

if not hasattr(agent_framework, "ChatMessage"):
    agent_framework.ChatMessage = agent_framework.Message

if not hasattr(agent_framework, "ChatMessageStoreProtocol"):
    agent_framework.ChatMessageStoreProtocol = agent_framework.BaseHistoryProvider
