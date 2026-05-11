from __future__ import annotations

"""
Export a session-level trace pack as JSON.

This script is useful when a consolidated audit-style snapshot is needed for a
single session, including timeline events, retrieval versions, evidence, draft
versions, and the current final state.

Edit the configuration block below, then run:
python tools/export_trace_pack.py
"""

from pathlib import Path

from export_utils import (
    DEFAULT_DATABASE_PATH,
    DEFAULT_OUTPUT_DIR,
    build_trace_pack,
    ensure_output_dir,
    open_connection,
    write_json,
)


# -----------------------------------------------------------------------------
# Configuration
# Edit only the values in this block, then run:
# python tools/export_trace_pack.py
# -----------------------------------------------------------------------------
DATABASE_PATH = DEFAULT_DATABASE_PATH
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
SESSION_ID = "sess-001"
OUTPUT_FILENAME = f"{SESSION_ID}.trace-pack.json"


def main() -> None:
    output_dir = ensure_output_dir(Path(OUTPUT_DIR))
    output_path = output_dir / OUTPUT_FILENAME

    with open_connection(Path(DATABASE_PATH)) as connection:
        payload = build_trace_pack(connection, SESSION_ID)

    write_json(output_path, payload)
    print(f"Trace pack exported to: {output_path}")


if __name__ == "__main__":
    main()
