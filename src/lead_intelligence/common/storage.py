from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from lead_intelligence.common.models import LeadRecord


LEAD_COLUMNS: dict[str, str] = {
    "normalized_company_name": "TEXT",
    "domain": "TEXT",
    "address": "TEXT",
    "hq_country": "TEXT",
    "raw_source_path": "TEXT",
    "last_enriched_at": "TEXT",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_company_name(value: str) -> str:
    value = (value or "").casefold()
    value = re.sub(r"\b(pte|ltd|limited|llp|llc|inc|corp|corporation|co|company)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def domain_from_website(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    try:
        host = urlparse(value).hostname or ""
    except Exception:
        return ""
    return host.lower().removeprefix("www.")


def migrate_leads_table(conn: sqlite3.Connection) -> None:
    existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(leads)").fetchall()}
    for column_name, column_type in LEAD_COLUMNS.items():
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {column_name} {column_type}")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_region_company ON leads(region, company_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_website ON leads(website)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_region_domain ON leads(region, domain)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_region_normalized_name "
        "ON leads(region, normalized_company_name)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_region_status ON leads(region, enrichment_status)")


def initialize_database(db_path: Path | str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                region TEXT NOT NULL,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                company_name TEXT NOT NULL,
                normalized_company_name TEXT,
                website TEXT,
                domain TEXT,
                email TEXT,
                phone TEXT,
                address TEXT,
                hq_country TEXT,
                representative_name TEXT,
                position TEXT,
                description TEXT,
                industry TEXT,
                source_url TEXT,
                raw_source_path TEXT,
                enrichment_status TEXT,
                missing_fields_json TEXT,
                confidence REAL,
                raw_json TEXT,
                last_enriched_at TEXT,
                inserted_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(region, source, source_id)
            )
            """
        )
        migrate_leads_table(conn)


def save_lead(db_path: Path | str, lead: LeadRecord) -> None:
    initialize_database(db_path)
    now = utc_now()
    normalized_company_name = lead.normalized_company_name or normalize_company_name(lead.company_name)
    domain = lead.domain or domain_from_website(lead.website)
    last_enriched_at = lead.last_enriched_at or now
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO leads (
                region, source, source_id, company_name, normalized_company_name,
                website, domain, email, phone, address, hq_country,
                representative_name, position, description, industry, source_url,
                raw_source_path, enrichment_status, missing_fields_json, confidence,
                raw_json, last_enriched_at, inserted_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(region, source, source_id) DO UPDATE SET
                company_name = excluded.company_name,
                normalized_company_name = COALESCE(NULLIF(excluded.normalized_company_name, ''), leads.normalized_company_name),
                website = COALESCE(NULLIF(excluded.website, ''), leads.website),
                domain = COALESCE(NULLIF(excluded.domain, ''), leads.domain),
                email = COALESCE(NULLIF(excluded.email, ''), leads.email),
                phone = COALESCE(NULLIF(excluded.phone, ''), leads.phone),
                address = COALESCE(NULLIF(excluded.address, ''), leads.address),
                hq_country = COALESCE(NULLIF(excluded.hq_country, ''), leads.hq_country),
                representative_name = COALESCE(NULLIF(excluded.representative_name, ''), leads.representative_name),
                position = COALESCE(NULLIF(excluded.position, ''), leads.position),
                description = COALESCE(NULLIF(excluded.description, ''), leads.description),
                industry = COALESCE(NULLIF(excluded.industry, ''), leads.industry),
                source_url = COALESCE(NULLIF(excluded.source_url, ''), leads.source_url),
                raw_source_path = COALESCE(NULLIF(excluded.raw_source_path, ''), leads.raw_source_path),
                enrichment_status = excluded.enrichment_status,
                missing_fields_json = excluded.missing_fields_json,
                confidence = excluded.confidence,
                raw_json = excluded.raw_json,
                last_enriched_at = excluded.last_enriched_at,
                updated_at = excluded.updated_at
            """,
            (
                lead.region,
                lead.source,
                lead.source_id,
                lead.company_name,
                normalized_company_name,
                lead.website,
                domain,
                lead.email,
                lead.phone,
                lead.address,
                lead.hq_country,
                lead.representative_name,
                lead.position,
                lead.description,
                lead.industry,
                lead.source_url,
                lead.raw_source_path,
                lead.enrichment_status,
                json.dumps(lead.missing_fields, ensure_ascii=False),
                lead.confidence,
                json.dumps(lead.raw, ensure_ascii=False),
                last_enriched_at,
                now,
                now,
            ),
        )
