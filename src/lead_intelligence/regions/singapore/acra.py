from __future__ import annotations

import csv
from pathlib import Path

from lead_intelligence.common.models import CompanyInput


ACRA_LIVE_FIELDNAMES = [
    "uen",
    "entity_name",
    "entity_status_description",
    "entity_type_description",
    "registration_incorporation_date",
    "uen_issue_date",
    "primary_ssic_code",
    "primary_ssic_description",
    "secondary_ssic_code",
    "secondary_ssic_description",
]


def is_live_status(status: str) -> bool:
    return (status or "").strip().casefold().startswith("live")


def iter_acra_files(external_dir: Path):
    for path in sorted(external_dir.glob("*.csv")):
        with path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                yield row


def write_live_acra_csv(external_dir: Path, output_csv: Path) -> tuple[int, int, int]:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    total = live = written = 0
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ACRA_LIVE_FIELDNAMES)
        writer.writeheader()
        for row in iter_acra_files(external_dir):
            total += 1
            if not is_live_status(row.get("entity_status_description", "")):
                continue
            live += 1
            out = {field: (row.get(field) or "").strip() for field in ACRA_LIVE_FIELDNAMES}
            key = out["uen"] or out["entity_name"].casefold()
            if not out["entity_name"] or key in seen:
                continue
            seen.add(key)
            writer.writerow(out)
            written += 1
    return total, live, written


def iter_company_inputs(path: Path, *, limit: int | None = None):
    with path.open(newline="", encoding="utf-8-sig") as f:
        for idx, row in enumerate(csv.DictReader(f), start=1):
            if limit is not None and idx > limit:
                break
            yield CompanyInput(
                region="singapore",
                source="acra",
                source_id=(row.get("uen") or row.get("entity_name") or "").strip(),
                company_name=(row.get("entity_name") or "").strip(),
                status=(row.get("entity_status_description") or "").strip(),
                industry=(row.get("primary_ssic_description") or "").strip(),
                raw=row,
            )

