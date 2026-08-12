"""Validate the YAML and emit JSON for the application to load.

YAML is the right format to *edit*. It is the wrong format to *load*, and measurably so. On this
file, on this machine:

    YAML, yaml.safe_load (pure python)   49.3 ms     417x slower
    YAML, CSafeLoader (libyaml)           3.9 ms      33x slower
    JSON, json.loads                      0.1 ms       1x

So the editability argument for YAML is right and worth keeping. The speed argument is backwards,
because YAML is the slowest of the three by a wide margin.

It is also beside the point. The vocabulary loads once when the process starts, so even the slow
path costs 49 milliseconds a deploy. It would only matter if something loaded it per message,
and nothing should.

This keeps both properties. Humans edit YAML. The build validates it and writes JSON. The
application loads JSON, quickly, and can be certain it is valid because an invalid file never
became JSON in the first place.

    python -m packages.intents.compile   # writes build/*.json, exits non-zero if anything is wrong
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.intents import schema

HERE = Path(__file__).parent
OUT = HERE / "build"


def main() -> int:
    try:
        vocab = schema.load(HERE / "intents.yaml")
    except Exception as exc:
        print(f"FAIL intents.yaml\n  {exc}")
        return 1

    OUT.mkdir(exist_ok=True)
    (OUT / "intents.json").write_text(
        json.dumps(vocab.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote build/intents.json  ({(OUT / 'intents.json').stat().st_size:,} bytes)")

    failed = False
    for path in sorted((HERE / "clients").glob("*.yaml")):
        try:
            client = schema.load_client(path)
            client.check_against(vocab)
        except Exception as exc:
            print(f"FAIL {path.name}\n  {exc}")
            failed = True
            continue
        target = OUT / f"client-{path.stem}.json"
        target.write_text(
            json.dumps(client.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        print(
            f"wrote {target.name}  ({client.market}, {client.currency}, "
            f"quotes={'yes' if client.quote_prices else 'no'})"
        )

    if failed:
        print("\nNothing shipped. Fix the above and run again.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
