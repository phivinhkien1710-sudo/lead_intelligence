#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from lead_intelligence.common.storage import initialize_database

DEFAULT_LEGACY_PATHS = [
    Path("/Users/phikien/kien/kienngu123/data"),
    Path("/Users/phikien/kien/kienngu123"),
]
DEFAULT_DB = PROJECT_ROOT / "databases" / "vietnam_leads.db"
REGION = "vietnam"
STATUS = "imported_legacy"
MISSING_FIELDS = [
    "website",
    "industry",
    "description",
    "hq_country",
    "representative_name",
    "email",
    "phone",
]
LEAD_COLUMNS = [
    "region",
    "source",
    "source_id",
    "company_name",
    "normalized_company_name",
    "website",
    "domain",
    "email",
    "phone",
    "address",
    "hq_country",
    "representative_name",
    "position",
    "description",
    "industry",
    "source_url",
    "raw_source_path",
    "enrichment_status",
    "missing_fields_json",
    "confidence",
    "raw_json",
    "last_enriched_at",
    "inserted_at",
    "updated_at",
]


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
    total_missing_hq_country: int = 0


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
    value = re.sub(r"\b(pte|ltd|limited|llp|llc|inc|corp|corporation|co|company|co ltd|coltd)\b", " ", value)
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
    if not text:
        return ""
    emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    return "; ".join(dict.fromkeys(email.lower() for email in emails))


def normalize_phone(value: Any) -> str:
    text = clean_na(value)
    if not text:
        return ""
    text = re.sub(r"[^0-9+;()/ .-]+", " ", text)
    return clean_space(text)


def clean_representative(value: Any) -> str:
    text = clean_na(value)
    text = re.sub(r"^(managing director|director|president|general director|representative|owner)\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"\b(Mr|Mrs|Ms|Miss|Dr)\.?\s+", "", text, flags=re.I)
    return clean_space(text)


def missing_fields(row: dict[str, Any]) -> list[str]:
    return [field for field in MISSING_FIELDS if not clean_na(row.get(field, ""))]


def score_completeness(row: dict[str, Any]) -> int:
    fields = ["website", "email", "phone", "address", "hq_country", "representative_name", "position", "description", "industry", "source_url"]
    return sum(1 for field in fields if clean_na(row.get(field, "")))


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


def useful_sqlite_tables(path: Path) -> list[str]:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
    except Exception:
        return []
    return [table for table in ("factlink_companies", "companies") if table in tables]


def csv_source_name(path: Path, row: dict[str, Any]) -> str:
    fields = {field.strip().lower() for field in row.keys()}
    if "member_id" in fields and "factlink_url" in fields:
        return "factlink_csv"
    if "company" in fields and "company website" in fields:
        return "company_lookup_csv"
    if "company" in fields and "tax id" in fields:
        return "v1000_csv"
    return f"csv:{path.stem}"


def adapt_factlink(row: dict[str, Any], raw_source_path: str, source: str = "factlink") -> dict[str, Any]:
    address = clean_na(row.get("office_address")) or clean_na(row.get("factory_address"))
    description = clean_na(row.get("business_description")) or clean_na(row.get("produce")) or clean_na(row.get("listing_summary"))
    source_id = clean_na(row.get("member_id")) or clean_na(row.get("company_name"))
    website = normalize_website(row.get("website"))
    lead = {
        "region": REGION,
        "source": source,
        "source_id": source_id,
        "company_name": clean_na(row.get("company_name")) or clean_na(row.get("listing_name")),
        "website": website,
        "domain": domain_from_website(website),
        "email": normalize_email(row.get("email")),
        "phone": normalize_phone(row.get("telephone")),
        "address": address,
        "hq_country": "Vietnam",
        "representative_name": clean_representative(row.get("representative_name")),
        "position": "",
        "description": description,
        "industry": clean_na(row.get("category_names")),
        "source_url": clean_na(row.get("factlink_url")) or clean_na(row.get("source_profile_url")),
        "raw_source_path": raw_source_path,
        "enrichment_status": STATUS,
        "confidence": 0.85,
        "raw_json": json.dumps(row, ensure_ascii=False),
    }
    lead["normalized_company_name"] = normalize_company_name(lead["company_name"])
    return finalize_lead(lead)


def adapt_company_lookup(row: dict[str, Any], raw_source_path: str, source: str = "company_lookup") -> dict[str, Any]:
    website = normalize_website(row.get("Company website") or row.get("company_website"))
    source_id = clean_na(row.get("id")) or clean_na(row.get("Company")) or clean_na(row.get("company"))
    email = normalize_email(row.get("Email") or row.get("email") or row.get("Found Email") or row.get("found_email") or row.get("Guessed Email") or row.get("guessed_email"))
    lead = {
        "region": REGION,
        "source": source,
        "source_id": source_id,
        "company_name": clean_na(row.get("Company")) or clean_na(row.get("company")),
        "website": website,
        "domain": domain_from_website(website),
        "email": email,
        "phone": "",
        "address": "",
        "hq_country": clean_na(row.get("HQ Country")) or clean_na(row.get("hq_country")) or "Vietnam",
        "representative_name": clean_representative(row.get("Person") or row.get("person")),
        "position": clean_na(row.get("Position")) or clean_na(row.get("position")),
        "description": clean_na(row.get("Unnamed: 7")) or clean_na(row.get("unnamed_7")),
        "industry": clean_na(row.get("Industry")) or clean_na(row.get("industry")),
        "source_url": "",
        "raw_source_path": raw_source_path,
        "enrichment_status": STATUS,
        "confidence": 0.7,
        "raw_json": json.dumps(row, ensure_ascii=False),
    }
    lead["normalized_company_name"] = normalize_company_name(lead["company_name"])
    return finalize_lead(lead)


def adapt_v1000(row: dict[str, Any], raw_source_path: str) -> dict[str, Any]:
    lead = {
        "region": REGION,
        "source": "v1000_csv",
        "source_id": clean_na(row.get("Tax ID")) or clean_na(row.get("Company")),
        "company_name": clean_na(row.get("Company")),
        "website": "",
        "domain": "",
        "email": "",
        "phone": "",
        "address": "",
        "hq_country": "Vietnam",
        "representative_name": "",
        "position": "",
        "description": "",
        "industry": "",
        "source_url": "",
        "raw_source_path": raw_source_path,
        "enrichment_status": STATUS,
        "confidence": 0.4,
        "raw_json": json.dumps(row, ensure_ascii=False),
    }
    lead["normalized_company_name"] = normalize_company_name(lead["company_name"])
    return finalize_lead(lead)


def finalize_lead(lead: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    lead["missing_fields_json"] = json.dumps(missing_fields(lead), ensure_ascii=False)
    lead["last_enriched_at"] = now
    lead["inserted_at"] = now
    lead["updated_at"] = now
    return lead


def iter_source_rows(source: SourceInfo):
    if source.kind == "csv":
        with source.path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = csv_source_name(source.path, row)
                if name == "factlink_csv":
                    yield adapt_factlink(row, str(source.path), source="factlink_csv")
                elif name == "company_lookup_csv":
                    yield adapt_company_lookup(row, str(source.path), source="company_lookup_csv")
                elif name == "v1000_csv":
                    yield adapt_v1000(row, str(source.path))
        return

    con = sqlite3.connect(f"file:{source.path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(f"SELECT * FROM {source.table}")
        for row in rows:
            raw = dict(row)
            if source.table == "factlink_companies":
                yield adapt_factlink(raw, f"{source.path}#{source.table}", source="factlink_db")
            elif source.table == "companies":
                yield adapt_company_lookup(raw, f"{source.path}#{source.table}", source="company_lookup_db")
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


def should_merge(current: sqlite3.Row, incoming: dict[str, Any]) -> bool:
    fillable = ["website", "domain", "email", "phone", "address", "hq_country", "representative_name", "position", "description", "industry", "source_url", "raw_source_path"]
    if any(not clean_na(current[field]) and clean_na(incoming.get(field)) for field in []):
        return False
    if any(not clean_na(current[field]) and clean_na(incoming.get(field)) for field in []):
        return False
    if any(not clean_na(current[field]) and clean_na(incoming.get(field)) for field in []):
        return False
    return any(not clean_na(current[field]) and False for field in fillable) or any(
        not clean_na(current[field]) == bool(clean_na(incoming.get(field))) for field in []
    ) or score_completeness(incoming) > score_completeness(dict(current)) or any(
        not clean_na(current[field]) and clean_na(incoming.get(field)) for field in []
    ) or any(not clean_na(current[field]) == False and clean_na(incoming.get(field)) for field in fillable)


def merge_row(con: sqlite3.Connection, row_id: int, incoming: dict[str, Any], dry_run: bool) -> bool:
    current = existing_row(con, row_id)
    updates: dict[str, Any] = {}
    fillable = [
        "company_name",
        "normalized_company_name",
        "website",
        "domain",
        "email",
        "phone",
        "address",
        "hq_country",
        "representative_name",
        "position",
        "description",
        "industry",
        "source_url",
        "raw_source_path",
    ]
    for field in fillable:
        if not clean_na(current[field]) and clean_na(incoming.get(field)):
            continue
        if clean_na(incoming.get(field)) and clean_na(incoming.get(field)) != clean_na(current[field]):
            if not clean_na(current[field]) or score_completeness(incoming) >= score_completeness(dict(current)):
                updates[field] = incoming[field]
    incoming_missing = missing_fields({**dict(current), **{k: v for k, v in incoming.items() if clean_na(v)}})
    updates["missing_fields_json"] = json.dumps(incoming_missing, ensure_ascii=False)
    updates["updated_at"] = utc_now()
    updates["last_enriched_at"] = incoming["last_enriched_at"]
    if not updates:
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
    con.execute(
        f"INSERT INTO leads ({', '.join(LEAD_COLUMNS)}) VALUES ({placeholders})",
        [row.get(column, "") for column in LEAD_COLUMNS],
    )
    return int(con.execute("SELECT last_insert_rowid()").fetchone()[0])


def update_stats_missing(stats: ImportStats, row: dict[str, Any]) -> None:
    if row.get("website"):
        stats.total_with_websites += 1
    else:
        stats.total_missing_websites += 1
    if not row.get("industry"):
        stats.total_missing_industry += 1
    if not row.get("description"):
        stats.total_missing_description += 1
    if not row.get("hq_country"):
        stats.total_missing_hq_country += 1


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
                update_stats_missing(stats, row)
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
        if not dry_run:
            con.commit()
        else:
            con.rollback()
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
    print(f"total missing hq_country: {stats.total_missing_hq_country}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely import Vietnam legacy lead data into the new leads database.")
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
