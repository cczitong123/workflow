from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def get_prompt_dir() -> Path:
    return Path(os.getenv("BMWCODE_PROMPT_DIR", str(DEFAULT_PROMPT_DIR))).expanduser().resolve()


def load_prompt_text(name: str) -> str:
    path = get_prompt_dir() / f"{name}.txt"
    return path.read_text(encoding="utf-8")


def load_fewshot_examples(name: str) -> list[dict]:
    path = get_prompt_dir() / f"{name}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
