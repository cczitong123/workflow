from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
API_SRC = PROJECT_ROOT / "apps" / "api" / "src"
sys.path.insert(0, str(API_SRC))

from server import run  # noqa: E402


if __name__ == "__main__":
    run()
