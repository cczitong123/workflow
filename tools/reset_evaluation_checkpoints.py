from __future__ import annotations

"""
Reset evaluation checkpoint results while preserving generated artifacts.

Edit the configuration block below, then run:

    python tools/reset_evaluation_checkpoints.py

Typical use for prompt-iteration:
- keep generation artifacts
- remove old evaluation outputs
- keep retrieval metrics if you only want to re-judge IIS/SR
- reset completed checkpoints so run_evaluation.py reuses generation and reruns evaluation
"""

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Configuration - edit these values directly before running.
OUTPUT_DIR = PROJECT_ROOT / "tools" / "evaluation_outputs"
ONLY_STATUS = "all"  # "all", "completed", "judge_failed", "generation_failed", ...
STRATEGY = ""  # Leave empty to process every strategy directory.
KEEP_RETRIEVAL_METRICS = True
DRY_RUN = False

CHECKPOINT_ROOT_NAME = "checkpoints"

CASE_RESULT_KEYS_TO_REMOVE = [
    "retrieval_metrics",
    "retrieval_path_breakdown",
    "iis_evaluation",
    "historical_iis_baseline_evaluation",
    "software_requirements_evaluation",
    "historical_software_requirements_baseline_evaluation",
]

STAGE_KEYS_TO_REMOVE = [
    "retrieval_metrics",
    "generated_iis_evaluation",
    "historical_iis_baseline_evaluation",
    "generated_software_requirements_evaluation",
    "historical_software_requirements_baseline_evaluation",
]

GENERATION_STAGE_KEYS = [
    "retrieval_intent",
    "evidence",
    "iis",
    "software_requirements",
    "load_pregenerated",
]


def _load_json(path: Path) -> dict[str, Any] | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _has_generation_artifacts(case_result: dict[str, Any]) -> bool:
    generation = case_result.get("generation", {})
    if not isinstance(generation, dict):
        return False
    return any(generation.get(key) for key in ("retrieval_intent", "evidence", "iis", "software_requirements"))


def _choose_restart_step(checkpoint: dict[str, Any], case_result: dict[str, Any]) -> str:
    stages = checkpoint.get("stages", {})
    generation = case_result.get("generation", {})

    if not isinstance(stages, dict):
        stages = {}
    if not isinstance(generation, dict):
        generation = {}

    if generation.get("software_requirements") or (
        isinstance(stages.get("software_requirements"), dict)
        and str(stages["software_requirements"].get("status", "")).strip() == "completed"
    ):
        return "retrieval_metrics"
    if generation.get("iis") or (
        isinstance(stages.get("iis"), dict)
        and str(stages["iis"].get("status", "")).strip() == "completed"
    ):
        return "software_requirements"
    if generation.get("evidence") or (
        isinstance(stages.get("evidence"), dict)
        and str(stages["evidence"].get("status", "")).strip() == "completed"
    ):
        return "iis"
    if generation.get("retrieval_intent") or (
        isinstance(stages.get("retrieval_intent"), dict)
        and str(stages["retrieval_intent"].get("status", "")).strip() == "completed"
    ):
        return "evidence"
    return "retrieval_intent"


def _reset_checkpoint(
    checkpoint: dict[str, Any],
    *,
    drop_retrieval_metrics: bool,
) -> tuple[bool, str]:
    case_result = checkpoint.get("case_result")
    if not isinstance(case_result, dict):
        return False, "missing_case_result"

    if not _has_generation_artifacts(case_result):
        return False, "missing_generation"

    removed_any = False
    for key in CASE_RESULT_KEYS_TO_REMOVE:
        if not drop_retrieval_metrics and key in {"retrieval_metrics", "retrieval_path_breakdown"}:
            continue
        if key in case_result:
            case_result.pop(key, None)
            removed_any = True

    stages = checkpoint.get("stages", {})
    if isinstance(stages, dict):
        for key in STAGE_KEYS_TO_REMOVE:
            if not drop_retrieval_metrics and key == "retrieval_metrics":
                continue
            if key in stages:
                stages.pop(key, None)
                removed_any = True

        for key in GENERATION_STAGE_KEYS:
            if key in stages and isinstance(stages[key], dict):
                stages[key]["status"] = "completed"

    checkpoint["status"] = "pending"
    checkpoint["completed_at"] = None
    checkpoint["error"] = None
    checkpoint["last_step"] = _choose_restart_step(checkpoint, case_result)
    return True, "reset" if removed_any else "status_only_reset"


def _iter_checkpoint_files(checkpoint_root: Path, strategy_filter: str | None) -> list[Path]:
    if not checkpoint_root.exists():
        return []
    paths: list[Path] = []
    for strategy_dir in sorted(checkpoint_root.iterdir()):
        if not strategy_dir.is_dir():
            continue
        if strategy_filter and strategy_dir.name != strategy_filter:
            continue
        paths.extend(sorted(strategy_dir.glob("*.json")))
    return paths


def main() -> None:
    checkpoint_root = OUTPUT_DIR / CHECKPOINT_ROOT_NAME
    checkpoint_files = _iter_checkpoint_files(checkpoint_root, STRATEGY.strip() or None)
    if not checkpoint_files:
        print(f"[RESET-EVAL] No checkpoint files found under {checkpoint_root}")
        return

    print(
        "[RESET-EVAL] Starting with config: "
        f"output_dir={OUTPUT_DIR} only_status={ONLY_STATUS} strategy={STRATEGY or 'ALL'} "
        f"keep_retrieval_metrics={KEEP_RETRIEVAL_METRICS} dry_run={DRY_RUN}",
        flush=True,
    )

    inspected = 0
    matched = 0
    reset = 0
    skipped = 0

    for path in checkpoint_files:
        inspected += 1
        checkpoint = _load_json(path)
        if checkpoint is None:
            skipped += 1
            print(f"[RESET-EVAL][skip] {path} reason=invalid_json", flush=True)
            continue

        status = str(checkpoint.get("status", "")).strip()
        if ONLY_STATUS != "all" and status != ONLY_STATUS:
            skipped += 1
            continue

        matched += 1
        changed, reason = _reset_checkpoint(
            checkpoint,
            drop_retrieval_metrics=not KEEP_RETRIEVAL_METRICS,
        )
        if not changed:
            skipped += 1
            print(f"[RESET-EVAL][skip] {path} reason={reason}", flush=True)
            continue

        if DRY_RUN:
            print(
                f"[RESET-EVAL][dry-run] would reset {path} "
                f"previous_status={status} restart_from={checkpoint.get('last_step')}",
                flush=True,
            )
        else:
            _write_json(path, checkpoint)
            print(
                f"[RESET-EVAL][reset] {path} "
                f"previous_status={status} restart_from={checkpoint.get('last_step')}",
                flush=True,
            )
        reset += 1

    print(
        f"[RESET-EVAL] inspected={inspected} matched={matched} reset={reset} skipped={skipped} "
        f"output_dir={OUTPUT_DIR}",
        flush=True,
    )


if __name__ == "__main__":
    main()
