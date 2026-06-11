from __future__ import annotations

import json
import re
import time

from config import LlmApiConfig
from prompt_loader import load_fewshot_examples, render_prompt
from modules.shared.models import (
    EvidenceItem,
    FileChange,
    OpenQuestion,
    ParsedWhatToDo,
    RetrievalIntent,
    SoftwareRequirementsDraft,
    Step,
    WhatToDoDraft,
)


def _log(message: str) -> None:
    print(f"[AGENTIC-WORKFLOW][LLM] {message}", flush=True)


_TOKEN_CACHE: dict[str, dict[str, float | str]] = {}


def _normalize_retrieval_keywords(items: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        raw = str(item).strip()
        if not raw:
            continue
        for token in re.findall(r"[A-Za-z0-9_]+", raw.lower()):
            if len(token) < 2:
                continue
            if token in seen:
                continue
            seen.add(token)
            normalized.append(token)
    return normalized


def _should_retry_remote_error(exc: Exception) -> bool:
    retryable_names = {
        "ConnectError",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "RemoteProtocolError",
        "NetworkError",
        "TimeoutException",
    }
    current: Exception | None = exc
    while current is not None:
        if type(current).__name__ in retryable_names:
            return True
        status_code = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(status_code, int) and status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
            return True
        current = current.__cause__ if isinstance(current.__cause__, Exception) else None
    return False


def _retry_delay_seconds(config: LlmApiConfig, attempt: int) -> float:
    base = max(config.retry_backoff_seconds, 0.0)
    if base == 0:
        return 0.0
    return base * (2 ** max(attempt - 1, 0))


def _run_with_retries(
    *,
    operation_name: str,
    config: LlmApiConfig,
    func,
):
    max_attempts = max(config.max_retries, 0) + 1
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt > 1:
                _log(f"{operation_name} retry attempt {attempt}/{max_attempts}")
            return func()
        except Exception as exc:  # pragma: no cover - exercised via integration/runtime
            last_exc = exc
            if attempt >= max_attempts or not _should_retry_remote_error(exc):
                raise
            delay = _retry_delay_seconds(config, attempt)
            _log(
                f"{operation_name} failed on attempt {attempt}/{max_attempts} with "
                f"{type(exc).__name__}: {exc}. Retrying in {delay:.1f}s."
            )
            if delay > 0:
                time.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{operation_name} failed without an exception.")


def build_retrieval_intent(description: str, config: LlmApiConfig) -> tuple[str, str, list[str], list[str], str]:
    mode = config.mode.lower().strip()
    _log(f"build_retrieval_intent mode={mode}")
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
    _log(f"generate_draft mode={mode}")
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
    _log(f"refine_draft mode={mode}")
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


def generate_software_requirements(
    description: str,
    implementation_intent_specification: WhatToDoDraft,
    config: LlmApiConfig,
    *,
    source_iis_version_id: int | None,
    source_iis_version_number: int | None,
) -> SoftwareRequirementsDraft:
    mode = config.mode.lower().strip()
    _log(f"generate_software_requirements mode={mode}")
    if mode == "mock":
        return _generate_mock_software_requirements(
            implementation_intent_specification,
            source_iis_version_id=source_iis_version_id,
            source_iis_version_number=source_iis_version_number,
        )
    if mode == "local":
        return _generate_local_software_requirements(
            description,
            implementation_intent_specification,
            config,
            source_iis_version_id=source_iis_version_id,
            source_iis_version_number=source_iis_version_number,
        )
    if mode == "remote":
        return _generate_remote_software_requirements(
            description,
            implementation_intent_specification,
            config,
            source_iis_version_id=source_iis_version_id,
            source_iis_version_number=source_iis_version_number,
        )
    raise NotImplementedError(
        f"LLM mode '{config.mode}' is not implemented yet. "
        "Update modules/integrations/llm_adapter.py."
    )


def refine_software_requirements(
    current: SoftwareRequirementsDraft,
    user_message: str,
    answered_questions: list[dict[str, str]],
    config: LlmApiConfig,
) -> SoftwareRequirementsDraft:
    mode = config.mode.lower().strip()
    _log(f"refine_software_requirements mode={mode}")
    if mode == "mock":
        return _refine_mock_software_requirements(current, user_message, answered_questions)
    if mode == "local":
        return _refine_local_software_requirements(current, user_message, answered_questions, config)
    if mode == "remote":
        return _refine_remote_software_requirements(current, user_message, answered_questions, config)
    raise NotImplementedError(
        f"LLM mode '{config.mode}' is not implemented yet. "
        "Update modules/integrations/llm_adapter.py."
    )


def _build_mock_intent(description: str) -> tuple[str, str, list[str], list[str], str]:
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
    query = "\n".join(
        piece
        for piece in [
            summary,
            technical_intent,
            "keywords: " + ", ".join(keywords) if keywords else "",
            "suspected areas: " + ", ".join(suspected_areas) if suspected_areas else "",
        ]
        if piece
    )
    return summary, technical_intent, keywords, suspected_areas, query


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
        reference_note = " Draft format is guided by historical implementation samples."
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


def _generate_mock_software_requirements(
    implementation_intent_specification: WhatToDoDraft,
    *,
    source_iis_version_id: int | None,
    source_iis_version_number: int | None,
) -> SoftwareRequirementsDraft:
    requirements = []
    for index, step in enumerate(implementation_intent_specification.steps, start=1):
        if step.actions:
            requirements.extend(
                f"SR-{len(requirements) + 1}: The system shall {action[0].lower() + action[1:] if action else action}."
                for action in step.actions
            )
        elif step.condition:
            requirements.append(f"SR-{len(requirements) + 1}: The system shall satisfy the condition '{step.condition}'.")
    traceability_summary = [
        f"SR-{index}: Derived from IIS step {min(index, len(implementation_intent_specification.steps))}."
        for index in range(1, len(requirements) + 1)
    ]
    raw_text = _render_software_requirements(requirements, traceability_summary)
    return SoftwareRequirementsDraft(
        version=1,
        requirements=requirements,
        traceability_summary=traceability_summary,
        raw_text=raw_text,
        source_iis_version_id=source_iis_version_id,
        source_iis_version_number=source_iis_version_number,
    )


def _build_local_intent(description: str, config: LlmApiConfig) -> tuple[str, str, list[str], list[str], str]:
    raise NotImplementedError(
        "Local LLM mode is selected, but local summarization/inference is not implemented yet. "
        "Use config.local_model_path and config.device in this file."
    )


def _build_remote_intent(description: str, config: LlmApiConfig) -> tuple[str, str, list[str], list[str], str]:
    _log("Rendering retrieval_intent_system prompt")
    prompt = render_prompt(
        "retrieval_intent_system",
        description=description,
        fewshot=json.dumps(load_fewshot_examples("retrieval_intent_fewshot"), ensure_ascii=False, indent=2),
    )
    _log(f"Retrieval intent prompt rendered. chars={len(prompt)}")
    payload = _call_remote_chat(prompt, config)
    _log(f"Retrieval intent response received. chars={len(payload)}")
    data = _parse_json_response(payload)
    summary = str(data.get("summary", ""))
    technical_intent = str(data.get("technical_intent", ""))
    keywords = _normalize_retrieval_keywords([str(item) for item in data.get("keywords", [])])
    suspected_areas = [str(item) for item in data.get("suspected_areas", [])]

    _log("Rendering retrieval_query_system prompt")
    query_prompt = render_prompt(
        "retrieval_query_system",
        description=description,
        summary=summary,
        technical_intent=technical_intent,
        keywords=json.dumps(keywords, ensure_ascii=False),
        suspected_areas=json.dumps(suspected_areas, ensure_ascii=False),
        fewshot=json.dumps(load_fewshot_examples("retrieval_query_fewshot"), ensure_ascii=False, indent=2),
    )
    _log(f"Retrieval query prompt rendered. chars={len(query_prompt)}")
    query_payload = _call_remote_chat(query_prompt, config)
    _log(f"Retrieval query response received. chars={len(query_payload)}")
    query_data = _parse_json_response(query_payload)
    query = str(query_data.get("query", "")).strip()
    return (
        summary,
        technical_intent,
        keywords,
        suspected_areas,
        query,
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


def _generate_local_software_requirements(
    description: str,
    implementation_intent_specification: WhatToDoDraft,
    config: LlmApiConfig,
    *,
    source_iis_version_id: int | None,
    source_iis_version_number: int | None,
) -> SoftwareRequirementsDraft:
    raise NotImplementedError(
        "Local LLM mode is selected, but local software-requirements generation is not implemented yet."
    )


def _generate_remote_draft(
    description: str,
    evidence: list[EvidenceItem],
    references: list[ParsedWhatToDo],
    config: LlmApiConfig,
) -> WhatToDoDraft:
    _log("Rendering draft_generation_system prompt")
    prompt = render_prompt(
        "draft_generation_system",
        description=description,
        evidence=json.dumps([_evidence_to_prompt(item) for item in evidence], ensure_ascii=False, indent=2),
        references=json.dumps([_reference_to_prompt(item) for item in references], ensure_ascii=False, indent=2),
        fewshot=json.dumps(load_fewshot_examples("draft_generation_fewshot"), ensure_ascii=False, indent=2),
    )
    _log(f"Draft generation prompt rendered. chars={len(prompt)}")
    payload = _call_remote_chat(prompt, config)
    _log(f"Draft generation response received. chars={len(payload)}")
    data = _parse_json_response(payload)
    return _draft_from_json(data, version=1)


def _generate_remote_software_requirements(
    description: str,
    implementation_intent_specification: WhatToDoDraft,
    config: LlmApiConfig,
    *,
    source_iis_version_id: int | None,
    source_iis_version_number: int | None,
) -> SoftwareRequirementsDraft:
    _log("Rendering software_requirements_system prompt")
    prompt = render_prompt(
        "software_requirements_system",
        description=description,
        implementation_intent_specification=json.dumps(
            _draft_to_prompt(implementation_intent_specification),
            ensure_ascii=False,
            indent=2,
        ),
        fewshot=json.dumps(load_fewshot_examples("software_requirements_fewshot"), ensure_ascii=False, indent=2),
    )
    _log(f"Software requirements prompt rendered. chars={len(prompt)}")
    payload = _call_remote_chat(prompt, config)
    _log(f"Software requirements response received. chars={len(payload)}")
    data = _parse_json_response(payload)
    return _software_requirements_from_json(
        data,
        version=1,
        source_iis_version_id=source_iis_version_id,
        source_iis_version_number=source_iis_version_number,
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


def _refine_mock_software_requirements(
    current: SoftwareRequirementsDraft,
    user_message: str,
    answered_questions: list[dict[str, str]],
) -> SoftwareRequirementsDraft:
    additions = []
    if user_message.strip():
        additions.append(user_message.strip())
    additions.extend(
        f"Resolved {item['id']}: {item['answer']}"
        for item in answered_questions
        if item.get("id") and item.get("answer")
    )
    requirements = list(current.requirements) + [
        f"SR-{len(current.requirements) + index}: {text}"
        for index, text in enumerate(additions, start=1)
    ]
    raw_text = _render_software_requirements(requirements, current.traceability_summary)
    return SoftwareRequirementsDraft(
        version=current.version + 1,
        requirements=requirements,
        traceability_summary=current.traceability_summary,
        raw_text=raw_text,
        source_iis_version_id=current.source_iis_version_id,
        source_iis_version_number=current.source_iis_version_number,
    )


def _refine_local_software_requirements(
    current: SoftwareRequirementsDraft,
    user_message: str,
    answered_questions: list[dict[str, str]],
    config: LlmApiConfig,
) -> SoftwareRequirementsDraft:
    raise NotImplementedError(
        "Local LLM mode is selected, but local software-requirements refine is not implemented yet."
    )


def _refine_remote_draft(
    current: WhatToDoDraft,
    user_message: str,
    answered_questions: list[dict[str, str]],
    config: LlmApiConfig,
) -> WhatToDoDraft:
    _log("Rendering refine_open_questions_system prompt")
    prompt = render_prompt(
        "refine_open_questions_system",
        current_draft=json.dumps(_draft_to_prompt(current), ensure_ascii=False, indent=2),
        user_message=user_message,
        answered_questions=json.dumps(answered_questions, ensure_ascii=False, indent=2),
        fewshot=json.dumps(load_fewshot_examples("refine_open_questions_fewshot"), ensure_ascii=False, indent=2),
    )
    _log(f"Refine prompt rendered. chars={len(prompt)}")
    payload = _call_remote_chat(prompt, config)
    _log(f"Refine response received. chars={len(payload)}")
    data = _parse_json_response(payload)
    return _draft_from_json(data, version=current.version + 1)


def _refine_remote_software_requirements(
    current: SoftwareRequirementsDraft,
    user_message: str,
    answered_questions: list[dict[str, str]],
    config: LlmApiConfig,
) -> SoftwareRequirementsDraft:
    _log("Rendering refine_software_requirements_system prompt")
    prompt = render_prompt(
        "refine_software_requirements_system",
        current_software_requirements=json.dumps(
            _software_requirements_to_prompt(current),
            ensure_ascii=False,
            indent=2,
        ),
        user_message=user_message,
        answered_questions=json.dumps(answered_questions, ensure_ascii=False, indent=2),
        fewshot=json.dumps(load_fewshot_examples("software_requirements_fewshot"), ensure_ascii=False, indent=2),
    )
    _log(f"Software requirements refine prompt rendered. chars={len(prompt)}")
    payload = _call_remote_chat(prompt, config)
    _log(f"Software requirements refine response received. chars={len(payload)}")
    data = _parse_json_response(payload)
    return _software_requirements_from_json(
        data,
        version=current.version + 1,
        source_iis_version_id=current.source_iis_version_id,
        source_iis_version_number=current.source_iis_version_number,
    )


def _render_draft(steps: list[Step], files_to_change: list[FileChange]) -> str:
    lines = ["## What to do", ""]
    for step in steps:
        if step.condition:
            lines.append(f"- {step.condition}")
        for action in step.actions:
            lines.append(f"  - {action}" if step.condition else f"- {action}")
    lines.extend(["", "## Where to change", ""])
    for item in files_to_change:
        lines.append(f"- `{item.path}`: {item.reason}")
    return "\n".join(lines)


def _call_remote_chat(prompt: str, config: LlmApiConfig) -> str:
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "Remote LLM mode requires httpx. Install dependencies with `python -m pip install -r requirements.txt`."
        ) from exc

    _log("Preparing remote chat call")

    def _send_request() -> str:
        access_token = config.access_token or _get_access_token(config, httpx)
        url = f"{config.endpoint.rstrip('/')}{config.api_path}"
        _log(f"Remote endpoint ready url={url} model={config.model}")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "x-apikey": config.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "model": config.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if config.include_tuning_params:
            payload.update(
                {
                    "temperature": config.temperature,
                    "max_tokens": config.max_tokens,
                    "top_p": config.top_p,
                    "presence_penalty": config.presence_penalty,
                    "frequency_penalty": config.frequency_penalty,
                }
            )
        client_kwargs = {"timeout": config.timeout_seconds}
        if config.cert_path:
            client_kwargs["verify"] = config.cert_path

        _log(f"Request URL={url}")
        _log(f"Request headers keys={list(headers.keys())}")
        _log(f"x-apikey length={len(config.api_key) if config.api_key else 0}")
        _log(f"Payload keys={list(payload.keys())}")

        with httpx.Client(**client_kwargs) as client:
            _log("Sending remote chat request")
            response = client.post(url, headers=headers, json=payload)
            _log(f"Remote chat response status={response.status_code}")
            _log(f"Remote chat response headers={dict(response.headers)}")
            try:
                body = response.json()
                _log(f"Remote chat response body(JSON)={json.dumps(body, ensure_ascii=False)[:4000]}")
            except Exception:
                body = None
                _log(f"Remote chat response body(raw)={response.text[:4000]}")
            response.raise_for_status()
        if body is None:
            raise RuntimeError("Remote LLM response body is not valid JSON.")
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response shape: {body}") from exc

    return _run_with_retries(
        operation_name="remote_chat_request",
        config=config,
        func=_send_request,
    )


def _get_access_token(config: LlmApiConfig, httpx_module) -> str:
    cache_key = f"{config.auth_url}|{config.client_id}"
    cached = _TOKEN_CACHE.get(cache_key)
    now = time.time()
    if cached and isinstance(cached.get("expires_at"), float) and cached["expires_at"] > now:
        _log("Reusing cached M2M token")
        return str(cached["token"])

    if not config.client_id or not config.client_secret or not config.auth_url:
        raise RuntimeError(
            "Remote LLM mode requires either AGENTIC_WORKFLOW_LLM_ACCESS_TOKEN or the full M2M config: "
            "AGENTIC_WORKFLOW_LLM_AUTH_URL, AGENTIC_WORKFLOW_LLM_CLIENT_ID, AGENTIC_WORKFLOW_LLM_CLIENT_SECRET."
        )
    _log(f"Requesting M2M token from auth_url={config.auth_url}")
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

    def _fetch_token() -> str:
        with httpx_module.Client(**client_kwargs) as client:
            response = client.post(config.auth_url, headers=headers, data=data)
            _log(f"M2M token response status={response.status_code}")
            try:
                response_body = response.json()
                _log(f"M2M token body(JSON)={json.dumps(response_body, ensure_ascii=False)[:4000]}")
            except Exception:
                response_body = None
                _log(f"M2M token body(raw)={response.text[:4000]}")
            response.raise_for_status()
            if response_body is None:
                raise RuntimeError("M2M token response is not valid JSON.")
            token = response_body.get("access_token")
            if not token:
                raise RuntimeError("Empty access_token from M2M auth response.")
            expires_in = int(response_body.get("expires_in", 0) or 0)
        expires_at = now + max(expires_in - 60, 60)
        _TOKEN_CACHE[cache_key] = {
            "token": str(token),
            "expires_at": float(expires_at),
        }
        _log("M2M token acquired successfully")
        return str(token)

    return _run_with_retries(
        operation_name="m2m_token_request",
        config=config,
        func=_fetch_token,
    )


def _parse_json_response(text: str) -> dict:
    _log("Parsing LLM response as JSON")
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


def _software_requirements_from_json(
    data: dict,
    *,
    version: int,
    source_iis_version_id: int | None,
    source_iis_version_number: int | None,
) -> SoftwareRequirementsDraft:
    requirements = []
    for item in data.get("software_requirements", []):
        if isinstance(item, dict):
            req_id = str(item.get("id", "")).strip()
            text = str(item.get("text", "")).strip()
            if text:
                requirements.append(f"{req_id}: {text}" if req_id else text)
        else:
            text = str(item).strip()
            if text:
                requirements.append(text)
    traceability_summary = []
    for item in data.get("traceability_summary", []):
        if isinstance(item, dict):
            req_id = str(item.get("requirement_id", "")).strip()
            mappings = [str(entry).strip() for entry in item.get("maps_to", []) if str(entry).strip()]
            if mappings:
                prefix = f"{req_id}: " if req_id else ""
                traceability_summary.append(prefix + "; ".join(mappings))
        else:
            text = str(item).strip()
            if text:
                traceability_summary.append(text)
    return SoftwareRequirementsDraft(
        version=version,
        requirements=requirements,
        traceability_summary=traceability_summary,
        raw_text=_render_software_requirements(requirements, traceability_summary),
        source_iis_version_id=source_iis_version_id,
        source_iis_version_number=source_iis_version_number,
    )


def _evidence_to_prompt(item: EvidenceItem) -> dict:
    return {
        "path": item.path,
        "symbol": item.symbol,
        "why_relevant": item.why_relevant,
        "suggested_change": item.suggested_change,
        "location_hint": item.location_hint,
    }


def _retrieval_intent_to_prompt(intent: RetrievalIntent) -> dict:
    return {
        "summary": intent.summary,
        "technical_intent": intent.technical_intent,
        "keywords": intent.keywords,
        "suspected_areas": intent.suspected_areas,
        "query": intent.query,
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


def _software_requirements_to_prompt(software_requirements: SoftwareRequirementsDraft) -> dict:
    return {
        "version": software_requirements.version,
        "software_requirements": software_requirements.requirements,
        "traceability_summary": software_requirements.traceability_summary,
        "source_iis_version_id": software_requirements.source_iis_version_id,
        "source_iis_version_number": software_requirements.source_iis_version_number,
    }


def _render_software_requirements(requirements: list[str], traceability_summary: list[str]) -> str:
    lines = ["## Software Requirements", ""]
    for item in requirements:
        lines.append(f"- {item}")
    lines.extend(["", "## Traceability Summary", ""])
    for item in traceability_summary:
        lines.append(f"- {item}")
    return "\n".join(lines)
