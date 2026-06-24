from __future__ import annotations

import csv
from pathlib import Path

from lead_intelligence.common.models import CompanyInput


def iter_factlink_companies(path: Path, *, limit: int | None = None):
    with path.open(newline="", encoding="utf-8-sig") as f:
        for idx, row in enumerate(csv.DictReader(f), start=1):
            if limit is not None and idx > limit:
                break
            yield CompanyInput(
                region="vietnam",
                source="factlink",
                source_id=(row.get("member_id") or row.get("company_name") or "").strip(),
                company_name=(row.get("company_name") or "").strip(),
                website=(row.get("website") or "").strip(),
                email=(row.get("email") or "").strip(),
                phone=(row.get("telephone") or "").strip(),
                representative_name=(row.get("representative_name") or "").strip(),
                description=(row.get("business_description") or row.get("produce") or "").strip(),
                industry=(row.get("category_names") or "").strip(),
                source_url=(row.get("factlink_url") or row.get("source_profile_url") or "").strip(),
                raw=row,
            )

