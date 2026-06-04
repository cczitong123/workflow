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

import json

from evaluation_utils import (
    PROJECT_ROOT,
    apply_retrieval_strategy_override,
    aggregate_scores,
    evaluate_iis_case,
    evaluate_retrieval_case,
    evaluate_software_requirements_case,
    load_eval_config,
    load_json,
    render_markdown_summary,
    run_generation_pipeline,
    write_json,
    write_markdown,
)


# Configuration - edit these values directly before running.
DATASET_PATH = PROJECT_ROOT / "tools" / "evaluation_dataset.example.json"
OUTPUT_DIR = PROJECT_ROOT / "tools" / "evaluation_outputs"
RUN_GENERATION = True
EVALUATE_IIS = True
EVALUATE_SOFTWARE_REQUIREMENTS = True
CASE_IDS: list[str] = []
MAX_CASES: int | None = None
RETRIEVAL_STRATEGY_EXPERIMENTS = [
    {
        "name": "max_only",
        "strategy": "max_only",
    },
    {
        "name": "max_plus_second",
        "strategy": "max_plus_second",
        "alpha": 0.25,
    },
    {
        "name": "max_plus_log_count",
        "strategy": "max_plus_log_count",
        "beta": 0.05,
    },
    {
        "name": "max_plus_second_plus_log_count",
        "strategy": "max_plus_second_plus_log_count",
        "alpha": 0.25,
        "beta": 0.05,
    },
    {
        "name": "sum_all",
        "strategy": "sum_all",
    },
]


def _should_keep_case(case: dict[str, object]) -> bool:
    case_id = str(case.get("case_id", ""))
    if CASE_IDS and case_id not in CASE_IDS:
        return False
    return True


def main() -> None:
    app_config = load_eval_config()
    dataset = load_json(DATASET_PATH)
    if not isinstance(dataset, list):
        raise TypeError("Evaluation dataset must be a JSON array of case objects.")

    cases = [case for case in dataset if isinstance(case, dict) and _should_keep_case(case)]
    if MAX_CASES is not None:
        cases = cases[:MAX_CASES]

    results: list[dict[str, object]] = []

    for strategy_config in RETRIEVAL_STRATEGY_EXPERIMENTS:
        strategy_name = str(strategy_config.get("name") or strategy_config.get("strategy") or "unnamed")
        strategy = str(strategy_config.get("strategy", strategy_name))
        strategy_app_config = apply_retrieval_strategy_override(
            app_config,
            strategy=strategy,
            alpha=strategy_config.get("alpha"),
            beta=strategy_config.get("beta"),
            candidate_multiplier=strategy_config.get("candidate_multiplier"),
        )

        for case in cases:
            case_id = str(case.get("case_id", "unknown-case"))
            print(f"[EVAL] Running case {case_id} with strategy={strategy_name}", flush=True)

            if RUN_GENERATION:
                generation = run_generation_pipeline(str(case.get("description", "")), strategy_app_config)
            else:
                generation = {
                    "retrieval_intent": case.get("generated_retrieval_intent"),
                    "evidence": case.get("generated_evidence"),
                    "iis": case.get("generated_iis"),
                    "software_requirements": case.get("generated_software_requirements"),
                }

            generated_iis_text = str((generation.get("iis") or {}).get("raw_text", "")).strip()
            generated_software_requirements_text = str(
                (generation.get("software_requirements") or {}).get("raw_text", "")
            ).strip()

            case_result: dict[str, object] = {
                "case_id": case_id,
                "retrieval_strategy": strategy_name,
                "task_type": case.get("task_type", ""),
                "difficulty": case.get("difficulty", ""),
                "generation": generation,
                "retrieval_metrics": evaluate_retrieval_case(
                    case=case,
                    generation=generation,
                ),
            }

            if EVALUATE_IIS and generated_iis_text:
                case_result["iis_evaluation"] = evaluate_iis_case(
                    case=case,
                    generated_iis_text=generated_iis_text,
                    app_config=strategy_app_config,
                )

            if EVALUATE_SOFTWARE_REQUIREMENTS and generated_software_requirements_text:
                case_result["software_requirements_evaluation"] = evaluate_software_requirements_case(
                    case=case,
                    confirmed_iis_text=generated_iis_text,
                    generated_software_requirements_text=generated_software_requirements_text,
                    app_config=strategy_app_config,
                )

            results.append(case_result)

    aggregate = aggregate_scores(results)

    write_json(OUTPUT_DIR / "evaluation_results.json", results)
    write_json(OUTPUT_DIR / "evaluation_aggregate.json", aggregate)
    write_markdown(OUTPUT_DIR / "evaluation_summary.md", render_markdown_summary(results, aggregate))

    print(f"[EVAL] Wrote results to {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
