from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from modules.epics.what_to_do_parser import parse_what_to_do
from modules.shared.models import EpicRecord, EpicSource


class EpicRepository:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def list_epics(self) -> list[EpicRecord]:
        return [self.get_epic(path.stem) for path in sorted(self.data_dir.glob("*.json"))]

    def get_epic(self, epic_id: str) -> EpicRecord:
        path = self.data_dir / f"{epic_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return normalize_epic_payload(payload)


def normalize_epic_payload(payload: dict[str, Any]) -> EpicRecord:
    epic_id_value = _first_present(payload, ["id", "key", "issueKey"])
    epic_title = _first_present(payload, ["title", "summary", "name", "fields.summary"]) or str(epic_id_value)
    description = _first_present(payload, ["description", "fields.description"]) or ""
    what_to_do = _first_present(
        payload,
        ["whatToDo", "What-to-do", "what_to_do", "fields.whatToDo", "fields.What-to-do"],
    ) or ""
    source = EpicSource(
        id=str(epic_id_value),
        title=str(epic_title),
        description=str(description),
        what_to_do=str(what_to_do),
        metadata=_extract_metadata(payload),
    )
    parsed = parse_what_to_do(source.what_to_do)
    return EpicRecord(source=source, parsed_what_to_do=parsed)


def _first_present(payload: dict[str, Any], paths: list[str]) -> Any:
    for path in paths:
        current: Any = payload
        found = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current is not None:
            return current
    return None


def _extract_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    ignored_keys = {"id", "key", "issueKey", "title", "summary", "name", "description", "whatToDo", "What-to-do", "what_to_do"}
    return {key: value for key, value in payload.items() if key not in ignored_keys}
