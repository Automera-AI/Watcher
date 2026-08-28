"""Import a clinic's availability workbook (demo step 4).

Operator-run, like ``scripts/import_property_facts.py``, and for the same reason: it needs
``openpyxl``, which nothing shipped imports and which is therefore not a project dependency.

    pip install openpyxl

    # read the file, validate it, write nothing
    python scripts/import_clinic_workbook.py \\
        docs/DermaClub_Availability_DEMO_2026-08-26_1.xlsx --timezone Africa/Cairo

    # …and then, once the report is clean, write it for one tenant
    DATABASE_URL=... python scripts/import_clinic_workbook.py \\
        docs/DermaClub_Availability_DEMO_2026-08-26_1.xlsx \\
        --timezone Africa/Cairo --tenant 0000-… --apply

The demo workbook is committed in ``docs/`` and
``apps/api/tests/test_clinic_workbook_integration.py`` runs this importer against it, so the
figures the client signed off are checked by the test suite rather than by re-reading a report.

Everything that decides whether the file is fit to import lives in ``apps/api/clinic/importer.py``,
where it is tested. This script does three things and no more: find the sheets, hand their rows
over, and print what came back.

``--timezone`` is required. The sheet holds a wall clock and names no zone; the tenant default is
a different city's, and a whole week of appointments an hour out is not something a demo notices
until it is quoting times to a patient.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

from apps.api.clinic.importer import (
    BRANCH_COLUMNS,
    BRANCH_REQUIRED,
    SERVICE_COLUMNS,
    SERVICE_REQUIRED,
    SLOT_COLUMNS,
    SLOT_REQUIRED,
    CataloguePlan,
    ColumnMap,
    ImportIssue,
    Row,
    plan_import,
    rows_from_grid,
)

#: The sheet each set of rows is read from, by the names a re-saved workbook plausibly uses.
SHEET_NAMES = {
    "branches": ("branches", "branch", "locations"),
    "services": ("services", "service", "catalogue", "catalog", "treatments"),
    "availability": ("availability", "slots", "diary", "schedule"),
}


def _find_sheet(workbook: Any, kind: str) -> Any:
    wanted = SHEET_NAMES[kind]
    for sheet in workbook.worksheets:
        if sheet.title.strip().casefold() in wanted:
            return sheet
    titles = ", ".join(sheet.title for sheet in workbook.worksheets)
    raise SystemExit(f"no {kind} sheet: looked for one of {wanted}, workbook has: {titles}")


def _read_sheet(
    workbook: Any, kind: str, columns: ColumnMap, required: Sequence[str]
) -> tuple[tuple[Row, ...], tuple[ImportIssue, ...]]:
    sheet = _find_sheet(workbook, kind)
    grid = list(sheet.iter_rows(values_only=True))
    if not grid:
        raise SystemExit(f"the {kind} sheet is empty")
    return rows_from_grid(grid[0], grid[1:], columns, required, sheet=kind, first_data_row=2)


def read_workbook(path: str, *, timezone: str, allow_overlaps: bool) -> CataloguePlan:
    import openpyxl  # local import: see the module docstring for why this is not a pinned dep

    workbook = openpyxl.load_workbook(path, data_only=True)
    branches, branch_issues = _read_sheet(workbook, "branches", BRANCH_COLUMNS, BRANCH_REQUIRED)
    services, service_issues = _read_sheet(workbook, "services", SERVICE_COLUMNS, SERVICE_REQUIRED)
    slots, slot_issues = _read_sheet(workbook, "availability", SLOT_COLUMNS, SLOT_REQUIRED)

    return plan_import(
        branches,
        services,
        slots,
        timezone=timezone,
        allow_overlaps=allow_overlaps,
        extra_issues=(*branch_issues, *service_issues, *slot_issues),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", help="path to the .xlsx")
    parser.add_argument(
        "--timezone",
        required=True,
        help="IANA zone the sheet's wall clock is in, e.g. Africa/Cairo",
    )
    parser.add_argument("--tenant", help="tenant UUID to write for; required with --apply")
    parser.add_argument(
        "--import-version",
        default=None,
        help="provenance stamped on every row (default: the workbook's filename)",
    )
    parser.add_argument(
        "--allow-overlaps",
        action="store_true",
        help="downgrade overlapping slots in one branch from an error to a warning",
    )
    parser.add_argument(
        "--apply", action="store_true", help="write to the database (default: report only)"
    )
    args = parser.parse_args()

    plan = read_workbook(args.workbook, timezone=args.timezone, allow_overlaps=args.allow_overlaps)
    report = plan.report

    for issue in report.issues:
        print(issue, file=sys.stderr)
    print(report.summary())
    print("slots by date:", ", ".join(f"{d}={n}" for d, n in report.slots_by_date.items()))
    print("slots by status:", ", ".join(f"{s}={n}" for s, n in report.slots_by_status.items()))
    if report.highest_reference:
        taken = ", ".join(f"{p}-{n:04d}" for p, n in sorted(report.highest_reference.items()))
        print("highest booking reference already taken:", taken)

    if not report.ok:
        raise SystemExit(f"{len(report.errors)} errors — nothing imported")
    if not args.apply:
        print("dry run: nothing written. Re-run with --tenant and --apply to import.")
        return
    if not args.tenant:
        raise SystemExit("--apply needs --tenant")

    # Imported here rather than at module scope so a dry run needs no database configuration.
    from apps.api.core.config import get_settings
    from apps.api.db.clinic_repo import SqlAlchemyClinicRepository
    from apps.api.db.engine import Database, create_db_engine, normalize_database_url

    settings = get_settings()
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is not set")
    engine = create_db_engine(normalize_database_url(settings.database_url.get_secret_value()))
    database = Database.from_engine(engine)
    repository = SqlAlchemyClinicRepository(database.tenant_session)

    outcome = repository.import_catalogue(
        args.tenant,
        plan,
        import_version=args.import_version or args.workbook.rsplit("/", 1)[-1],
    )
    print(outcome.summary())


if __name__ == "__main__":
    main()
