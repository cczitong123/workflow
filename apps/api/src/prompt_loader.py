from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
ENV_PREFIX = "AGENTIC_WORKFLOW"


def get_prompt_dir() -> Path:
    prompt_dir = os.getenv(f"{ENV_PREFIX}_PROMPT_DIR", str(DEFAULT_PROMPT_DIR))
    return Path(prompt_dir).expanduser().resolve()


def load_prompt_text(name: str) -> str:
    path = get_prompt_dir() / f"{name}.txt"
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **values: str) -> str:
    template = load_prompt_text(name)
    for key, value in values.items():
        template = template.replace(f"{{{key}}}", value)
    return template


def load_fewshot_examples(name: str) -> list[dict]:
    path = get_prompt_dir() / f"{name}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
