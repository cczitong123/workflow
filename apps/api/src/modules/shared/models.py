from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Step:
    condition: str
    actions: list[str]


@dataclass
class FileChange:
    path: str
    reason: str


@dataclass
class ParsedWhatToDo:
    steps: list[Step] = field(default_factory=list)
    files_to_change: list[FileChange] = field(default_factory=list)
    raw_text: str = ""
    parse_notes: list[str] = field(default_factory=list)


@dataclass
class EpicSource:
    id: str
    title: str
    description: str
    what_to_do: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EpicRecord:
    source: EpicSource
    parsed_what_to_do: ParsedWhatToDo | None = None


@dataclass
class RetrievalIntent:
    summary: str
    technical_intent: str
    keywords: list[str]
    suspected_areas: list[str]
    query: str = ""


@dataclass
class EvidenceItem:
    id: str
    path: str
    chunk_type: str
    symbol: str | None
    snippet: str
    score: float
    why_relevant: str
    suggested_change: str
    location_hint: str | None = None


@dataclass
class OpenQuestion:
    id: str
    question: str
    reason: str
    status: str = "open"
    answer: str | None = None


@dataclass
class WhatToDoDraft:
    version: int
    steps: list[Step]
    files_to_change: list[FileChange]
    open_questions: list[OpenQuestion]
    raw_text: str
    summary: str = ""


@dataclass
class DraftVersionRecord:
    id: int
    version_number: int
    source_type: str
    retrieval_version_id: int | None
    summary: str
    raw_text: str
    created_at: str


@dataclass
class RetrievalVersionRecord:
    id: int
    version_number: int
    trigger_source: str
    summary: str
    technical_intent: str
    query: str
    keywords: list[str]
    suspected_areas: list[str]
    created_at: str


@dataclass
class Session:
    id: str
    epic_id: str
    input_title: str
    input_description: str
    source_type: str = "local"
    retrieval_intent: RetrievalIntent | None = None
    evidence: list[EvidenceItem] = field(default_factory=list)
    draft: WhatToDoDraft | None = None
    draft_history: list[WhatToDoDraft] = field(default_factory=list)
    reference_samples: list[ParsedWhatToDo] = field(default_factory=list)
    status: str = "idle"
    current_phase: str = "idle"
    current_message: str = ""
    current_retrieval_version_id: int | None = None
    current_draft_version_id: int | None = None


def to_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    return value
