#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lead_intelligence.common.config import DATABASE_DIR, first_env, load_dotenv
from lead_intelligence.enrichment.pipeline import EnrichmentSettings
from lead_intelligence.regions.singapore.pipeline import SingaporePipelineSettings, run_singapore_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Singapore lead intelligence pipeline.")
    parser.add_argument("--input-csv", type=Path, default=Path("data/acra_live_companies_for_recordowl.csv"))
    parser.add_argument("--external-dir", type=Path, default=Path("external data/ACRAInformationonCorporateEntities"))
    parser.add_argument("--db", type=Path, default=DATABASE_DIR / "singapore_leads.db")
    parser.add_argument("--prepare-acra", action="store_true", help="Create the live ACRA input CSV before enrichment.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-recordowl", dest="use_recordowl", action="store_false")
    parser.add_argument("--no-tavily", dest="use_tavily", action="store_false")
    parser.add_argument("--no-openai", dest="use_openai", action="store_false")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--recordowl-delay", type=float, default=0.5)
    parser.add_argument("--recordowl-timeout", type=int, default=20)
    parser.add_argument("--scrape-delay", type=float, default=0.2)
    parser.add_argument("--scrape-timeout", type=int, default=15)
    parser.add_argument("--max-pages", type=int, default=5)
    args = parser.parse_args()

    env = load_dotenv(args.env)
    enrichment = EnrichmentSettings(
        db_path=args.db,
        tavily_api_key=first_env(env, "TAVILY_API_KEY"),
        openai_api_key=first_env(env, "OPENAI_API_KEY"),
        openai_model=first_env(env, "OPENAI_MODEL") or "gpt-4o-mini",
        use_tavily=args.use_tavily,
        use_openai=args.use_openai,
        scrape_delay=args.scrape_delay,
        scrape_timeout=args.scrape_timeout,
        max_pages=args.max_pages,
    )
    settings = SingaporePipelineSettings(
        input_csv=args.input_csv,
        external_dir=args.external_dir,
        db_path=args.db,
        prepare_acra=args.prepare_acra,
        use_recordowl=args.use_recordowl,
        limit=args.limit,
        recordowl_delay=args.recordowl_delay,
        recordowl_timeout=args.recordowl_timeout,
        enrichment=enrichment,
    )
    result = run_singapore_pipeline(settings)
    print(f"[done] {result}")


if __name__ == "__main__":
    main()

