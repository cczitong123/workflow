from __future__ import annotations

"""
Export one retrieval version from SQLite as a standalone JSON file.

This script is useful when the generated retrieval intent, retrieval query,
and linked code evidence for a single retrieval run need to be inspected.

Edit the configuration block below, then run:
python tools/export_retrieval_version.py
"""

from pathlib import Path

from export_utils import (
    DEFAULT_DATABASE_PATH,
    DEFAULT_OUTPUT_DIR,
    ensure_output_dir,
    load_single_retrieval_version,
    open_connection,
    write_json,
)


# -----------------------------------------------------------------------------
# Configuration
# Edit only the values in this block, then run:
# python tools/export_retrieval_version.py
# -----------------------------------------------------------------------------
DATABASE_PATH = DEFAULT_DATABASE_PATH
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
RETRIEVAL_VERSION_ID = 1
OUTPUT_FILENAME = f"retrieval-version-{RETRIEVAL_VERSION_ID}.json"


def main() -> None:
    output_dir = ensure_output_dir(Path(OUTPUT_DIR))
    output_path = output_dir / OUTPUT_FILENAME

    with open_connection(Path(DATABASE_PATH)) as connection:
        payload = load_single_retrieval_version(connection, RETRIEVAL_VERSION_ID)

    write_json(output_path, payload)
    print(f"Retrieval version exported to: {output_path}")


if __name__ == "__main__":
    main()
