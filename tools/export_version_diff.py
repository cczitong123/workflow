from __future__ import annotations

"""
Export a JSON diff between two draft version numbers in one session.

This script is useful when draft evolution needs to be inspected between two
known version numbers, especially for demo-stage comparison of refine and
restore flows.

Edit the configuration block below, then run:
python tools/export_version_diff.py
"""

from pathlib import Path

from export_utils import (
    DEFAULT_DATABASE_PATH,
    DEFAULT_OUTPUT_DIR,
    build_draft_diff,
    ensure_output_dir,
    load_draft_version_by_number,
    open_connection,
    write_json,
)


# -----------------------------------------------------------------------------
# Configuration
# Edit only the values in this block, then run:
# python tools/export_version_diff.py
# -----------------------------------------------------------------------------
DATABASE_PATH = DEFAULT_DATABASE_PATH
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
SESSION_ID = "sess-001"
FROM_VERSION_NUMBER = 1
TO_VERSION_NUMBER = 2
OUTPUT_FILENAME = f"{SESSION_ID}.v{FROM_VERSION_NUMBER}-v{TO_VERSION_NUMBER}.diff.json"


def main() -> None:
    output_dir = ensure_output_dir(Path(OUTPUT_DIR))
    output_path = output_dir / OUTPUT_FILENAME

    with open_connection(Path(DATABASE_PATH)) as connection:
        earlier = load_draft_version_by_number(connection, SESSION_ID, FROM_VERSION_NUMBER)
        later = load_draft_version_by_number(connection, SESSION_ID, TO_VERSION_NUMBER)
        payload = build_draft_diff(earlier, later)

    write_json(output_path, payload)
    print(f"Version diff exported to: {output_path}")


if __name__ == "__main__":
    main()
