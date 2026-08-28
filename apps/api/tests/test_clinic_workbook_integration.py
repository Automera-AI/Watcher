"""The importer against the demo workbook committed in ``docs/`` (demo step 4).

Every other clinic test uses invented rows, which is right: they are about what the importer
*decides*. This one is about the file the demo actually imports. It is the difference between "the
column map handles a header spelled like that" and "the column map handles the file on disk".

It is also why the workbook lives in the repository. Reading it here means the figures the client
signed off in the source-data review are re-checked by ``pytest`` rather than by someone
re-uploading a spreadsheet and reading a report by eye.

Skipped when ``openpyxl`` is absent — it is an operator's dependency, not the application's
(``scripts/import_clinic_workbook.py``), and CI does not install it. The numbers below are from the
review: 14 branches, 35 services, 672 slots, 407 open / 265 booked, 62 back-to-back pairs, no
overlaps, no unresolved service name, Friday 4 September absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.api.clinic.importer import CataloguePlan

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKBOOK = REPO_ROOT / "docs" / "DermaClub_Availability_DEMO_2026-08-26_1.xlsx"

pytestmark = [
    pytest.mark.skipif(not WORKBOOK.exists(), reason=f"{WORKBOOK.name} is not in docs/"),
]


@pytest.fixture(scope="module")
def plan() -> CataloguePlan:
    pytest.importorskip("openpyxl", reason="operator dependency; see scripts/")
    from scripts.import_clinic_workbook import read_workbook

    return read_workbook(str(WORKBOOK), timezone="Africa/Cairo", allow_overlaps=False)


def test_the_demo_workbook_imports_without_errors(plan: CataloguePlan) -> None:
    report = plan.report
    assert report.ok, "\n".join(str(issue) for issue in report.errors)


def test_the_counts_the_client_signed_off(plan: CataloguePlan) -> None:
    report = plan.report
    assert (report.branches, report.services, report.slots, report.bookings) == (14, 35, 672, 265)
    assert report.slots_by_status == {"booked": 265, "open": 407}
    assert report.skipped_retail == 0  # retail was removed from the catalogue (decision 6)


def test_the_diary_is_the_demo_week_with_friday_closed(plan: CataloguePlan) -> None:
    dates = plan.report.slots_by_date
    assert set(dates) == {
        "2026-08-31",
        "2026-09-01",
        "2026-09-02",
        "2026-09-03",
        "2026-09-05",
        "2026-09-06",
    }
    assert set(dates.values()) == {112}


def test_the_sixty_two_back_to_back_pairs_survive_the_import(plan: CataloguePlan) -> None:
    """Decision 3. A number that has moved means the diary's shape changed."""
    assert plan.report.back_to_back_pairs == 62
    assert plan.report.overlapping_pairs == 0


def test_every_availability_row_resolves_to_a_catalogue_service(plan: CataloguePlan) -> None:
    codes = {service.code for service in plan.services}
    assert {slot.service_code for slot in plan.slots} <= codes
    assert len({slot.external_id for slot in plan.slots}) == 672


def test_the_branch_provenance_is_five_real_four_given_five_placeholder(
    plan: CataloguePlan,
) -> None:
    """Decision 5. The Read Me's "nine are placeholders" folds the four given in; the flag
    follows the column's literal value."""
    branches = plan.branches
    assert len(branches) == 14
    assert sum(branch.placeholder for branch in branches) == 5


def test_the_six_session_package_is_priced_above_the_single_session(plan: CataloguePlan) -> None:
    """Decision 4. The correction is conditional; this workbook already holds 15,000."""
    prices = {service.code: service.price_minor for service in plan.services}
    assert prices["DT028"] == 310_000
    assert prices["DT029"] == 1_500_000
    assert prices["DT030"] == 1_635_000


def test_the_highest_booking_reference_step_six_must_issue_after(plan: CataloguePlan) -> None:
    assert plan.report.highest_reference == {"DC": 265}
