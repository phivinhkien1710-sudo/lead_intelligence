#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from lead_intelligence.common.storage import initialize_database

DEFAULT_LEGACY_PATHS = [
    Path("/Users/phikien/singaporecompanies/legacy_workspace"),
    Path("/Users/phikien/kien/recordowl_project/data"),
]
DEFAULT_DB = PROJECT_ROOT / "databases" / "singapore_leads.db"
REGION = "singapore"
STATUS = "imported_legacy"
MISSING_FIELDS = ["website", "industry", "description", "hq_country", "representative_name", "email", "phone"]
LEAD_COLUMNS = [
    "region", "source", "source_id", "company_name", "normalized_company_name", "website", "domain",
    "email", "phone", "address", "hq_country", "representative_name", "position", "description",
    "industry", "source_url", "raw_source_path", "enrichment_status", "missing_fields_json", "confidence",
    "raw_json", "last_enriched_at", "inserted_at", "updated_at",
]
SKIP_CSV_NAMES = {
    "acra_live_companies_for_recordowl.csv",
    "acra_recordowl_lookup_attempts.csv",
    "recordowl_db_companies_for_leader_enrichment_20260619_122232.csv",
    "EntitiesRegisteredwithACRA.csv",
}


@dataclass(slots=True)
class ImportStats:
    source_files_found: int = 0
    total_source_rows_read: int = 0
    inserted: int = 0
    merged: int = 0
    skipped_exact_duplicates: int = 0
    skipped_missing_company_name: int = 0
    total_with_websites: int = 0
    total_missing_websites: int = 0
    total_missing_industry: int = 0
    total_missing_description: int = 0
    total_with_representative_contact_data: int = 0
    source_usage: Counter[str] = field(default_factory=Counter)


@dataclass(slots=True)
class SourceInfo:
    path: Path
    kind: str
    table: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_na(value: Any) -> str:
    text = clean_space(value)
    if text.lower() in {"", "na", "n/a", "none", "null", "-"}:
        return ""
    return text


def normalize_company_name(value: str) -> str:
    value = clean_na(value).casefold()
    value = re.sub(r"\b(pte|ltd|private|limited|llp|llc|inc|corp|corporation|co|company)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_website(value: Any) -> str:
    text = clean_na(value)
    if not text:
        return ""
    url_match = re.search(r"https?://[^\s,;]+", text)
    if url_match:
        text = url_match.group(0)
    elif ";" in text:
        text = text.split(";", 1)[0]
    elif "," in text:
        text = text.split(",", 1)[0]
    if not text.startswith(("http://", "https://")):
        text = f"https://{text}"
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if not parsed.netloc or any(char.isspace() for char in parsed.netloc):
        return ""
    return text.rstrip("/")


def domain_from_website(value: Any) -> str:
    website = normalize_website(value)
    if not website:
        return ""
    try:
        return (urlparse(website).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def normalize_email(value: Any) -> str:
    text = clean_na(value)
    emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    return "; ".join(dict.fromkeys(email.lower() for email in emails))


def normalize_phone(value: Any) -> str:
    text = clean_na(value)
    if not text:
        return ""
    text = re.sub(r"[^0-9+;()/ .-]+", " ", text)
    return clean_space(text)


def clean_person(value: Any) -> str:
    text = clean_na(value)
    text = re.sub(r"\b(Mr|Mrs|Ms|Miss|Dr)\.?\s+", "", text, flags=re.I)
    if any(bad in text.lower() for bad in ("http", "www.", "error:", "lorem ipsum")):
        return ""
    return clean_space(text)


def missing_fields(row: dict[str, Any]) -> list[str]:
    return [field for field in MISSING_FIELDS if not clean_na(row.get(field, ""))]


def score_completeness(row: dict[str, Any]) -> int:
    fields = ["website", "email", "phone", "address", "hq_country", "representative_name", "position", "description", "industry", "source_url"]
    return sum(1 for field in fields if clean_na(row.get(field, "")))


def finalize_lead(lead: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    lead["normalized_company_name"] = lead.get("normalized_company_name") or normalize_company_name(lead.get("company_name", ""))
    lead["domain"] = lead.get("domain") or domain_from_website(lead.get("website", ""))
    lead["missing_fields_json"] = json.dumps(missing_fields(lead), ensure_ascii=False)
    lead["last_enriched_at"] = now
    lead["inserted_at"] = now
    lead["updated_at"] = now
    return lead


def has_representative_or_contact(row: dict[str, Any]) -> bool:
    return bool(row.get("representative_name") or row.get("email") or row.get("phone") or row.get("position"))


def useful_sqlite_tables(path: Path) -> list[str]:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
    except Exception:
        return []
    return [table for table in ("recordowl_companies", "recordowl_leader_enrichment") if table in tables]


def csv_kind(path: Path, first_row: dict[str, Any]) -> str:
    fields = {field.strip().lower() for field in first_row.keys()}
    if path.name in SKIP_CSV_NAMES:
        return "skip"
    if "entity_status_description" in fields and "primary_ssic_description" in fields:
        return "skip"
    if {"entity_name", "matched_company", "website", "recordowl_url"}.issubset(fields):
        return "recordowl_website_csv"
    if {"entity_name", "decision_maker_name", "decision_maker_email"}.issubset(fields):
        return "old_enriched_csv"
    if {"company_name", "raw_leader_name", "leader_name", "leader_title"}.issubset(fields):
        return "recordowl_leader_csv"
    return "skip"


def discover_sources(paths: Iterable[Path]) -> list[SourceInfo]:
    seen: set[tuple[Path, str, str]] = set()
    sources: list[SourceInfo] = []
    for base in paths:
        if not base.exists():
            continue
        candidates = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in candidates:
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix == ".csv":
                try:
                    with path.open(newline="", encoding="utf-8-sig") as f:
                        first = next(csv.DictReader(f), None)
                    if not first or csv_kind(path, first) == "skip":
                        continue
                except Exception:
                    continue
                key = (path.resolve(), "csv", "")
                if key not in seen:
                    seen.add(key)
                    sources.append(SourceInfo(path=path.resolve(), kind="csv"))
            elif suffix in {".db", ".sqlite", ".sqlite3"}:
                for table in useful_sqlite_tables(path):
                    key = (path.resolve(), "sqlite", table)
                    if key not in seen:
                        seen.add(key)
                        sources.append(SourceInfo(path=path.resolve(), kind="sqlite", table=table))
    return sources


def adapt_recordowl_website(row: dict[str, Any], raw_source_path: str, source: str) -> dict[str, Any] | None:
    website = normalize_website(row.get("website"))
    if not website:
        return None
    lead = {
        "region": REGION,
        "source": source,
        "source_id": clean_na(row.get("uen")) or clean_na(row.get("matched_uen")) or clean_na(row.get("recordowl_url")) or clean_na(row.get("company_key")) or clean_na(row.get("company_name")) or clean_na(row.get("entity_name")),
        "company_name": clean_na(row.get("company_name")) or clean_na(row.get("matched_company")) or clean_na(row.get("entity_name")),
        "website": website,
        "domain": domain_from_website(website),
        "email": "",
        "phone": "",
        "address": "",
        "hq_country": "Singapore",
        "representative_name": "",
        "position": "",
        "description": "",
        "industry": "",
        "source_url": clean_na(row.get("recordowl_url")),
        "raw_source_path": raw_source_path,
        "enrichment_status": STATUS,
        "confidence": float(clean_na(row.get("match_confidence")) or 0.7) if re.fullmatch(r"\d+(\.\d+)?", clean_na(row.get("match_confidence"))) else 0.7,
        "raw_json": json.dumps(row, ensure_ascii=False),
    }
    return finalize_lead(lead)


def adapt_old_enriched(row: dict[str, Any], raw_source_path: str) -> dict[str, Any] | None:
    website = normalize_website(row.get("domain") or row.get("domain_source_url"))
    email = normalize_email(row.get("decision_maker_email"))
    person = clean_person(row.get("decision_maker_name"))
    if not website and not email and not person:
        return None
    lead = {
        "region": REGION,
        "source": "old_enriched_csv",
        "source_id": clean_na(row.get("entity_name")) or clean_na(row.get("domain")),
        "company_name": clean_na(row.get("entity_name")),
        "website": website,
        "domain": domain_from_website(website),
        "email": email,
        "phone": "",
        "address": "",
        "hq_country": "Singapore",
        "representative_name": person,
        "position": clean_na(row.get("decision_maker_position")),
        "description": "",
        "industry": "",
        "source_url": clean_na(row.get("domain_source_url")) or clean_na(row.get("decision_maker_source_url")),
        "raw_source_path": raw_source_path,
        "enrichment_status": STATUS,
        "confidence": 0.75,
        "raw_json": json.dumps(row, ensure_ascii=False),
    }
    return finalize_lead(lead)


def adapt_leader(row: dict[str, Any], raw_source_path: str, source: str) -> dict[str, Any] | None:
    website = normalize_website(row.get("website"))
    person = clean_person(row.get("leader_name") or row.get("raw_leader_name"))
    title = clean_na(row.get("leader_title"))
    if not website and not person and not title:
        return None
    lead = {
        "region": REGION,
        "source": source,
        "source_id": clean_na(row.get("uen")) or clean_na(row.get("recordowl_url")) or clean_na(row.get("company_key")) or clean_na(row.get("company_name")),
        "company_name": clean_na(row.get("company_name")),
        "website": website,
        "domain": domain_from_website(website),
        "email": "",
        "phone": "",
        "address": "",
        "hq_country": "Singapore",
        "representative_name": person,
        "position": title,
        "description": clean_na(row.get("leader_evidence")),
        "industry": "",
        "source_url": clean_na(row.get("leader_source_url")) or clean_na(row.get("recordowl_url")),
        "raw_source_path": raw_source_path,
        "enrichment_status": STATUS,
        "confidence": float(clean_na(row.get("leader_confidence")) or 0.6) if re.fullmatch(r"\d+(\.\d+)?", clean_na(row.get("leader_confidence"))) else 0.6,
        "raw_json": json.dumps(row, ensure_ascii=False),
    }
    return finalize_lead(lead)


def iter_source_rows(source: SourceInfo):
    if source.kind == "csv":
        with source.path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                kind = csv_kind(source.path, row)
                lead = None
                if kind == "recordowl_website_csv":
                    lead = adapt_recordowl_website(row, str(source.path), "recordowl_website_csv")
                elif kind == "old_enriched_csv":
                    lead = adapt_old_enriched(row, str(source.path))
                elif kind == "recordowl_leader_csv":
                    lead = adapt_leader(row, str(source.path), "recordowl_leader_csv")
                if lead:
                    yield lead
        return

    con = sqlite3.connect(f"file:{source.path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        for row in con.execute(f"SELECT * FROM {source.table}"):
            raw = dict(row)
            lead = None
            if source.table == "recordowl_companies":
                lead = adapt_recordowl_website(raw, f"{source.path}#{source.table}", "recordowl_db")
            elif source.table == "recordowl_leader_enrichment":
                lead = adapt_leader(raw, f"{source.path}#{source.table}", "recordowl_leader_db")
            if lead:
                yield lead
    finally:
        con.close()


def connect_target(path: Path) -> sqlite3.Connection:
    initialize_database(path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def load_existing_indexes(con: sqlite3.Connection) -> tuple[set[tuple[str, str, str]], dict[str, int], dict[str, int]]:
    exact: set[tuple[str, str, str]] = set()
    by_domain: dict[str, int] = {}
    by_name: dict[str, int] = {}
    for row in con.execute("SELECT id, region, source, source_id, domain, normalized_company_name FROM leads WHERE region = ?", (REGION,)):
        exact.add((row["region"], row["source"], row["source_id"]))
        if row["domain"]:
            by_domain.setdefault(row["domain"], row["id"])
        if row["normalized_company_name"]:
            by_name.setdefault(row["normalized_company_name"], row["id"])
    return exact, by_domain, by_name


def existing_row(con: sqlite3.Connection, row_id: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM leads WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"Existing row disappeared: {row_id}")
    return row


def score(row: dict[str, Any] | sqlite3.Row) -> int:
    return score_completeness(dict(row))


def merge_row(con: sqlite3.Connection, row_id: int, incoming: dict[str, Any], dry_run: bool) -> bool:
    current = existing_row(con, row_id)
    updates: dict[str, Any] = {}
    fillable = ["company_name", "normalized_company_name", "website", "domain", "email", "phone", "address", "hq_country", "representative_name", "position", "description", "industry", "source_url", "raw_source_path"]
    incoming_better = score(incoming) >= score(current)
    for field in fillable:
        incoming_value = clean_na(incoming.get(field))
        current_value = clean_na(current[field])
        if incoming_value and (not current_value or incoming_better):
            if incoming_value != current_value:
                updates[field] = incoming.get(field, "")
    merged_view = dict(current)
    merged_view.update({k: v for k, v in incoming.items() if clean_na(v)})
    updates["missing_fields_json"] = json.dumps(missing_fields(merged_view), ensure_ascii=False)
    updates["updated_at"] = utc_now()
    updates["last_enriched_at"] = incoming["last_enriched_at"]
    if len(updates) <= 3:
        return False
    if dry_run:
        return True
    assignments = ", ".join(f"{field} = ?" for field in updates)
    con.execute(f"UPDATE leads SET {assignments} WHERE id = ?", [*updates.values(), row_id])
    return True


def insert_row(con: sqlite3.Connection, row: dict[str, Any], dry_run: bool) -> int | None:
    if dry_run:
        return None
    placeholders = ", ".join("?" for _ in LEAD_COLUMNS)
    con.execute(f"INSERT INTO leads ({', '.join(LEAD_COLUMNS)}) VALUES ({placeholders})", [row.get(column, "") for column in LEAD_COLUMNS])
    return int(con.execute("SELECT last_insert_rowid()").fetchone()[0])


def update_stats(stats: ImportStats, row: dict[str, Any], source: SourceInfo) -> None:
    if row.get("website"):
        stats.total_with_websites += 1
    else:
        stats.total_missing_websites += 1
    if not row.get("industry"):
        stats.total_missing_industry += 1
    if not row.get("description"):
        stats.total_missing_description += 1
    if has_representative_or_contact_data(row):
        stats.total_with_representative_contact_data += 1
    label = str(source.path)
    if source.table:
        label += f"#{source.table}"
    stats.source_usage[label] += 1


def has_representative_or_contact_data(row: dict[str, Any]) -> bool:
    return bool(row.get("representative_name") or row.get("email") or row.get("phone") or row.get("position"))


def import_legacy(sources: list[SourceInfo], db_path: Path, dry_run: bool) -> ImportStats:
    stats = ImportStats(source_files_found=len({source.path for source in sources}))
    con = connect_target(db_path)
    exact, by_domain, by_name = load_existing_indexes(con)
    try:
        for source in sources:
            for row in iter_source_rows(source):
                stats.total_source_rows_read += 1
                if not row["company_name"]:
                    stats.skipped_missing_company_name += 1
                    continue
                update_stats(stats, row, source)
                exact_key = (row["region"], row["source"], row["source_id"])
                if exact_key in exact:
                    stats.skipped_exact_duplicates += 1
                    continue
                merge_id = None
                if row["domain"] and row["domain"] in by_domain:
                    merge_id = by_domain[row["domain"]]
                elif row["normalized_company_name"] and row["normalized_company_name"] in by_name:
                    merge_id = by_name[row["normalized_company_name"]]
                if merge_id is not None:
                    if dry_run and merge_id < 0:
                        stats.merged += 1
                    elif merge_row(con, merge_id, row, dry_run):
                        stats.merged += 1
                    else:
                        stats.skipped_exact_duplicates += 1
                    exact.add(exact_key)
                    continue
                new_id = insert_row(con, row, dry_run)
                stats.inserted += 1
                exact.add(exact_key)
                synthetic_id = new_id if new_id is not None else -(stats.inserted + stats.merged + stats.skipped_exact_duplicates)
                if row["domain"]:
                    by_domain.setdefault(row["domain"], synthetic_id)
                if row["normalized_company_name"]:
                    by_name.setdefault(row["normalized_company_name"], synthetic_id)
        if dry_run:
            con.rollback()
        else:
            con.commit()
    finally:
        con.close()
    return stats


def print_sources(sources: list[SourceInfo]) -> None:
    print("[sources]")
    for source in sources:
        label = f"{source.path}"
        if source.table:
            label += f"#{source.table}"
        print(f"- {source.kind}: {label}")


def print_summary(stats: ImportStats, dry_run: bool) -> None:
    print("\n[import summary]")
    print(f"mode: {'dry-run' if dry_run else 'real import'}")
    print(f"source files found: {stats.source_files_found}")
    print(f"total source rows read: {stats.total_source_rows_read}")
    print(f"total inserted: {stats.inserted}")
    print(f"total merged: {stats.merged}")
    print(f"total skipped exact duplicates: {stats.skipped_exact_duplicates}")
    print(f"total skipped because company_name missing: {stats.skipped_missing_company_name}")
    print(f"total with websites: {stats.total_with_websites}")
    print(f"total missing websites: {stats.total_missing_websites}")
    print(f"total missing industry: {stats.total_missing_industry}")
    print(f"total missing description: {stats.total_missing_description}")
    print(f"total with representative/contact data: {stats.total_with_representative_contact_data}")
    print("top 10 source files used:")
    for source, count in stats.source_usage.most_common(10):
        print(f"  {count}: {source}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely import Singapore legacy lead data into the new leads database.")
    parser.add_argument("--dry-run", action="store_true", help="Scan and deduplicate without writing to the database.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--legacy-path", action="append", type=Path, default=[], help="Additional legacy file/folder to scan.")
    args = parser.parse_args()

    paths = DEFAULT_LEGACY_PATHS + args.legacy_path
    sources = discover_sources(paths)
    print_sources(sources)
    stats = import_legacy(sources, args.db, args.dry_run)
    print_summary(stats, args.dry_run)


if __name__ == "__main__":
    main()
