from __future__ import annotations

import json
import re

from config import LlmApiConfig
from prompt_loader import load_fewshot_examples, load_prompt_text
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
    prompt = load_prompt_text("retrieval_intent_system").format(
        description=description,
        fewshot=json.dumps(load_fewshot_examples("retrieval_intent_fewshot"), ensure_ascii=False, indent=2),
    )
    payload = _call_remote_chat(prompt, config)
    data = _parse_json_response(payload)
    return (
        str(data.get("summary", "")),
        str(data.get("technical_intent", "")),
        [str(item) for item in data.get("keywords", [])],
        [str(item) for item in data.get("suspected_areas", [])],
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
    prompt = load_prompt_text("draft_generation_system").format(
        description=description,
        evidence=json.dumps([_evidence_to_prompt(item) for item in evidence], ensure_ascii=False, indent=2),
        references=json.dumps([_reference_to_prompt(item) for item in references], ensure_ascii=False, indent=2),
        fewshot=json.dumps(load_fewshot_examples("draft_generation_fewshot"), ensure_ascii=False, indent=2),
    )
    payload = _call_remote_chat(prompt, config)
    data = _parse_json_response(payload)
    return _draft_from_json(data, version=1)


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
    prompt = load_prompt_text("refine_open_questions_system").format(
        current_draft=json.dumps(_draft_to_prompt(current), ensure_ascii=False, indent=2),
        user_message=user_message,
        answered_questions=json.dumps(answered_questions, ensure_ascii=False, indent=2),
        fewshot=json.dumps(load_fewshot_examples("refine_open_questions_fewshot"), ensure_ascii=False, indent=2),
    )
    payload = _call_remote_chat(prompt, config)
    data = _parse_json_response(payload)
    return _draft_from_json(data, version=current.version + 1)


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


def _call_remote_chat(prompt: str, config: LlmApiConfig) -> str:
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "Remote LLM mode requires httpx. Install dependencies with `python -m pip install -r requirements.txt`."
        ) from exc

    access_token = config.access_token or _get_access_token(config, httpx)
    url = f"{config.endpoint.rstrip('/')}{config.api_path}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "x-apikey": config.api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "top_p": config.top_p,
        "presence_penalty": config.presence_penalty,
        "frequency_penalty": config.frequency_penalty,
    }
    client_kwargs = {"timeout": config.timeout_seconds}
    if config.cert_path:
        client_kwargs["verify"] = config.cert_path

    with httpx.Client(**client_kwargs) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected LLM response shape: {body}") from exc


def _get_access_token(config: LlmApiConfig, httpx_module) -> str:
    if not config.client_id or not config.client_secret or not config.auth_url:
        raise RuntimeError(
            "Remote LLM mode requires either BMWCODE_LLM_ACCESS_TOKEN or the full M2M config: "
            "BMWCODE_LLM_AUTH_URL, BMWCODE_LLM_CLIENT_ID, BMWCODE_LLM_CLIENT_SECRET."
        )
    data = {
        "grant_type": "client_credentials",
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "scope": "machine2machine",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    client_kwargs = {"timeout": min(config.timeout_seconds, 30)}
    if config.cert_path:
        client_kwargs["verify"] = config.cert_path

    with httpx_module.Client(**client_kwargs) as client:
        response = client.post(config.auth_url, headers=headers, data=data)
        response.raise_for_status()
        token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Empty access_token from M2M auth response.")
    return token


def _parse_json_response(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```json\s*(\{.*\})\s*```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
    raise RuntimeError(f"LLM response is not valid JSON: {text}")


def _draft_from_json(data: dict, version: int) -> WhatToDoDraft:
    steps = [
        Step(
            condition=str(item.get("condition", "")),
            actions=[str(action) for action in item.get("actions", [])],
        )
        for item in data.get("steps", [])
    ]
    files_to_change = [
        FileChange(path=str(item.get("path", "")), reason=str(item.get("reason", "")))
        for item in data.get("files_to_change", [])
    ]
    open_questions = [
        OpenQuestion(
            id=str(item.get("id", f"oq-{index + 1}")),
            question=str(item.get("question", "")),
            reason=str(item.get("reason", "")),
            status=str(item.get("status", "open")),
            answer=item.get("answer"),
        )
        for index, item in enumerate(data.get("open_questions", []))
    ]
    return WhatToDoDraft(
        version=version,
        steps=steps,
        files_to_change=files_to_change,
        open_questions=open_questions,
        raw_text=_render_draft(steps, files_to_change),
        summary=str(data.get("summary", "")),
    )


def _evidence_to_prompt(item: EvidenceItem) -> dict:
    return {
        "path": item.path,
        "symbol": item.symbol,
        "why_relevant": item.why_relevant,
        "suggested_change": item.suggested_change,
        "location_hint": item.location_hint,
    }


def _reference_to_prompt(item: ParsedWhatToDo) -> dict:
    return {
        "steps": [{"condition": step.condition, "actions": step.actions} for step in item.steps],
        "files_to_change": [
            {"path": file_change.path, "reason": file_change.reason}
            for file_change in item.files_to_change
        ],
    }


def _draft_to_prompt(draft: WhatToDoDraft) -> dict:
    return {
        "version": draft.version,
        "steps": [{"condition": step.condition, "actions": step.actions} for step in draft.steps],
        "files_to_change": [
            {"path": item.path, "reason": item.reason} for item in draft.files_to_change
        ],
        "open_questions": [
            {
                "id": item.id,
                "question": item.question,
                "reason": item.reason,
                "status": item.status,
                "answer": item.answer,
            }
            for item in draft.open_questions
        ],
        "summary": draft.summary,
    }
