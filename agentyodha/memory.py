"""Simple JSON-file conversation persistence.

The Claude API is stateless, so multi-turn agents must resend history each
request. ConversationStore saves that history to disk so a session can be
resumed later (`agentyodha chat assistant --session my-task`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ConversationStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
        return self.directory / f"{safe}.json"

    def load(self, session_id: str) -> list[dict[str, Any]]:
        path = self._path(session_id)
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        self._path(session_id).write_text(
            json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def delete(self, session_id: str) -> None:
        self._path(session_id).unlink(missing_ok=True)

    def sessions(self) -> list[str]:
        return sorted(p.stem for p in self.directory.glob("*.json"))
