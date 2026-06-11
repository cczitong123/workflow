from __future__ import annotations

"""
Run end-to-end IIS and Software Requirements evaluation on a prepared dataset.

Edit the configuration block at the top of this file, then run:

    python tools/run_evaluation.py

This script can:
- generate IIS and Software Requirements from Epic descriptions
- compare them against historical references
- ask an LLM judge to score multiple quality dimensions
- aggregate scores by task type and difficulty
- export detailed JSON and a Markdown summary
"""

import hashlib
import re
import time
from datetime import datetime, timezone

from evaluation_utils import (
    PROJECT_ROOT,
    apply_retrieval_strategy_override,
    aggregate_scores,
    evaluate_iis_case,
    evaluate_retrieval_case,
    evaluate_software_requirements_case,
    generate_iis_payload,
    generate_retrieval_intent_payload,
    generate_software_requirements_payload,
    get_case_historical_changed_files,
    get_case_description,
    get_case_difficulty,
    get_case_historical_software_requirements,
    get_case_historical_what_to_do,
    get_case_id,
    get_case_task_type,
    render_historical_software_requirements_text,
    load_eval_config,
    load_json,
    render_markdown_summary,
    retrieve_evidence_payload,
    write_json,
    write_markdown,
)


# Configuration - edit these values directly before running.
DATASET_PATH = PROJECT_ROOT / "tools" / "evaluation_dataset.example.json"
OUTPUT_DIR = PROJECT_ROOT / "tools" / "evaluation_outputs"
RUN_GENERATION = True
EVALUATE_IIS = True
EVALUATE_SOFTWARE_REQUIREMENTS = True
RESUME_FROM_CHECKPOINTS = True
SKIP_COMPLETED_CHECKPOINTS = True
CONTINUE_AFTER_CASE_ERROR = True
CASE_IDS: list[str] = []
MAX_CASES: int | None = None
RANKING_STRATEGY_EXPERIMENTS = [
    {
        "enabled": True,
        "name": "semantic_only",
        "ranking_mode": "semantic_only",
    },
    {
        "enabled": True,
        "name": "filename_only",
        "ranking_mode": "filename_only",
    },
    {
        "enabled": True,
        "name": "weighted_85_15",
        "ranking_mode": "weight",
        "ranking_alpha": 0.85,
        "ranking_beta": 0.15,
    },
    {
        "enabled": True,
        "name": "weighted_70_30",
        "ranking_mode": "weight",
        "ranking_alpha": 0.7,
        "ranking_beta": 0.3,
    },
    {
        "enabled": True,
        "name": "weighted_50_50",
        "ranking_mode": "weight",
        "ranking_alpha": 0.5,
        "ranking_beta": 0.5,
    },
]
FILE_AGGREGATION_STRATEGY_EXPERIMENTS = [
    {
        "enabled": True,
        "name": "max_only",
        "file_aggregation_strategy": "max_only",
    },
    {
        "enabled": True,
        "name": "max_plus_second",
        "file_aggregation_strategy": "max_plus_second",
        "file_aggregation_alpha": 0.25,
    },
    {
        "enabled": True,
        "name": "max_plus_log_count",
        "file_aggregation_strategy": "max_plus_log_count",
        "file_aggregation_beta": 0.05,
    },
    {
        "enabled": True,
        "name": "max_plus_second_plus_log_count",
        "file_aggregation_strategy": "max_plus_second_plus_log_count",
        "file_aggregation_alpha": 0.25,
        "file_aggregation_beta": 0.05,
    },
    {
        "enabled": True,
        "name": "sum_all",
        "file_aggregation_strategy": "sum_all",
    },
]
CHECKPOINT_DIR = OUTPUT_DIR / "checkpoints"


def _should_keep_case(case: dict[str, object]) -> bool:
    case_id = _resolve_case_id(case)
    if CASE_IDS and case_id not in CASE_IDS:
        return False
    return True


def _log_eval_progress(
    *,
    step_label: str,
    strategy_name: str,
    case_id: str,
    case_index: int,
    total_cases: int,
    strategy_index: int,
    total_strategies: int,
) -> None:
    print(
        f"[EVAL][strategy {strategy_index}/{total_strategies}={strategy_name}]"
        f"[case {case_index}/{total_cases}={case_id}] {step_label}",
        flush=True,
    )


def _log_checkpoint_path(path) -> None:
    print(f"[EVAL][checkpoint] {path}", flush=True)


def _is_enabled(experiment: dict[str, object]) -> bool:
    value = experiment.get("enabled", True)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return slug.strip("_") or "unnamed"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _description_preview(text: str, limit: int = 120) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _resolve_case_id(case: dict[str, object]) -> str:
    for key in ("case_id", "epic_id", "issue_key", "epic_key", "key", "id"):
        value = str(case.get(key, "")).strip()
        if value and value.lower() not in {"unknown", "unknown-case", "none"}:
            return value
    description = " ".join(str(case.get("description", "")).split()).strip()
    if description:
        digest = hashlib.sha1(description.encode("utf-8")).hexdigest()[:10].upper()
        return f"CASE-{digest}"
    return "CASE-NO-DESCRIPTION"


def _checkpoint_path(strategy_name: str, case_id: str) -> object:
    return CHECKPOINT_DIR / _slugify(strategy_name) / f"{_slugify(case_id)}.json"


def _build_base_case_result(case: dict[str, object], strategy_name: str, ranking_name: str, file_aggregation_name: str) -> dict[str, object]:
    case_id = _resolve_case_id(case)
    description_preview = _description_preview(get_case_description(case))
    return {
        "case_id": case_id,
        "case_label": f"{case_id} | {description_preview}" if description_preview else case_id,
        "description_preview": description_preview,
        "retrieval_strategy": strategy_name,
        "ranking_strategy": ranking_name,
        "file_aggregation_strategy": file_aggregation_name,
        "task_type": get_case_task_type(case),
        "difficulty": get_case_difficulty(case),
        "generation": {},
    }


def _build_base_checkpoint(
    *,
    case: dict[str, object],
    strategy_name: str,
    ranking_name: str,
    file_aggregation_name: str,
) -> dict[str, object]:
    return {
        "case_id": _resolve_case_id(case),
        "strategy_name": strategy_name,
        "ranking_strategy": ranking_name,
        "file_aggregation_strategy": file_aggregation_name,
        "status": "pending",
        "last_step": "",
        "started_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "completed_at": None,
        "error": None,
        "stages": {},
        "case_result": _build_base_case_result(case, strategy_name, ranking_name, file_aggregation_name),
    }


def _load_checkpoint(path) -> dict[str, object] | None:
    if not path.exists():
        return None
    payload = load_json(path)
    return payload if isinstance(payload, dict) else None


def _save_checkpoint(path, checkpoint: dict[str, object]) -> None:
    checkpoint["updated_at"] = _utc_now_iso()
    write_json(path, checkpoint)


def _mark_stage(
    checkpoint: dict[str, object],
    *,
    stage_name: str,
    status: str,
) -> None:
    stages = checkpoint.setdefault("stages", {})
    if not isinstance(stages, dict):
        stages = {}
        checkpoint["stages"] = stages
    stages[stage_name] = {
        "status": status,
        "updated_at": _utc_now_iso(),
    }


def _write_run_outputs(results: list[dict[str, object]]) -> None:
    aggregate = aggregate_scores(results)
    write_json(OUTPUT_DIR / "evaluation_results.json", results)
    write_json(OUTPUT_DIR / "evaluation_aggregate.json", aggregate)
    write_markdown(OUTPUT_DIR / "evaluation_summary.md", render_markdown_summary(results, aggregate))


def _looks_like_code_path(value: str) -> bool:
    text = value.strip()
    if not text or " " in text:
        return False
    if "/" not in text and "\\" not in text:
        return False
    return bool(re.search(r"\.[A-Za-z0-9_+-]+$", text))


def _print_dataset_sanity_check(cases: list[dict[str, object]]) -> None:
    total_cases = len(cases)
    with_explicit_id = 0
    with_files = 0
    with_what_to_do = 0
    with_srs = 0
    with_task_type = 0
    with_difficulty = 0
    suspicious_file_cases: list[tuple[str, list[str]]] = []

    for case in cases:
        case_id = _resolve_case_id(case)
        explicit_id = get_case_id(case)
        if explicit_id:
            with_explicit_id += 1

        files = get_case_historical_changed_files(case)
        if files:
            with_files += 1
            suspicious_lines = [path for path in files if not _looks_like_code_path(path)]
            if suspicious_lines:
                suspicious_file_cases.append((case_id, suspicious_lines[:3]))

        if get_case_historical_what_to_do(case):
            with_what_to_do += 1
        if get_case_historical_software_requirements(case):
            with_srs += 1
        if get_case_task_type(case):
            with_task_type += 1
        if get_case_difficulty(case):
            with_difficulty += 1

    print("[EVAL][sanity] Dataset sanity check", flush=True)
    print(f"[EVAL][sanity] total_cases={total_cases}", flush=True)
    print(
        f"[EVAL][sanity] explicit_ids={with_explicit_id}/{total_cases} | "
        f"files={with_files}/{total_cases} | "
        f"what_to_do={with_what_to_do}/{total_cases} | "
        f"srs={with_srs}/{total_cases}",
        flush=True,
    )
    print(
        f"[EVAL][sanity] task_type={with_task_type}/{total_cases} | "
        f"difficulty={with_difficulty}/{total_cases}",
        flush=True,
    )

    if suspicious_file_cases:
        preview_count = min(len(suspicious_file_cases), 10)
        print(
            f"[EVAL][sanity][warn] {len(suspicious_file_cases)} case(s) contain file lines "
            f"that do not look like code paths. Showing first {preview_count}:",
            flush=True,
        )
        for case_id, samples in suspicious_file_cases[:preview_count]:
            joined = " | ".join(samples)
            print(f"[EVAL][sanity][warn] {case_id}: {joined}", flush=True)
    else:
        print("[EVAL][sanity] No suspicious file lines detected.", flush=True)


def main() -> None:
    app_config = load_eval_config()
    dataset = load_json(DATASET_PATH)
    if not isinstance(dataset, list):
        raise TypeError("Evaluation dataset must be a JSON array of case objects.")

    cases = [case for case in dataset if isinstance(case, dict) and _should_keep_case(case)]
    if MAX_CASES is not None:
        cases = cases[:MAX_CASES]
    _print_dataset_sanity_check(cases)

    results: list[dict[str, object]] = []
    total_cases = len(cases)
    enabled_ranking_experiments = [
        config for config in RANKING_STRATEGY_EXPERIMENTS if _is_enabled(config)
    ]
    enabled_file_aggregation_experiments = [
        config for config in FILE_AGGREGATION_STRATEGY_EXPERIMENTS if _is_enabled(config)
    ]
    if not enabled_ranking_experiments:
        raise ValueError("No ranking strategy experiments are enabled.")
    if not enabled_file_aggregation_experiments:
        raise ValueError("No file aggregation strategy experiments are enabled.")

    strategy_matrix = []
    for ranking_config in enabled_ranking_experiments:
        for aggregation_config in enabled_file_aggregation_experiments:
            ranking_name = str(ranking_config.get("name") or ranking_config.get("ranking_mode") or "unnamed")
            aggregation_name = str(
                aggregation_config.get("name")
                or aggregation_config.get("file_aggregation_strategy")
                or "unnamed"
            )
            strategy_matrix.append(
                {
                    "name": f"{ranking_name}__{aggregation_name}",
                    "ranking_name": ranking_name,
                    "file_aggregation_name": aggregation_name,
                    "ranking_mode": str(ranking_config.get("ranking_mode", "semantic_only")),
                    "ranking_alpha": ranking_config.get("ranking_alpha"),
                    "ranking_beta": ranking_config.get("ranking_beta"),
                    "file_aggregation_strategy": str(
                        aggregation_config.get("file_aggregation_strategy", "max_only")
                    ),
                    "file_aggregation_alpha": aggregation_config.get("file_aggregation_alpha"),
                    "file_aggregation_beta": aggregation_config.get("file_aggregation_beta"),
                    "candidate_multiplier": aggregation_config.get("candidate_multiplier"),
                }
            )

    total_strategies = len(strategy_matrix)

    for strategy_index, strategy_config in enumerate(strategy_matrix, start=1):
        strategy_name = str(strategy_config.get("name", "unnamed"))
        ranking_name = str(strategy_config.get("ranking_name", "unknown"))
        file_aggregation_name = str(strategy_config.get("file_aggregation_name", "unknown"))
        strategy_app_config = apply_retrieval_strategy_override(
            app_config,
            ranking_mode=strategy_config.get("ranking_mode"),
            ranking_alpha=strategy_config.get("ranking_alpha"),
            ranking_beta=strategy_config.get("ranking_beta"),
            file_aggregation_strategy=strategy_config.get("file_aggregation_strategy"),
            file_aggregation_alpha=strategy_config.get("file_aggregation_alpha"),
            file_aggregation_beta=strategy_config.get("file_aggregation_beta"),
            candidate_multiplier=strategy_config.get("candidate_multiplier"),
        )

        for case_index, case in enumerate(cases, start=1):
            case_id = _resolve_case_id(case)
            case_started_at = time.perf_counter()
            checkpoint_path = _checkpoint_path(strategy_name, case_id)
            checkpoint = (
                _load_checkpoint(checkpoint_path)
                if RESUME_FROM_CHECKPOINTS
                else None
            )
            if checkpoint is None:
                checkpoint = _build_base_checkpoint(
                    case=case,
                    strategy_name=strategy_name,
                    ranking_name=ranking_name,
                    file_aggregation_name=file_aggregation_name,
                )
            case_result = checkpoint.get("case_result")
            if not isinstance(case_result, dict):
                case_result = _build_base_case_result(case, strategy_name, ranking_name, file_aggregation_name)
                checkpoint["case_result"] = case_result

            if (
                RESUME_FROM_CHECKPOINTS
                and SKIP_COMPLETED_CHECKPOINTS
                and checkpoint.get("status") == "completed"
            ):
                results.append(case_result)
                _log_eval_progress(
                    step_label="skipping completed checkpoint",
                    strategy_name=strategy_name,
                    case_id=case_id,
                    case_index=case_index,
                    total_cases=total_cases,
                    strategy_index=strategy_index,
                    total_strategies=total_strategies,
                )
                continue

            checkpoint["status"] = "running"
            checkpoint["error"] = None
            checkpoint["last_step"] = "starting"
            _save_checkpoint(checkpoint_path, checkpoint)
            _log_checkpoint_path(checkpoint_path)
            _log_eval_progress(
                step_label=f"starting case (ranking={ranking_name}, file_agg={file_aggregation_name})",
                strategy_name=strategy_name,
                case_id=case_id,
                case_index=case_index,
                total_cases=total_cases,
                strategy_index=strategy_index,
                total_strategies=total_strategies,
            )
            try:
                generation = case_result.setdefault("generation", {})
                generation["retrieval_config"] = {
                    "ranking_mode": strategy_app_config.code_rag.ranking_mode,
                    "ranking_alpha": strategy_app_config.code_rag.ranking_alpha,
                    "ranking_beta": strategy_app_config.code_rag.ranking_beta,
                    "file_aggregation_strategy": strategy_app_config.code_rag.file_aggregation_strategy,
                    "file_aggregation_alpha": strategy_app_config.code_rag.file_aggregation_alpha,
                    "file_aggregation_beta": strategy_app_config.code_rag.file_aggregation_beta,
                    "file_aggregation_candidate_multiplier": strategy_app_config.code_rag.file_aggregation_candidate_multiplier,
                }
                _save_checkpoint(checkpoint_path, checkpoint)

                if RUN_GENERATION:
                    if not generation.get("retrieval_intent"):
                        checkpoint["last_step"] = "retrieval_intent"
                        _log_eval_progress(
                            step_label="generating retrieval intent",
                            strategy_name=strategy_name,
                            case_id=case_id,
                            case_index=case_index,
                            total_cases=total_cases,
                            strategy_index=strategy_index,
                            total_strategies=total_strategies,
                        )
                        generation["retrieval_intent"] = generate_retrieval_intent_payload(
                            get_case_description(case),
                            strategy_app_config,
                        )
                        _mark_stage(checkpoint, stage_name="retrieval_intent", status="completed")
                        _save_checkpoint(checkpoint_path, checkpoint)

                    if not generation.get("evidence"):
                        checkpoint["last_step"] = "evidence"
                        _log_eval_progress(
                            step_label="retrieving code evidence",
                            strategy_name=strategy_name,
                            case_id=case_id,
                            case_index=case_index,
                            total_cases=total_cases,
                            strategy_index=strategy_index,
                            total_strategies=total_strategies,
                        )
                        generation["evidence"] = retrieve_evidence_payload(
                            generation["retrieval_intent"],
                            strategy_app_config,
                        )
                        _mark_stage(checkpoint, stage_name="evidence", status="completed")
                        _save_checkpoint(checkpoint_path, checkpoint)

                    if not generation.get("iis"):
                        checkpoint["last_step"] = "iis"
                        _log_eval_progress(
                            step_label="generating IIS",
                            strategy_name=strategy_name,
                            case_id=case_id,
                            case_index=case_index,
                            total_cases=total_cases,
                            strategy_index=strategy_index,
                            total_strategies=total_strategies,
                        )
                        generation["iis"] = generate_iis_payload(
                            get_case_description(case),
                            generation["evidence"],
                            strategy_app_config,
                        )
                        _mark_stage(checkpoint, stage_name="iis", status="completed")
                        _save_checkpoint(checkpoint_path, checkpoint)

                    if not generation.get("software_requirements"):
                        checkpoint["last_step"] = "software_requirements"
                        _log_eval_progress(
                            step_label="generating software requirements",
                            strategy_name=strategy_name,
                            case_id=case_id,
                            case_index=case_index,
                            total_cases=total_cases,
                            strategy_index=strategy_index,
                            total_strategies=total_strategies,
                        )
                        generation["software_requirements"] = generate_software_requirements_payload(
                            get_case_description(case),
                            generation["iis"],
                            strategy_app_config,
                        )
                        _mark_stage(checkpoint, stage_name="software_requirements", status="completed")
                        _save_checkpoint(checkpoint_path, checkpoint)
                else:
                    if not any(
                        generation.get(key)
                        for key in ("retrieval_intent", "evidence", "iis", "software_requirements")
                    ):
                        checkpoint["last_step"] = "load_pregenerated"
                        _log_eval_progress(
                            step_label="using pre-generated artifacts from dataset",
                            strategy_name=strategy_name,
                            case_id=case_id,
                            case_index=case_index,
                            total_cases=total_cases,
                            strategy_index=strategy_index,
                            total_strategies=total_strategies,
                        )
                        generation.update(
                            {
                                "retrieval_intent": case.get("generated_retrieval_intent"),
                                "evidence": case.get("generated_evidence"),
                                "iis": case.get("generated_iis"),
                                "software_requirements": case.get("generated_software_requirements"),
                            }
                        )
                        _mark_stage(checkpoint, stage_name="load_pregenerated", status="completed")
                        _save_checkpoint(checkpoint_path, checkpoint)

                generated_iis_text = str((generation.get("iis") or {}).get("raw_text", "")).strip()
                generated_software_requirements_text = str(
                    (generation.get("software_requirements") or {}).get("raw_text", "")
                ).strip()

                if "retrieval_metrics" not in case_result:
                    checkpoint["last_step"] = "retrieval_metrics"
                    case_result["retrieval_metrics"] = evaluate_retrieval_case(
                        case=case,
                        generation=generation,
                    )
                    _mark_stage(checkpoint, stage_name="retrieval_metrics", status="completed")
                    _save_checkpoint(checkpoint_path, checkpoint)
                    _log_eval_progress(
                        step_label="computed retrieval metrics",
                        strategy_name=strategy_name,
                        case_id=case_id,
                        case_index=case_index,
                        total_cases=total_cases,
                        strategy_index=strategy_index,
                        total_strategies=total_strategies,
                    )

                if EVALUATE_IIS and generated_iis_text and "iis_evaluation" not in case_result:
                    checkpoint["last_step"] = "generated_iis_evaluation"
                    _log_eval_progress(
                        step_label="scoring generated IIS",
                        strategy_name=strategy_name,
                        case_id=case_id,
                        case_index=case_index,
                        total_cases=total_cases,
                        strategy_index=strategy_index,
                        total_strategies=total_strategies,
                    )
                    case_result["iis_evaluation"] = evaluate_iis_case(
                        case=case,
                        candidate_label="generated_iis",
                        candidate_iis_text=generated_iis_text,
                        candidate_evidence=generation.get("evidence"),
                        app_config=strategy_app_config,
                    )
                    _mark_stage(checkpoint, stage_name="generated_iis_evaluation", status="completed")
                    _save_checkpoint(checkpoint_path, checkpoint)

                historical_what_to_do = get_case_historical_what_to_do(case)
                if (
                    EVALUATE_IIS
                    and historical_what_to_do
                    and "historical_iis_baseline_evaluation" not in case_result
                ):
                    checkpoint["last_step"] = "historical_iis_baseline_evaluation"
                    _log_eval_progress(
                        step_label="scoring historical what-to-do baseline",
                        strategy_name=strategy_name,
                        case_id=case_id,
                        case_index=case_index,
                        total_cases=total_cases,
                        strategy_index=strategy_index,
                        total_strategies=total_strategies,
                    )
                    case_result["historical_iis_baseline_evaluation"] = evaluate_iis_case(
                        case=case,
                        candidate_label="historical_what_to_do_baseline",
                        candidate_iis_text=historical_what_to_do,
                        candidate_evidence=None,
                        app_config=strategy_app_config,
                    )
                    _mark_stage(checkpoint, stage_name="historical_iis_baseline_evaluation", status="completed")
                    _save_checkpoint(checkpoint_path, checkpoint)

                if (
                    EVALUATE_SOFTWARE_REQUIREMENTS
                    and generated_software_requirements_text
                    and "software_requirements_evaluation" not in case_result
                ):
                    checkpoint["last_step"] = "generated_software_requirements_evaluation"
                    _log_eval_progress(
                        step_label="scoring generated software requirements",
                        strategy_name=strategy_name,
                        case_id=case_id,
                        case_index=case_index,
                        total_cases=total_cases,
                        strategy_index=strategy_index,
                        total_strategies=total_strategies,
                    )
                    case_result["software_requirements_evaluation"] = evaluate_software_requirements_case(
                        case=case,
                        confirmed_iis_text=generated_iis_text,
                        candidate_label="generated_software_requirements",
                        candidate_software_requirements_text=generated_software_requirements_text,
                        app_config=strategy_app_config,
                    )
                    _mark_stage(checkpoint, stage_name="generated_software_requirements_evaluation", status="completed")
                    _save_checkpoint(checkpoint_path, checkpoint)

                historical_software_requirements_text = render_historical_software_requirements_text(
                    get_case_historical_software_requirements(case)
                )
                if (
                    EVALUATE_SOFTWARE_REQUIREMENTS
                    and historical_software_requirements_text
                    and "historical_software_requirements_baseline_evaluation" not in case_result
                ):
                    checkpoint["last_step"] = "historical_software_requirements_baseline_evaluation"
                    _log_eval_progress(
                        step_label="scoring historical software requirements baseline",
                        strategy_name=strategy_name,
                        case_id=case_id,
                        case_index=case_index,
                        total_cases=total_cases,
                        strategy_index=strategy_index,
                        total_strategies=total_strategies,
                    )
                    case_result["historical_software_requirements_baseline_evaluation"] = evaluate_software_requirements_case(
                        case=case,
                        confirmed_iis_text=generated_iis_text,
                        candidate_label="historical_software_requirements_baseline",
                        candidate_software_requirements_text=historical_software_requirements_text,
                        app_config=strategy_app_config,
                    )
                    _mark_stage(
                        checkpoint,
                        stage_name="historical_software_requirements_baseline_evaluation",
                        status="completed",
                    )
                    _save_checkpoint(checkpoint_path, checkpoint)

                checkpoint["status"] = "completed"
                checkpoint["completed_at"] = _utc_now_iso()
                checkpoint["last_step"] = "completed"
                _save_checkpoint(checkpoint_path, checkpoint)

                results.append(case_result)
                _write_run_outputs(results)

                elapsed_seconds = time.perf_counter() - case_started_at
                _log_eval_progress(
                    step_label=f"finished case in {elapsed_seconds:.1f}s",
                    strategy_name=strategy_name,
                    case_id=case_id,
                    case_index=case_index,
                    total_cases=total_cases,
                    strategy_index=strategy_index,
                    total_strategies=total_strategies,
                )
            except Exception as exc:
                checkpoint["status"] = "failed"
                checkpoint["error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "last_step": checkpoint.get("last_step", ""),
                    "failed_at": _utc_now_iso(),
                }
                _save_checkpoint(checkpoint_path, checkpoint)
                _log_eval_progress(
                    step_label=f"failed at {checkpoint.get('last_step', 'unknown_step')}: {type(exc).__name__}: {exc}",
                    strategy_name=strategy_name,
                    case_id=case_id,
                    case_index=case_index,
                    total_cases=total_cases,
                    strategy_index=strategy_index,
                    total_strategies=total_strategies,
                )
                _log_checkpoint_path(checkpoint_path)
                if CONTINUE_AFTER_CASE_ERROR:
                    continue
                raise

    print("[EVAL] Aggregating results and writing output files", flush=True)
    _write_run_outputs(results)

    print(f"[EVAL] Wrote results to {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
