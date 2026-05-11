from __future__ import annotations

"""
Export one session as a timeline-oriented JSON snapshot.

This script is useful when the full local workflow for a single session needs
to be inspected in one file, including:
- session metadata
- original input description
- retrieval versions
- evidence snapshots
- draft versions
- user/system events

Edit the configuration block below, then run:
python tools/export_session_timeline.py
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
# python tools/export_session_timeline.py
# -----------------------------------------------------------------------------
DATABASE_PATH = DEFAULT_DATABASE_PATH
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
SESSION_ID = "sess-001"
OUTPUT_FILENAME = f"{SESSION_ID}.timeline.json"


def main() -> None:
    output_dir = ensure_output_dir(Path(OUTPUT_DIR))
    output_path = output_dir / OUTPUT_FILENAME

    with open_connection(Path(DATABASE_PATH)) as connection:
        payload = build_trace_pack(connection, SESSION_ID)

    write_json(output_path, payload)
    print(f"Session timeline exported to: {output_path}")


if __name__ == "__main__":
    main()
