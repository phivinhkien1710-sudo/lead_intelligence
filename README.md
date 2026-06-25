# Lead Intelligence Pipeline

`lead-intelligence-pipeline` is a Python project for building enriched B2B company lead datasets from public company registries, business directories, and company websites.

It provides a shared enrichment engine with region-specific loaders for Singapore and Vietnam, producing structured lead records that can be analyzed, enriched, and stored in regional SQLite databases.

## What It Does

The pipeline turns company records into structured lead intelligence:

1. Load company input from a regional dataset or directory source.
2. Discover or confirm the company website.
3. Scrape relevant website pages.
4. Extract useful fields with rule-based logic.
5. Identify missing important fields.
6. Use Tavily only when important fields are still missing.
7. Use OpenAI only to structure or extract uncertain information.
8. Save results to the correct regional SQLite database.

Fallback AI/search tools are optional. The default workflow prioritizes deterministic data processing, public datasets, website discovery, website scraping, and rule-based extraction.

## Architecture

```text
lead-intelligence-pipeline/
  src/lead_intelligence/
    common/
      config.py          # environment loading and project paths
      csv_io.py          # CSV helpers
      http.py            # shared HTTP/session helpers
      models.py          # shared dataclasses
      storage.py         # shared SQLite lead store
    enrichment/
      pipeline.py        # shared enrichment order
      rules.py           # rule-based extraction
      website.py         # website scraping
      recordowl.py       # website discovery integration
      fallbacks.py       # Tavily/OpenAI fallback hooks
    regions/
      singapore/
        acra.py          # Singapore registry loading/filtering
        pipeline.py      # Singapore orchestration
      vietnam/
        factlink.py      # Vietnam directory CSV loading
        pipeline.py      # Vietnam orchestration
  scripts/
    run_singapore_pipeline.py
    run_vietnam_pipeline.py
    import_singapore_legacy.py
    import_vietnam_legacy.py
    analyze_missing_fields.py
    enrich_decision_makers.py
  databases/
    singapore_leads.db   # generated locally
    vietnam_leads.db     # generated locally
```

## Databases

The project writes separate regional databases:

- `databases/singapore_leads.db`
- `databases/vietnam_leads.db`

Both use the shared `leads` table schema from `src/lead_intelligence/common/storage.py`. Generated databases are ignored by git.

## Setup

```bash
git clone https://github.com/phivinhkien1710-sudo/lead_intelligence.git
cd lead_intelligence
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Fill `.env` only if you want fallback enrichment:

```bash
TAVILY_API_KEY=...
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
```

## Data Inputs

Raw datasets and generated outputs should stay local and outside source control. The repository is configured to ignore common data locations and generated files, including `data/`, `raw_data/`, CSV exports, SQLite databases, caches, and `.env` files.

Recommended local layout:

```text
data/
  singapore/
    acra/
  vietnam/
    factlink_companies_all.csv
databases/
  singapore_leads.db
  vietnam_leads.db
```

You can also pass explicit input paths to the scripts when your datasets are stored elsewhere.

## Singapore Workflow

Prepare a clean live-company input from Singapore registry files:

```bash
python3 scripts/run_singapore_pipeline.py \
  --prepare-acra \
  --external-dir data/singapore/acra \
  --no-recordowl \
  --no-tavily \
  --no-openai
```

Run a small enrichment batch:

```bash
python3 scripts/run_singapore_pipeline.py --limit 100
```

Run without AI/search fallbacks:

```bash
python3 scripts/run_singapore_pipeline.py --limit 100 --no-tavily --no-openai
```

Default Singapore database:

```text
databases/singapore_leads.db
```

## Vietnam Workflow

Use a Vietnam company directory CSV as input:

```bash
python3 scripts/run_vietnam_pipeline.py \
  --input-csv data/vietnam/factlink_companies_all.csv \
  --limit 100
```

Run without AI/search fallbacks:

```bash
python3 scripts/run_vietnam_pipeline.py \
  --input-csv data/vietnam/factlink_companies_all.csv \
  --limit 100 \
  --no-tavily \
  --no-openai
```

Default Vietnam database:

```text
databases/vietnam_leads.db
```

## Legacy Data Import

The import scripts migrate existing exported CSV and SQLite sources into the clean regional databases without calling Tavily or OpenAI:

```bash
python3 scripts/import_singapore_legacy.py --dry-run
python3 scripts/import_vietnam_legacy.py --dry-run
```

After reviewing the dry-run summaries, run the same commands without `--dry-run` to write to the regional databases.

## Missing-Field Analysis

Analyze enrichment coverage across one or both regions:

```bash
python3 scripts/analyze_missing_fields.py --region singapore
python3 scripts/analyze_missing_fields.py --region vietnam
python3 scripts/analyze_missing_fields.py --region all
```

The analysis is read-only and reports coverage for websites, domains, industry, descriptions, headquarters country, email, phone, and representative names.

## Decision-Maker Enrichment

Find likely key decision makers from company websites, with optional Tavily/OpenAI fallback support:

```bash
python3 scripts/enrich_decision_makers.py --region singapore --limit 100 --workers 8
python3 scripts/enrich_decision_makers.py --region vietnam --limit 100 --workers 8
```

Use `--dry-run` to preview behavior without modifying the database. Use `--use-tavily` and `--use-openai` only when fallback enrichment is needed.

## Data Hygiene

The `.gitignore` excludes:

- API keys and `.env` files
- raw external datasets
- generated CSVs
- SQLite databases
- cache and progress files
- `__pycache__`

This keeps the repository focused on reusable source code while allowing large or sensitive datasets to remain local.
