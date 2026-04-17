from __future__ import annotations

from config import LlmApiConfig
from modules.shared.models import (
    EvidenceItem,
    FileChange,
    OpenQuestion,
    ParsedWhatToDo,
    Step,
    WhatToDoDraft,
)


def build_retrieval_intent(description: str, config: LlmApiConfig) -> tuple[str, str, list[str], list[str]]:
    mode = config.mode.lower().strip()
    if mode == "mock":
        return _build_mock_intent(description)
    if mode == "local":
        return _build_local_intent(description, config)
    if mode == "remote":
        return _build_remote_intent(description, config)
    raise NotImplementedError(
        f"LLM mode '{config.mode}' is not implemented yet. "
        "Update modules/integrations/llm_adapter.py."
    )


def generate_draft(
    description: str,
    evidence: list[EvidenceItem],
    references: list[ParsedWhatToDo],
    config: LlmApiConfig,
) -> WhatToDoDraft:
    mode = config.mode.lower().strip()
    if mode == "mock":
        return _generate_mock_draft(description, evidence, references)
    if mode == "local":
        return _generate_local_draft(description, evidence, references, config)
    if mode == "remote":
        return _generate_remote_draft(description, evidence, references, config)
    raise NotImplementedError(
        f"LLM mode '{config.mode}' is not implemented yet. "
        "Update modules/integrations/llm_adapter.py."
    )


def refine_draft(
    current: WhatToDoDraft,
    user_message: str,
    answered_questions: list[dict[str, str]],
    config: LlmApiConfig,
) -> WhatToDoDraft:
    mode = config.mode.lower().strip()
    if mode == "mock":
        return _refine_mock_draft(current, user_message, answered_questions)
    if mode == "local":
        return _refine_local_draft(current, user_message, answered_questions, config)
    if mode == "remote":
        return _refine_remote_draft(current, user_message, answered_questions, config)
    raise NotImplementedError(
        f"LLM mode '{config.mode}' is not implemented yet. "
        "Update modules/integrations/llm_adapter.py."
    )


def _build_mock_intent(description: str) -> tuple[str, str, list[str], list[str]]:
    sentences = [line.strip() for line in description.splitlines() if line.strip()]
    summary = sentences[0] if sentences else "No summary available."
    keywords = []
    for token in description.replace(",", " ").replace(".", " ").split():
        normalized = token.strip().lower()
        if len(normalized) > 5 and normalized not in keywords:
            keywords.append(normalized)
        if len(keywords) == 6:
            break
    suspected_areas = ["request handling", "validation layer", "integration tests"]
    technical_intent = "Infer likely code touch points and describe the required behavior change."
    return summary, technical_intent, keywords, suspected_areas


def _generate_mock_draft(
    description: str,
    evidence: list[EvidenceItem],
    references: list[ParsedWhatToDo],
) -> WhatToDoDraft:
    steps = [
        Step(
            condition="If the new epic condition is met",
            actions=[
                "Update the request handling path so the new behavior is applied only for the target scenario.",
                "Propagate the decision through configuration or guard logic so downstream behavior stays explicit.",
            ],
        ),
        Step(
            condition="Else (all other cases)",
            actions=[
                "Keep the current behavior unchanged and preserve existing fallback handling.",
            ],
        ),
    ]
    files_to_change = [
        FileChange(path=item.path, reason=item.suggested_change) for item in evidence
    ]
    open_questions = [
        OpenQuestion(
            id="oq-1",
            question="What exact business condition should trigger the new behavior?",
            reason="The epic description may not fully specify the decision boundary.",
        ),
        OpenQuestion(
            id="oq-2",
            question="Are there compatibility constraints with the current fallback behavior?",
            reason="The legacy path should stay stable unless the epic explicitly changes it.",
        ),
    ]
    reference_note = ""
    if references:
        reference_note = " Draft format is guided by historical What-to-Do samples."
    raw_text = _render_draft(steps, files_to_change)
    summary = f"Generated from description with {len(evidence)} evidence items.{reference_note}"
    return WhatToDoDraft(
        version=1,
        steps=steps,
        files_to_change=files_to_change,
        open_questions=open_questions,
        raw_text=raw_text,
        summary=summary,
    )


def _build_local_intent(description: str, config: LlmApiConfig) -> tuple[str, str, list[str], list[str]]:
    raise NotImplementedError(
        "Local LLM mode is selected, but local summarization/inference is not implemented yet. "
        "Use config.local_model_path and config.device in this file."
    )


def _build_remote_intent(description: str, config: LlmApiConfig) -> tuple[str, str, list[str], list[str]]:
    raise NotImplementedError(
        "Remote LLM mode is selected, but the remote HTTP client is not implemented yet. "
        "Use config.endpoint, config.model, and auth fields in this file."
    )


def _generate_local_draft(
    description: str,
    evidence: list[EvidenceItem],
    references: list[ParsedWhatToDo],
    config: LlmApiConfig,
) -> WhatToDoDraft:
    raise NotImplementedError(
        "Local LLM mode is selected, but local draft generation is not implemented yet."
    )


def _generate_remote_draft(
    description: str,
    evidence: list[EvidenceItem],
    references: list[ParsedWhatToDo],
    config: LlmApiConfig,
) -> WhatToDoDraft:
    raise NotImplementedError(
        "Remote LLM mode is selected, but remote draft generation is not implemented yet."
    )


def _refine_mock_draft(
    current: WhatToDoDraft,
    user_message: str,
    answered_questions: list[dict[str, str]],
) -> WhatToDoDraft:
    steps = list(current.steps)
    if user_message.strip():
        steps = steps + [
            Step(
                condition="Additional note from reviewer",
                actions=[user_message.strip()],
            )
        ]

    answered_lookup = {item["id"]: item["answer"] for item in answered_questions if item.get("id")}
    open_questions = []
    for question in current.open_questions:
        if question.id in answered_lookup:
            question.answer = answered_lookup[question.id]
            question.status = "answered"
        open_questions.append(question)

    raw_text = _render_draft(steps, current.files_to_change)
    return WhatToDoDraft(
        version=current.version + 1,
        steps=steps,
        files_to_change=current.files_to_change,
        open_questions=open_questions,
        raw_text=raw_text,
        summary=current.summary,
    )


def _refine_local_draft(
    current: WhatToDoDraft,
    user_message: str,
    answered_questions: list[dict[str, str]],
    config: LlmApiConfig,
) -> WhatToDoDraft:
    raise NotImplementedError(
        "Local LLM mode is selected, but local refine is not implemented yet."
    )


def _refine_remote_draft(
    current: WhatToDoDraft,
    user_message: str,
    answered_questions: list[dict[str, str]],
    config: LlmApiConfig,
) -> WhatToDoDraft:
    raise NotImplementedError(
        "Remote LLM mode is selected, but remote refine is not implemented yet."
    )


def _render_draft(steps: list[Step], files_to_change: list[FileChange]) -> str:
    lines = ["**What to do:**", ""]
    for index, step in enumerate(steps, start=1):
        lines.append(f"{index}. **{step.condition}:**")
        for action in step.actions:
            lines.append(f"   - {action}")
    lines.extend(["", "**Files to change:**", ""])
    for item in files_to_change:
        lines.append(f"- `{item.path}` — {item.reason}")
    return "\n".join(lines)
