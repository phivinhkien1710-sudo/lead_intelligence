# Lead Intelligence Pipeline

`lead-intelligence-pipeline` is a unified Python project for building enriched B2B company lead datasets from public registry and directory sources.

The project consolidates the previous `singaporecompanies`, `kienngu123`, and `recordowl_project` work into one package with shared enrichment logic and region-specific loaders.

## What It Does

The pipeline turns raw company records into structured lead records:

1. Load company input from a regional public dataset.
2. Discover or confirm the company website.
3. Scrape the company website.
4. Extract useful fields with rule-based logic.
5. Identify missing important fields.
6. Use Tavily only if important fields are still missing.
7. Use OpenAI only to structure or extract uncertain information.
8. Save results to the correct regional SQLite database.

## Architecture

```text
lead-intelligence-pipeline/
  src/lead_intelligence/
    common/
      config.py          # env loading and project paths
      csv_io.py          # CSV helpers
      http.py            # shared HTTP/session helpers
      models.py          # shared dataclasses
      storage.py         # shared SQLite lead store
    enrichment/
      pipeline.py        # shared enrichment order
      rules.py           # rule-based extraction
      website.py         # website scraping
      recordowl.py       # RecordOwl website discovery
      fallbacks.py       # Tavily/OpenAI fallback hooks
    regions/
      singapore/
        acra.py          # ACRA live-company loading/filtering
        pipeline.py      # Singapore orchestration
      vietnam/
        factlink.py      # FactLink CSV loading
        pipeline.py      # Vietnam orchestration
  scripts/
    run_singapore_pipeline.py
    run_vietnam_pipeline.py
  databases/
    singapore_leads.db   # generated
    vietnam_leads.db     # generated
```

## Databases

The new project writes separate regional databases:

- `databases/singapore_leads.db`
- `databases/vietnam_leads.db`

Both use the shared `leads` table schema from `src/lead_intelligence/common/storage.py`.

Generated databases are ignored by git.

## Setup

```bash
cd /Users/phikien/singaporecompanies
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

Tavily and OpenAI are optional. The pipeline tries dataset fields, website discovery, website scraping, and rule-based extraction first.

## Singapore Workflow

Prepare a clean live-company input from ACRA files:

```bash
python3 scripts/run_singapore_pipeline.py --prepare-acra --no-recordowl --no-tavily --no-openai
```

If you want to reuse the old local ACRA files from the previous workspace:

```bash
python3 scripts/run_singapore_pipeline.py \
  --prepare-acra \
  --external-dir "/Users/phikien/singaporecompanies/legacy_workspace/external data/ACRAInformationonCorporateEntities" \
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

Use a FactLink CSV as input:

```bash
python3 scripts/run_vietnam_pipeline.py \
  --input-csv data/factlink_companies_all.csv \
  --limit 100
```

If you want to reuse the old local FactLink file from the previous workspace:

```bash
python3 scripts/run_vietnam_pipeline.py \
  --input-csv /Users/phikien/singaporecompanies/legacy_workspace/data/factlink_companies_all.csv \
  --limit 100
```

Run without AI/search fallbacks:

```bash
python3 scripts/run_vietnam_pipeline.py \
  --input-csv data/factlink_companies_all.csv \
  --limit 100 \
  --no-tavily \
  --no-openai
```

Default Vietnam database:

```text
databases/vietnam_leads.db
```

## Archive Policy

Old working code was archived instead of deleted:

- `/Users/phikien/singaporecompanies/legacy_workspace/archive/singaporecompanies/`
- `/Users/phikien/singaporecompanies/legacy_workspace/archive/kienngu123/`
- `/Users/phikien/singaporecompanies/legacy_workspace/archive/recordowl_project/`

Use the archive as a reference while gradually porting more mature logic into `src/lead_intelligence/`.

## Data Hygiene

The `.gitignore` excludes:

- API keys and `.env` files
- raw external datasets
- generated CSVs
- SQLite databases
- cache/progress files
- `__pycache__`

This keeps the repository suitable for source control while allowing large local datasets to remain on disk.
