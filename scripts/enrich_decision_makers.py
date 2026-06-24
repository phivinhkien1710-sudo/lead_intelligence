#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASES = {
    "singapore": PROJECT_ROOT / "databases" / "singapore_leads.db",
    "vietnam": PROJECT_ROOT / "databases" / "vietnam_leads.db",
}
DEFAULT_ENV_PATHS = [PROJECT_ROOT / ".env", Path("/Users/phikien/singaporecompanies/.env")]
PAGES = ("/", "/about", "/about-us", "/team", "/our-team", "/management", "/leadership", "/contact", "/contact-us")
KEYWORDS = (
    "founder",
    "co-founder",
    "ceo",
    "chief executive",
    "managing director",
    "director",
    "owner",
    "general manager",
    "head of sales",
    "business development",
    "operations manager",
    "procurement manager",
)
POSITION_PATTERNS = (
    "Chief Executive Officer",
    "CEO",
    "Founder",
    "Co-Founder",
    "Managing Director",
    "Director",
    "Owner",
    "General Manager",
    "Head of Sales",
    "Business Development",
    "Operations Manager",
    "Procurement Manager",
)
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
NAME_RE = re.compile(r"\b([A-Z][a-zA-Z'.-]+(?:\s+[A-Z][a-zA-Z'.-]+){1,3})\b")
BAD_NAME_PARTS = {
    "Home",
    "About",
    "Contact",
    "Team",
    "Leadership",
    "Management",
    "Singapore",
    "Vietnam",
    "Company",
    "Limited",
    "Private",
    "Business",
    "Facebook",
    "LinkedIn",
    "Twitter",
    "Instagram",
    "Copyright",
    "Privacy",
}
MISSING_FIELDS = ["website", "industry", "description", "hq_country", "representative_name", "email", "phone"]


@dataclass(slots=True)
class Candidate:
    representative_name: str = ""
    position: str = ""
    email: str = ""
    source_url: str = ""
    confidence: float = 0.0
    reason: str = ""
    evidence: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class Summary:
    selected: int = 0
    enriched: int = 0
    not_found: int = 0
    skipped: int = 0
    names_added: int = 0
    positions_added: int = 0
    emails_added: int = 0
    confidences: list[float] = field(default_factory=list)
    tavily_calls_used: int = 0
    openai_calls_used: int = 0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def present(value: Any) -> bool:
    return bool(clean_space(value))


def load_dotenv(paths: list[Path]) -> dict[str, str]:
    env = dict(os.environ)
    for path in paths:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def db_path_for_region(region: str) -> Path:
    return DATABASES[region]


def connect(db_path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def selection_sql() -> str:
    has_company = "company_name IS NOT NULL AND TRIM(company_name) != ''"
    missing_rep = "representative_name IS NULL OR TRIM(representative_name) = ''"
    missing_position = "position IS NULL OR TRIM(position) = ''"
    has_site = "(website IS NOT NULL AND TRIM(website) != '') OR (domain IS NOT NULL AND TRIM(domain) != '')"
    return f"""
        SELECT *
        FROM leads
        WHERE {has_company}
          AND (({missing_rep}) OR ({missing_position}))
        ORDER BY
            CASE
                WHEN ({has_site}) AND ({missing_rep}) THEN 1
                WHEN ({has_site}) AND NOT ({missing_rep}) AND ({missing_position}) THEN 2
                ELSE 3
            END,
            id
        LIMIT ?
    """


def select_leads(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(selection_sql(), (limit,)).fetchall()


def normalize_url(value: str) -> str:
    value = clean_space(value)
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    if not parsed.netloc:
        return ""
    return value.rstrip("/")


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def fetch_url(session: requests.Session, url: str, timeout: int = 15, delay: float = 0.2) -> str:
    if delay:
        time.sleep(delay)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


def evidence_from_website(row: sqlite3.Row) -> list[dict[str, str]]:
    base = normalize_url(row["website"] or row["domain"] or "")
    if not base:
        return []
    parsed = urlparse(base)
    base_host = parsed.netloc.lower().removeprefix("www.")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "LeadIntelligenceDecisionMakerBot/1.0 (+local research)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    evidence: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in PAGES:
        url = urljoin(base + "/", path.lstrip("/"))
        if url in seen:
            continue
        seen.add(url)
        try:
            html = fetch_url(session, url)
        except Exception:
            continue
        if urlparse(url).netloc.lower().removeprefix("www.") != base_host:
            continue
        text = html_to_text(html)
        snippets = keyword_snippets(text)
        for snippet in snippets:
            evidence.append({"source": "website", "url": url, "text": snippet})
    return evidence


def keyword_snippets(text: str, radius: int = 260) -> list[str]:
    lower = text.lower()
    snippets: list[str] = []
    for keyword in KEYWORDS:
        start = 0
        while True:
            idx = lower.find(keyword, start)
            if idx == -1:
                break
            left = max(0, idx - radius)
            right = min(len(text), idx + len(keyword) + radius)
            snippet = clean_space(text[left:right])
            if snippet and snippet not in snippets:
                snippets.append(snippet)
            start = idx + len(keyword)
    return snippets[:20]


def is_plausible_name(value: str) -> bool:
    value = clean_space(value)
    if not value or len(value) < 4 or len(value) > 80:
        return False
    if any(char.isdigit() for char in value):
        return False
    parts = value.split()
    if len(parts) < 2 or len(parts) > 4:
        return False
    if any(part.strip(".,:;|-/") in BAD_NAME_PARTS for part in parts):
        return False
    if any(value.lower().startswith(prefix) for prefix in ("our ", "the ", "new ", "best ")):
        return False
    return True


def extract_position(text: str) -> str:
    lower = text.lower()
    for position in POSITION_PATTERNS:
        if position.lower() in lower:
            return position
    return ""


def candidate_from_evidence(evidence: list[dict[str, str]]) -> Candidate:
    best = Candidate(evidence=evidence)
    for item in evidence:
        text = item["text"]
        position = extract_position(text)
        if not position:
            continue
        emails = EMAIL_RE.findall(text)
        names: list[str] = []
        for match in re.finditer(re.escape(position), text, flags=re.I):
            left = text[max(0, match.start() - 120):match.start()]
            right = text[match.end():match.end() + 120]
            names.extend(NAME_RE.findall(left))
            names.extend(NAME_RE.findall(right))
        names.extend(NAME_RE.findall(text[:180]))
        for name in names:
            name = clean_person_name(name)
            if not is_plausible_name(name):
                continue
            confidence = 0.72
            if emails:
                confidence += 0.08
            if item["source"] == "website":
                confidence += 0.05
            if confidence > best.confidence:
                best = Candidate(
                    representative_name=name,
                    position=position,
                    email=(emails[0].lower() if emails else ""),
                    source_url=item["url"],
                    confidence=min(confidence, 0.9),
                    reason=f"Found name near decision-maker title '{position}'.",
                    evidence=evidence,
                )
    return best


def clean_person_name(value: str) -> str:
    value = re.sub(r"\b(Mr|Mrs|Ms|Miss|Dr)\.?\s+", "", clean_space(value), flags=re.I)
    return value.strip(" ,.;:|-/")


def tavily_search(row: sqlite3.Row, api_key: str) -> tuple[list[dict[str, str]], int]:
    if not api_key:
        return [], 0
    queries = [
        f"{row['company_name']} founder",
        f"{row['company_name']} CEO",
        f"{row['company_name']} managing director",
        f"{row['company_name']} director",
        f"{row['company_name']} LinkedIn",
        f"{row['company_name']} leadership",
        f"{row['company_name']} management team",
    ]
    evidence: list[dict[str, str]] = []
    calls = 0
    for query in queries:
        try:
            response = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "search_depth": "basic", "max_results": 5},
                timeout=30,
            )
            response.raise_for_status()
            calls += 1
            payload = response.json()
        except Exception as exc:
            evidence.append({"source": "tavily_error", "url": "", "text": f"{query}: {exc}"})
            continue
        for result in payload.get("results", []):
            text = clean_space(f"{result.get('title', '')} {result.get('content', '')}")
            if text:
                evidence.append({"source": "tavily", "url": result.get("url", ""), "text": text})
    return evidence, calls


def openai_extract(row: sqlite3.Row, evidence: list[dict[str, str]], api_key: str, model: str) -> tuple[Candidate, int]:
    if not api_key or not evidence:
        return Candidate(evidence=evidence), 0
    context = "\n\n".join(f"URL: {item['url']}\nTEXT: {item['text']}" for item in evidence[:25])[:12000]
    schema_instruction = {
        "representative_name": "",
        "position": "",
        "email": "",
        "source_url": "",
        "confidence": 0.0,
        "reason": "",
    }
    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "input": [
                    {
                        "role": "system",
                        "content": (
                            "Extract one key decision-maker candidate for the company. "
                            "Only use evidence provided. If evidence is weak, return empty representative_name. "
                            "Return only JSON with these keys: representative_name, position, email, source_url, confidence, reason."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "company_name": row["company_name"],
                                "existing_representative_name": row["representative_name"] or "",
                                "existing_position": row["position"] or "",
                                "expected_json": schema_instruction,
                                "evidence": context,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                "text": {"format": {"type": "json_object"}},
            },
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload.get("output_text", "")
        if not text:
            chunks = []
            for item in payload.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") in {"output_text", "text"}:
                        chunks.append(content.get("text", ""))
            text = "".join(chunks)
        data = json.loads(text) if text else {}
    except Exception as exc:
        return Candidate(reason=f"OpenAI extraction failed: {exc}", evidence=evidence), 1
    candidate = Candidate(
        representative_name=clean_person_name(data.get("representative_name", "")),
        position=clean_space(data.get("position", "")),
        email=(EMAIL_RE.findall(data.get("email", "")) or [""])[0].lower(),
        source_url=clean_space(data.get("source_url", "")),
        confidence=float(data.get("confidence") or 0.0),
        reason=clean_space(data.get("reason", "")),
        evidence=evidence,
    )
    if candidate.representative_name and not is_plausible_name(candidate.representative_name):
        candidate.representative_name = ""
    return candidate, 1


def missing_fields(row: dict[str, Any]) -> list[str]:
    fields = ["website", "industry", "description", "hq_country", "representative_name", "email", "phone"]
    return [field for field in fields if not present(row.get(field))]


def merge_raw_json(row: sqlite3.Row, candidate: Candidate, status: str) -> str:
    try:
        raw = json.loads(row["raw_json"] or "{}")
        if not isinstance(raw, dict):
            raw = {"legacy_raw": raw}
    except json.JSONDecodeError:
        raw = {"legacy_raw_json": row["raw_json"] or ""}
    raw.setdefault("decision_maker_enrichment", [])
    raw["decision_maker_enrichment"].append(
        {
            "status": status,
            "candidate": {
                "representative_name": candidate.representative_name,
                "position": candidate.position,
                "email": candidate.email,
                "source_url": candidate.source_url,
                "confidence": candidate.confidence,
                "reason": candidate.reason,
            },
            "evidence": candidate.evidence[:20],
            "timestamp": utc_now(),
        }
    )
    return json.dumps(raw, ensure_ascii=False)


def should_update(row: sqlite3.Row, candidate: Candidate) -> bool:
    if candidate.confidence < 0.65:
        return False
    if not candidate.representative_name and not candidate.position and not candidate.email:
        return False
    existing_conf = float(row["confidence"] or 0.0)
    if candidate.confidence <= existing_conf and present(row["representative_name"]) and present(row["position"]):
        return False
    return True


def update_row(conn: sqlite3.Connection, row: sqlite3.Row, candidate: Candidate, dry_run: bool) -> tuple[bool, dict[str, int]]:
    counters = {"names_added": 0, "positions_added": 0, "emails_added": 0}
    updated = dict(row)
    existing_conf = float(row["confidence"] or 0.0)
    can_overwrite = candidate.confidence > existing_conf

    if candidate.representative_name and (not present(row["representative_name"]) or can_overwrite):
        updated["representative_name"] = candidate.representative_name
        if not present(row["representative_name"]):
            counters["names_added"] = 1
    if candidate.position and (not present(row["position"]) or can_overwrite):
        updated["position"] = candidate.position
        if not present(row["position"]):
            counters["positions_added"] = 1
    if candidate.email and (not present(row["email"]) or can_overwrite):
        updated["email"] = candidate.email
        if not present(row["email"]):
            counters["emails_added"] = 1
    if candidate.source_url and (not present(row["source_url"]) or can_overwrite):
        updated["source_url"] = candidate.source_url
    updated["confidence"] = max(existing_conf, candidate.confidence)
    updated["enrichment_status"] = "decision_maker_enriched"
    updated["updated_at"] = utc_now()
    updated["last_enriched_at"] = updated["updated_at"]
    updated["missing_fields_json"] = json.dumps(missing_fields(updated), ensure_ascii=False)
    updated["raw_json"] = merge_raw_json(row, candidate, "decision_maker_enriched")

    changed = any(updated[field] != row[field] for field in ("representative_name", "position", "email", "source_url", "confidence"))
    if not changed:
        return False, counters
    if not dry_run:
        conn.execute(
            """
            UPDATE leads
            SET representative_name = ?, position = ?, email = ?, source_url = ?, confidence = ?,
                raw_json = ?, enrichment_status = ?, missing_fields_json = ?, updated_at = ?, last_enriched_at = ?
            WHERE id = ?
            """,
            (
                updated["representative_name"],
                updated["position"],
                updated["email"],
                updated["source_url"],
                updated["confidence"],
                updated["raw_json"],
                updated["enrichment_status"],
                updated["missing_fields_json"],
                updated["updated_at"],
                updated["last_enriched_at"],
                row["id"],
            ),
        )
    return True, counters


def mark_not_found(conn: sqlite3.Connection, row: sqlite3.Row, candidate: Candidate, dry_run: bool) -> None:
    now = utc_now()
    updated = dict(row)
    updated["enrichment_status"] = "decision_maker_not_found"
    updated["missing_fields_json"] = json.dumps(missing_fields(updated), ensure_ascii=False)
    raw_json = merge_raw_json(row, candidate, "decision_maker_not_found")
    if not dry_run:
        conn.execute(
            """
            UPDATE leads
            SET enrichment_status = ?, missing_fields_json = ?, raw_json = ?, updated_at = ?, last_enriched_at = ?
            WHERE id = ?
            """,
            ("decision_maker_not_found", updated["missing_fields_json"], raw_json, now, now, row["id"]),
        )


def enrich_one(row: sqlite3.Row, env: dict[str, str], use_tavily: bool, use_openai: bool) -> tuple[Candidate, int, int]:
    evidence = evidence_from_website(row)
    candidate = candidate_from_evidence(evidence)
    tavily_calls = 0
    openai_calls = 0

    if candidate.confidence < 0.65 and use_tavily:
        tavily_evidence, tavily_calls = tavily_search(row, env.get("TAVILY_API_KEY", ""))
        evidence.extend(tavily_evidence)
        candidate = candidate_from_evidence(evidence)

    if use_openai and evidence:
        openai_candidate, openai_calls = openai_extract(row, evidence, env.get("OPENAI_API_KEY", ""), env.get("OPENAI_MODEL", "gpt-4o-mini"))
        if openai_candidate.confidence > candidate.confidence:
            candidate = openai_candidate

    candidate.evidence = evidence
    return candidate, tavily_calls, openai_calls


def process_one(row: sqlite3.Row, env: dict[str, str], use_tavily: bool, use_openai: bool) -> tuple[sqlite3.Row, Candidate, int, int]:
    candidate, tavily_calls, openai_calls = enrich_one(row, env, use_tavily, use_openai)
    return row, candidate, tavily_calls, openai_calls


def apply_result(conn: sqlite3.Connection, row: sqlite3.Row, candidate: Candidate, dry_run: bool) -> tuple[str, dict[str, int]]:
    if should_update(row, candidate):
        changed, counters = update_row(conn, row, candidate, dry_run)
        if changed:
            if not dry_run:
                conn.commit()
            return "enriched", counters
        return "skipped", {"names_added": 0, "positions_added": 0, "emails_added": 0}

    mark_not_found(conn, row, candidate, dry_run)
    if not dry_run:
        conn.commit()
    return "not_found", {"names_added": 0, "positions_added": 0, "emails_added": 0}


def run(args: argparse.Namespace) -> Summary:
    db_path = db_path_for_region(args.region)
    env = load_dotenv(DEFAULT_ENV_PATHS)
    summary = Summary()
    with connect(db_path, readonly=args.dry_run) as conn:
        rows = select_leads(conn, args.limit)
        summary.selected = len(rows)
        if args.use_tavily and not env.get("TAVILY_API_KEY"):
            print("Tavily requested but TAVILY_API_KEY is missing; Tavily calls will be skipped.")
        if args.use_openai and not env.get("OPENAI_API_KEY"):
            print("OpenAI requested but OPENAI_API_KEY is missing; OpenAI calls will be skipped.")

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {
                executor.submit(process_one, row, env, args.use_tavily, args.use_openai): row
                for row in rows
            }
            completed = 0
            for future in as_completed(futures):
                completed += 1
                row = futures[future]
                print(f"[{completed}/{len(rows)}] {row['company_name']}")
                try:
                    row, candidate, tavily_calls, openai_calls = future.result()
                except Exception as exc:
                    candidate = Candidate(reason=f"Processing failed: {exc}", evidence=[])
                    tavily_calls = 0
                    openai_calls = 0
                summary.tavily_calls_used += tavily_calls
                summary.openai_calls_used += openai_calls

                status, counters = apply_result(conn, row, candidate, args.dry_run)
                if status == "enriched":
                    summary.enriched += 1
                    summary.names_added += counters["names_added"]
                    summary.positions_added += counters["positions_added"]
                    summary.emails_added += counters["emails_added"]
                    summary.confidences.append(candidate.confidence)
                    print(f"  enriched: {candidate.representative_name} | {candidate.position} | {candidate.confidence:.2f}")
                elif status == "skipped":
                    summary.skipped += 1
                    print("  skipped: no field would improve")
                else:
                    summary.not_found += 1
                    print("  not found")
    return summary


def print_summary(summary: Summary) -> None:
    avg = mean(summary.confidences) if summary.confidences else 0.0
    print("\n[decision-maker enrichment summary]")
    print(f"selected: {summary.selected}")
    print(f"enriched: {summary.enriched}")
    print(f"not found: {summary.not_found}")
    print(f"skipped: {summary.skipped}")
    print(f"names added: {summary.names_added}")
    print(f"positions added: {summary.positions_added}")
    print(f"emails added: {summary.emails_added}")
    print(f"average confidence: {avg:.3f}")
    print(f"Tavily calls used: {summary.tavily_calls_used}")
    print(f"OpenAI calls used: {summary.openai_calls_used}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Find and store decision makers for existing leads.")
    parser.add_argument("--region", choices=["singapore", "vietnam"], required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--workers", type=int, default=4, help="Number of companies to scrape/search in parallel.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--use-tavily", action="store_true")
    parser.add_argument("--use-openai", action="store_true")
    args = parser.parse_args()

    summary = run(args)
    print_summary(summary)


if __name__ == "__main__":
    main()
