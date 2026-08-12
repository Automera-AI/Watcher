"""``python -m packages.intents`` entry point — validate without writing anything.

    python -m packages.intents packages/intents/intents.yaml packages/intents/clients/*.yaml

Use ``python -m packages.intents.compile`` to validate *and* emit the JSON the application loads.
"""

from __future__ import annotations

import sys

from packages.intents.schema import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
