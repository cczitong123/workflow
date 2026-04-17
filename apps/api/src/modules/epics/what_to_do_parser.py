from __future__ import annotations

import re

from modules.shared.models import FileChange, ParsedWhatToDo, Step


STEP_HEADER_RE = re.compile(r"^\s*(\d+)\.\s+\*\*(.+?)\*\*\s*:?\s*$")
INLINE_FILE_RE = re.compile(r"^\s*-\s*`([^`]+)`\s*[—-]\s*(.+?)\s*$")


def parse_what_to_do(text: str) -> ParsedWhatToDo:
    parsed = ParsedWhatToDo(raw_text=text or "")
    if not text or not text.strip():
        parsed.parse_notes.append("No whatToDo text provided.")
        return parsed

    lines = [line.rstrip() for line in text.splitlines()]
    current_step: Step | None = None
    in_files_section = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        lowered = line.lower().replace("*", "").rstrip(":").strip()
        if lowered == "what to do":
            in_files_section = False
            continue
        if "files to change" in lowered:
            in_files_section = True
            current_step = None
            continue

        step_match = STEP_HEADER_RE.match(raw_line)
        if step_match:
            condition = step_match.group(2).strip()
            current_step = Step(condition=condition, actions=[])
            parsed.steps.append(current_step)
            in_files_section = False
            continue

        if raw_line.lstrip().startswith("-"):
            content = raw_line.lstrip()[1:].strip()
            if in_files_section:
                file_match = INLINE_FILE_RE.match(raw_line)
                if file_match:
                    parsed.files_to_change.append(
                        FileChange(
                            path=file_match.group(1).strip(),
                            reason=file_match.group(2).strip(),
                        )
                    )
                else:
                    parsed.parse_notes.append(f"Unparsed file line: {raw_line}")
                continue

            if current_step is not None:
                action = content.strip("* ").strip()
                current_step.actions.append(action)
                continue

        parsed.parse_notes.append(f"Unparsed line: {raw_line}")

    if not parsed.steps:
        parsed.parse_notes.append("No structured steps detected.")
    return parsed
