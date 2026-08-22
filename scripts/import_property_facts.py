"""Turn one row of a property-management export into knowledge-base facts (roadmap 2.4).

Not run by the application or by CI — a one-off, operator-run tool, the same category as
``docs/make_roadmap.py``. It needs ``openpyxl``, which is not a project dependency for the same
reason ``reportlab`` (that script's own extra) is not: nothing shipped imports it.

    pip install openpyxl
    python scripts/import_property_facts.py PROPERTY_DETAILS.xlsx "85 St Dunstans Road" \\
        > facts.json

The output is a JSON list of ``{"topic", "question", "answer", "sensitive"}`` objects — the same
shape ``apps/api/db/models.py``'s ``FactRow`` persists, and what
``apps/api/tests/fixtures/demo_property_facts.json`` is a curated, committed copy of (see
``apps/api/tests/test_knowledge_integration.py``).

**What this deliberately does not extract, and why.** The source sheet is a real operator export
and it mixes guest-facing information with two things that do not belong in a knowledge base at
all: staff-only operational notes (a cleaner's lockbox code, in a column literally named "Staff
Notes"), and the guest's own key/lockbox access (in "Key Collection Instructions"). The second one
is not a judgement call — ``intents.yaml`` forbids ``check_in_support`` from ever giving out "the
door code, the key box code, or the unit number" through ``answer_from_knowledge``, verified or
not (see ``core/knowledge.py``'s module docstring). Access belongs to ``access_code_request``,
which roadmap 3.1 has not built yet. ``COLUMN_MAP`` below only names columns this tool is safe to
answer from; extending it to a code or a key location is the mistake this comment exists to stop.

Every other column that is not guest-facing prose (image/video links, internal owner fields) is
left out simply because it is not an answerable fact, not because of a safety rule.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ColumnFact:
    """One column of the export, turned into one fact — or two, for ``wifi``."""

    topic: str
    question: str
    header: str
    sensitive: bool = False
    #: Rewrites the cell's raw text into an answer. Default: use it as-is.
    render: Any = None


def _identity(value: str) -> str:
    return value.strip()


def _yes_no(topic_true: str, topic_false: str) -> Any:
    def render(value: str) -> str:
        return topic_true if value.strip().lower() == "yes" else topic_false

    return render


#: Guest-facing columns this tool is safe to answer from. See the module docstring for what is
#: deliberately absent — most importantly, anything that names a code or a key location.
COLUMN_MAP: list[ColumnFact] = [
    ColumnFact("bedrooms", "how many bedrooms are there", "No. Bedrooms", render=_identity),
    ColumnFact("bathrooms", "how many bathrooms are there", "No. Bathrooms", render=_identity),
    ColumnFact(
        "garden",
        "is there a garden",
        "Garden",
        render=_yes_no("Yes, there is a garden.", "No, there is no garden."),
    ),
    ColumnFact("parking", "is there parking", "Parking instructions", render=_identity),
    ColumnFact(
        "accessibility",
        "are there stairs to reach the property",
        "Stairs/ WheelChair Access",
        render=_identity,
    ),
    ColumnFact(
        "dishwasher",
        "is there a dishwasher",
        "Dishwasher",
        render=_yes_no("Yes, there is a dishwasher.", "No, there is no dishwasher."),
    ),
    ColumnFact(
        "directions",
        "how do I get there by public transport",
        "Nearest Tube stations",
        render=_identity,
    ),
    ColumnFact(
        "rubbish", "where do I put the rubbish", "Rubbish/ Trash Location", render=_identity
    ),
    ColumnFact(
        "hospital",
        "where is the nearest hospital",
        "Nearest hospital (Accident & Emergency)",
        render=_identity,
    ),
    ColumnFact(
        "heating",
        "how does the heating and hot water work",
        "Heating, Boiler & Hot Water Instructions",
        render=_identity,
    ),
]

#: The wifi column is one cell holding both a network name and a password — split into a public
#: fact and a sensitive one rather than one fact carrying both, so a match on "what's the wifi
#: name" cannot hand over the password as a side effect of `best_match` picking the wrong side of
#: the same answer.
WIFI_COLUMN = "Wi-Fi Location/ Details"


def _split_wifi(cell: str) -> tuple[str | None, str | None]:
    username: str | None = None
    password: str | None = None
    for line in cell.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        label = label.strip().lower()
        if "username" in label or "network" in label:
            username = value.strip()
        elif "password" in label:
            password = value.strip()
    return username, password


def facts_for_row(headers: list[str], row: list[Any]) -> list[dict[str, Any]]:
    """The curated facts for one property row, in ``FactRow`` shape."""
    by_header = dict(zip(headers, row, strict=False))
    facts: list[dict[str, Any]] = []

    for col in COLUMN_MAP:
        raw = by_header.get(col.header)
        if raw in (None, ""):
            continue
        answer = col.render(str(raw))
        facts.append(
            {
                "topic": col.topic,
                "question": col.question,
                "answer": answer,
                "sensitive": col.sensitive,
            }
        )

    wifi_cell = by_header.get(WIFI_COLUMN)
    if wifi_cell:
        network, password = _split_wifi(str(wifi_cell))
        if network:
            facts.append(
                {
                    "topic": "wifi",
                    "question": "what's the wifi network name",
                    "answer": network,
                    "sensitive": False,
                }
            )
        if password:
            facts.append(
                {
                    "topic": "wifi",
                    "question": "what's the wifi password",
                    "answer": password,
                    "sensitive": True,
                }
            )

    return facts


def main() -> None:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} PROPERTY_DETAILS.xlsx 'address substring'", file=sys.stderr)
        raise SystemExit(2)

    import openpyxl  # local import: see the module docstring for why this is not a pinned dep

    path, needle = sys.argv[1], sys.argv[2].lower()
    workbook = openpyxl.load_workbook(path, data_only=True)
    sheet = workbook.active
    header_row = 3  # the export's header row; see scripts/import_property_facts.py's own tests
    headers = [c.value for c in sheet[header_row]]

    for row_cells in sheet.iter_rows(min_row=header_row + 1):
        row = [c.value for c in row_cells]
        name = row[0]
        if isinstance(name, str) and needle in name.lower():
            print(json.dumps(facts_for_row(headers, row), indent=2))
            return

    print(f"no property matched {needle!r}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
