"""Does what a patient types reach exactly one service and one branch? (demo step 10 prep)

Operator-run, alongside ``scripts/import_clinic_workbook.py`` and for the same reason: it reads an
``.xlsx`` with ``openpyxl``, which nothing shipped imports.

    # what the workbook resolves today
    python scripts/check_alias_resolution.py \\
        docs/DermaClub_Availability_DEMO_2026-08-26_1.xlsx --timezone Africa/Cairo

    # …and what it would resolve with a proposed alias column laid over it
    python scripts/check_alias_resolution.py \\
        docs/DermaClub_Availability_DEMO_2026-08-26_1.xlsx --timezone Africa/Cairo \\
        --draft docs/dermaclub-aliases-draft.csv

The importer already refuses a workbook whose names collide, and that check runs here too by virtue
of running the importer. What it does *not* answer is the question this script exists for: a
catalogue can import perfectly cleanly and still leave "عايزة أحجز فاشيال في المعادي" reaching no
service and no branch, because resolving a patient's words is ``clinic/catalogue.py``'s job and it
happens a turn later, in front of the patient.

So this runs the real resolver over the real catalogue and prints one line per phrase: the single
thing it found, the several it could not choose between (which is a clarifying question the
receptionist will ask, correctly, and which costs a turn), or nothing at all (which is the failure
that has no recovery in a two-turn budget). Run it before the demo, and run it again on whatever
workbook the client sends back.

``--draft`` overlays a proposed alias column without touching the client's file: the draft is a
review artefact until the clinic puts it in their own workbook (decision 12), and nothing here
writes to the workbook it was given. ``--write-sheet`` emits the draft as a two-sheet ``.xlsx``
the clinic can paste from, which is the only file this script ever writes.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from apps.api.clinic.catalogue import resolve_branch, resolve_service
from apps.api.clinic.importer import CataloguePlan
from apps.api.core.clinic import Branch, Service, normalise_service_name

# A sibling script, not a package module: ``scripts/`` is a directory of operator entry points
# and running one puts this directory on the path. Imported rather than copied because reading
# the workbook — finding the sheets, folding the headers — is that script's job and there is no
# second version of it worth maintaining.
from import_clinic_workbook import read_workbook

#: The demo's own words, in the order the scripted journey says them, plus the phrases a client
#: probing the number is most likely to try. A phrase file (``--phrases``) replaces this list.
DEMO_PHRASES: tuple[tuple[str, str], ...] = (
    ("service", "فاشيال"),
    ("service", "الفاشيال"),
    ("service", "فاشيال بيسك"),
    ("service", "ليزر"),
    ("service", "بوتوكس"),
    ("service", "فيلر"),
    ("service", "برايم ليز 6 جلسات"),
    ("service", "تقشير"),
    ("service", "سيلوليت"),
    # The four the clinic named after the first review, and the phrase it gave to two of them.
    ("service", "حقن ترطيب ونضارة البشرة"),
    ("service", "باقة متابعة الليزر 4 جلسات"),
    ("service", "متابعة 4 جلسات فل بودي"),
    ("service", "ليزر 12 جلسة نص الجسم"),
    ("service", "هاف بودي"),
    ("service", "Basic Facial"),
    ("branch", "المعادي"),
    ("branch", "معادي"),
    ("branch", "التجمع"),
    ("branch", "مدينة نصر"),
    ("branch", "مصر الجديدة"),
    ("branch", "الشيخ زايد"),
    ("branch", "6 أكتوبر"),
    ("branch", "Maadi"),
)


def _load_draft(path: Path) -> dict[str, tuple[str, ...]]:
    """The proposed aliases, by branch id / service code. Blank rows carry none by design."""
    proposed: dict[str, tuple[str, ...]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            aliases = tuple(a.strip() for a in (row.get("aliases") or "").split("|") if a.strip())
            if aliases:
                proposed[row["id"].strip()] = aliases
    return proposed


def _overlay(plan: CataloguePlan, proposed: dict[str, tuple[str, ...]]) -> CataloguePlan:
    """The same catalogue with the proposed aliases added. The workbook itself is not touched."""
    branches = tuple(
        replace(b, aliases=(*b.aliases, *proposed.get(b.external_id, ()))) for b in plan.branches
    )
    services = tuple(
        replace(s, aliases=(*s.aliases, *proposed.get(s.code, ()))) for s in plan.services
    )
    return replace(plan, branches=branches, services=services)


def _collisions(branches: Sequence[Branch], services: Sequence[Service]) -> list[str]:
    """A name or alias reaching two catalogue rows — the thing the import refuses on.

    Reported here as well as there because a draft is checked before anyone edits the workbook,
    and finding out from a failed import on demo morning is finding out too late.
    """
    found: list[str] = []
    for label, rows in (("service", services), ("branch", branches)):
        reached: dict[str, list[str]] = {}
        for row in rows:
            key = row.code if isinstance(row, Service) else row.external_id
            for name in row.names:
                reached.setdefault(normalise_service_name(name), []).append(key)
        for name, keys in sorted(reached.items()):
            if len(set(keys)) > 1:
                found.append(f"{label} name {name!r} reaches {', '.join(sorted(set(keys)))}")
            elif len(keys) > 1:
                # Two spellings on one row that fold to the same string — a hamza variant, a ة
                # written as ه. Harmless in intent and still an error to the importer, which
                # counts names rather than distinct rows.
                found.append(f"{label} {keys[0]} lists {name!r} twice (two spellings, one form)")
    return found


def _write_sheet(path: Path, draft: Path) -> None:
    """Emit the draft as the two columns the clinic pastes into their own workbook.

    Keyed by Branch ID and Service ID rather than by name, because the row a value belongs to has
    to survive a catalogue that gets re-sorted between now and the demo. The rows with no proposed
    alias are written empty rather than omitted: a blank cell next to a service is a question the
    clinic can answer, and a missing row is one nobody sees.
    """
    import openpyxl  # local import: see the module docstring

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    # The client's own header names, so a pasted column lands next to the row it belongs to.
    for sheet_name, id_header, name_header in (
        ("Branches", "Branch ID", "Branch"),
        ("Services", "ID", "Service"),
    ):
        sheet = workbook.create_sheet(sheet_name)
        sheet.append([id_header, name_header, "Aliases", "Status", "Note"])
        sheet.sheet_view.rightToLeft = True
        with draft.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row["sheet"] != sheet_name:
                    continue
                sheet.append(
                    [row["id"], row["name"], row["aliases"], row["status"], row.get("note", "")]
                )
        for column, width in zip("ABCDE", (12, 42, 52, 18, 70), strict=True):
            sheet.column_dimensions[column].width = width
    workbook.save(path)
    print(f"wrote {path}")


def _phrases(path: Path | None) -> tuple[tuple[str, str], ...]:
    if path is None:
        return DEMO_PHRASES
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    parsed: list[tuple[str, str]] = []
    for line in lines:
        if not line or line.startswith("#"):
            continue
        kind, _, phrase = line.partition("\t") if "\t" in line else line.partition(" ")
        if kind not in ("service", "branch"):
            raise SystemExit(f"phrase lines start with 'service' or 'branch': {line!r}")
        parsed.append((kind, phrase.strip()))
    return tuple(parsed)


def _report(plan: CataloguePlan, phrases: Sequence[tuple[str, str]]) -> int:
    """One line per phrase. Returns the number that reached nothing."""
    missing = 0
    ambiguous = 0
    for kind, phrase in phrases:
        if kind == "service":
            match = resolve_service(phrase, plan.services)
            names = tuple(f"{s.code} {s.name}" for s in match.candidates)
            single = f"{match.found.code} {match.found.name}" if match.found else None
        else:
            branch = resolve_branch(phrase, plan.branches)
            names = tuple(f"{b.external_id} {b.name}" for b in branch.candidates)
            single = f"{branch.found.external_id} {branch.found.name}" if branch.found else None

        if single is not None:
            print(f"  ok        {kind:<8} {phrase!r} → {single}")
        elif names:
            ambiguous += 1
            print(f"  asks      {kind:<8} {phrase!r} → {len(names)}: {', '.join(names)}")
        else:
            missing += 1
            print(f"  NOTHING   {kind:<8} {phrase!r} → no match")

    print(
        f"\n{len(phrases)} phrases: {len(phrases) - missing - ambiguous} resolved, "
        f"{ambiguous} ask a clarifying question, {missing} reach nothing"
    )
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", help="path to the .xlsx")
    parser.add_argument("--timezone", required=True, help="IANA zone, e.g. Africa/Cairo")
    parser.add_argument("--draft", type=Path, default=None, help="proposed alias CSV to overlay")
    parser.add_argument("--phrases", type=Path, default=None, help="phrase file, one per line")
    parser.add_argument(
        "--write-sheet",
        type=Path,
        default=None,
        help="write the --draft out as a paste-ready two-sheet .xlsx",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="exit 0 even when a phrase reaches nothing (reporting, not gating)",
    )
    args = parser.parse_args()

    plan = read_workbook(args.workbook, timezone=args.timezone, allow_overlaps=False)
    if not plan.report.ok:
        for issue in plan.report.errors:
            print(issue, file=sys.stderr)
        raise SystemExit(f"{len(plan.report.errors)} import errors — the catalogue is not usable")

    if args.draft is not None:
        proposed = _load_draft(args.draft)
        plan = _overlay(plan, proposed)
        print(
            f"overlaid {sum(len(v) for v in proposed.values())} proposed aliases "
            f"over {len(proposed)} rows from {args.draft}\n"
        )

    if collisions := _collisions(plan.branches, plan.services):
        for collision in collisions:
            print(f"  COLLISION {collision}", file=sys.stderr)
        raise SystemExit(f"{len(collisions)} collisions — this catalogue would fail to import")

    if args.write_sheet is not None:
        if args.draft is None:
            raise SystemExit("--write-sheet needs --draft: there is nothing else to write")
        _write_sheet(args.write_sheet, args.draft)

    missing = _report(plan, _phrases(args.phrases))
    return 0 if args.allow_missing or missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
