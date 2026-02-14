"""Base trigger engine interface."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_review.models import ModelConfig


@dataclass
class TriggerResult:
    success: bool
    output: str = ""
    error: str = ""
    client_session_id: str = ""


class TriggerEngine(abc.ABC):
    """Abstract base class for client trigger engines."""

    @abc.abstractmethod
    async def create_session(self, model_id: str) -> str:
        """Create a client session for the given model. Returns client session ID."""
        ...

    @abc.abstractmethod
    async def send_prompt(
        self, client_session_id: str, model_id: str, prompt: str,
        *, model_config: ModelConfig | None = None,
    ) -> TriggerResult:
        """Send a prompt to the client and wait for completion."""
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        """Clean up resources."""
        ...
