"""``python -m packages.eval`` entry point."""

from __future__ import annotations

import sys

from packages.eval.cli import main

if __name__ == "__main__":
    sys.exit(main())
