"""OpenCode Serve trigger engine — HTTP API based."""

from __future__ import annotations

import httpx

from ai_review.trigger.base import TriggerEngine, TriggerResult


class OpenCodeTrigger(TriggerEngine):
    """Trigger OpenCode Serve via HTTP API."""

    def __init__(
        self,
        serve_url: str = "http://localhost:4096",
        timeout: float = 300.0,
    ) -> None:
        self.serve_url = serve_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)
        self._sessions: dict[str, str] = {}  # model_id -> opencode session_id

    async def create_session(self, model_id: str) -> str:
        """Create an OpenCode session via POST /session."""
        resp = await self._client.post(
            f"{self.serve_url}/session",
            json={"title": f"ai-review-{model_id}"},
        )
        resp.raise_for_status()
        session_id = resp.json()["id"]
        self._sessions[model_id] = session_id
        return session_id

    async def send_prompt(
        self,
        client_session_id: str,
        model_id: str,
        prompt: str,
        *,
        provider: str = "",
        model_spec: str = "",
        agent: str = "",
        async_mode: bool = False,
    ) -> TriggerResult:
        """Send a prompt to OpenCode Serve session."""
        body: dict = {
            "parts": [{"type": "text", "text": prompt}],
        }

        if provider and model_spec:
            body["model"] = {
                "providerID": provider,
                "modelID": model_spec,
            }

        if agent:
            body["agent"] = agent

        endpoint = "prompt_async" if async_mode else "message"

        try:
            resp = await self._client.post(
                f"{self.serve_url}/session/{client_session_id}/{endpoint}",
                json=body,
            )
            resp.raise_for_status()

            return TriggerResult(
                success=True,
                output=resp.text,
                client_session_id=client_session_id,
            )
        except httpx.HTTPStatusError as e:
            return TriggerResult(
                success=False,
                error=f"HTTP {e.response.status_code}: {e.response.text}",
                client_session_id=client_session_id,
            )
        except httpx.ConnectError:
            return TriggerResult(
                success=False,
                error=f"Cannot connect to OpenCode Serve at {self.serve_url}. Is it running?",
                client_session_id=client_session_id,
            )
        except Exception as e:
            return TriggerResult(
                success=False,
                error=str(e),
                client_session_id=client_session_id,
            )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
        self._sessions.clear()
