from __future__ import annotations

import itertools

from modules.shared.models import Session


class SessionStore:
    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._sessions: dict[str, Session] = {}

    def create(self, epic_id: str, title: str, description: str) -> Session:
        session_id = f"sess-{next(self._counter):03d}"
        session = Session(
            id=session_id,
            epic_id=epic_id,
            input_title=title,
            input_description=description,
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Session:
        return self._sessions[session_id]
