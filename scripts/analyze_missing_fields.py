#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from textwrap import shorten

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASES = {
    "singapore": PROJECT_ROOT / "databases" / "singapore_leads.db",
    "vietnam": PROJECT_ROOT / "databases" / "vietnam_leads.db",
}
IMPORTANT_FIELDS = ("website", "industry", "description", "hq_country")
ANALYZED_FIELDS = (
    "website",
    "domain",
    "industry",
    "description",
    "hq_country",
    "email",
    "phone",
    "representative_name",
)


def is_present_sql(field: str) -> str:
    return f"{field} IS NOT NULL AND TRIM({field}) != ''"


def count_where(conn: sqlite3.Connection, where: str = "1=1") -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM leads WHERE {where}").fetchone()[0])


def connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def field_counts(conn: sqlite3.Connection, field: str) -> tuple[int, int]:
    with_value = count_where(conn, is_present_sql(field))
    missing = count_where(conn, f"NOT ({is_present_sql(field)})")
    return with_value, missing


def important_missing_clause() -> str:
    return " OR ".join(f"NOT ({is_present_sql(field)})" for field in IMPORTANT_FIELDS)


def eligibility_clause() -> str:
    return f"({is_present_sql('company_name')}) AND ({important_missing_clause()})"


def priority_clauses() -> dict[str, str]:
    has_website = is_present_sql("website")
    has_company = is_present_sql("company_name")
    has_context = f"({is_present_sql('hq_country')} OR {is_present_sql('region')})"
    missing_industry_or_description = f"(NOT ({is_present_sql('industry')}) OR NOT ({is_present_sql('description')}))"
    missing_most = " AND ".join(f"NOT ({is_present_sql(field)})" for field in IMPORTANT_FIELDS)
    return {
        "Priority 1: has website but missing industry or description": f"{has_website} AND {missing_industry_or_description}",
        "Priority 2: no website but has company_name and hq_country/region context": f"NOT ({has_website}) AND {has_company} AND {has_context}",
        "Priority 3: missing most useful fields": missing_most,
    }


def print_line(label: str, value: int) -> None:
    print(f"  {label:<48} {value:>10}")


def print_section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def analyze_region(region: str, db_path: Path) -> None:
    print("=" * 78)
    print(f"{region.upper()} LEAD MISSING-FIELD ANALYSIS")
    print(f"Database: {db_path}")
    print("=" * 78)

    if not db_path.exists():
        print("Database file not found.")
        return

    with connect_readonly(db_path) as conn:
        total = count_where(conn)
        print_section("Coverage")
        print_line("total leads", total)
        for field in ANALYZED_FIELDS:
            with_value, missing = field_counts(conn, field)
            print_line(f"leads with {field}", with_value)
            print_line(f"leads missing {field}", missing)

        eligible_clause = eligibility_clause()
        eligible_total = count_where(conn, eligible_clause)
        eligible_with_website = count_where(conn, f"({eligible_clause}) AND ({is_present_sql('website')})")
        eligible_without_website = count_where(conn, f"({eligible_clause}) AND NOT ({is_present_sql('website')})")

        print_section("Fallback Enrichment Eligibility")
        print_line("total eligible for fallback enrichment", eligible_total)
        print_line("eligible leads with website", eligible_with_website)
        print_line("eligible leads without website", eligible_without_website)

        print_section("Priority Buckets")
        for label, clause in priority_clauses().items():
            print_line(label, count_where(conn, clause))

        print_section("Top 20 Eligible Samples")
        rows = conn.execute(
            f"""
            SELECT id, company_name, website, industry, description, missing_fields_json
            FROM leads
            WHERE {eligible_clause}
            ORDER BY
                CASE WHEN {is_present_sql('website')} THEN 0 ELSE 1 END,
                id
            LIMIT 20
            """
        ).fetchall()
        if not rows:
            print("  No eligible rows found.")
            return
        for row in rows:
            description = shorten((row["description"] or "").replace("\n", " "), width=80, placeholder="...")
            missing = row["missing_fields_json"] or "[]"
            try:
                missing = json.dumps(json.loads(missing), ensure_ascii=False)
            except json.JSONDecodeError:
                pass
            print(f"  id: {row['id']}")
            print(f"    company_name: {row['company_name'] or ''}")
            print(f"    website: {row['website'] or ''}")
            print(f"    industry: {row['industry'] or ''}")
            print(f"    description: {description}")
            print(f"    missing_fields_json: {missing}")


def selected_regions(value: str) -> list[str]:
    if value == "all":
        return ["singapore", "vietnam"]
    return [value]


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze missing fields in regional lead databases. Read-only.")
    parser.add_argument("--region", choices=["singapore", "vietnam", "all"], required=True)
    args = parser.parse_args()

    for region in selected_regions(args.region):
        analyze_region(region, DATABASES[region])
        print()


if __name__ == "__main__":
    main()
