# Scripts

Utility scripts for building, validating, and enriching the camp/afterschool dataset that powers the static site. All scripts are run from the project root (e.g. `python scripts/<name>.py`).

## Build & serve

- **`build_data_js.py`** — Builds `data.js` (consumed by the frontend) by merging `data/organizations.csv` + `data/programs.csv`. Runs `validate_data.py` first and aborts on errors.
- **`validate_data.py`** — Validates the two CSVs for foreign-key integrity, required fields, and enum values. Acts as the gate before `build_data_js.py` writes output.
- **`dev_server.py`** — Local static dev server with lightweight live-reload, used while iterating on the site.

## Data enrichment (CSV-in-place)

These scripts read a CSV, fill in blanks, and write it back. They are idempotent and never overwrite existing values unless explicitly flagged.

- **`backfill_age_grade.py`** — Fills blank `grades_min`/`grades_max` from `age_min`/`age_max` (and vice versa) using `data/age_to_grade.csv`.
- **`enrich_locations.py`** — Geocodes/normalizes program addresses to populate location fields where they look like real street addresses.
- **`enrich_websites.py`** — Populates blank `website` cells in `organizations.csv` using `programs.csv` registration URLs and `potential_vt_organizations_full.csv` website URLs.
- **`fetch_descriptions.py`** — Fetches each program's `registration_url` and extracts a description into the blank `description` field.
- **`infer_activities.py`** — Infers activity tags for programs by keyword-matching the description and program name against the canonical tag list.
- **`infer_counties.py`** — Fills `site_county` for programs (and backfills org city/county) using the Vermont town→county GeoJSON.
- **`infer_org_types.py`** — Infers `org_type` (nonprofit / municipal / school / private / university / faith-based) from keywords in `org_name`.
- **`normalize_times.py`** — Canonicalizes program start/end times into a consistent 12-hour format.
- **`parse_costs.py`** — Parses freeform `cost_raw` strings (e.g. "$200/week", "free", "$50/day") into a numeric `cost_per_week`.
- **`verify_camp_websites.py`** — Fetches each organization's `website` (2s delay between requests) and looks for camp / afterschool / youth-program keywords in the page. Upgrades the `confidence` column from `likely` to `confirmed` on a match; never downgrades existing `confirmed` rows. Progress is saved every 10 rows.

## 2027 ingestion

The pipeline that brings new-season listings in. Run order and details are in `docs/ingestion/runbook.md`. None of these scripts run `git`.

- **`ingest_config.py`** — Reads `data/ingest_config.json` (season, Sheet id, Tasks tab gid, `public_edit`, `secure_by`) and holds the reminder that fails the tests from `secure_by` until the Sheet is locked down.
- **`sources.py`** — The list of pages to watch (`data/sources.csv`). `seed` adds a row for every organization not yet listed, `robots` fills blank `robots_ai_optout` values from each site's `robots.txt` (2s between hosts), `check` lists problems in the file.
- **`watch_sources.py`** — Fetches each `html` and `pdf` source (2s between requests, skips pages checked in the last 6 days), and sets `status_2027` to `posted` when a page mentions the season, or `changed` when a merged page's text changes. Options: `--org`, `--limit`, `--dry-run`, `--force`.
- **`staging.py`** — The staging area for drafted rows, `data/staging/<season>/<org_id>.csv`. `add` and `add-org` stage rows and organization facts from a JSON file (used by the `extract-camp-listings` skill); `check` validates them.
- **`merge_staging.py`** — The only script that writes 2027 rows into `programs.csv` and `organizations.csv`. `--all-ready` or `--org ORG_ID`. Runs the validator and the build, restores both CSVs if either fails, and marks merged organizations `merged` in `sources.csv`.
- **`sheet_sync.py`** — Syncs the volunteer Google Sheet. `pull` downloads the tabs, saves a snapshot under `data/staging/<season>/sheet/` and stages finished tasks; `push` writes the rows to import into the Sheet (`_to_paste_*.csv`) and the cells to change (`_changes.txt`); `init` writes the header rows and How-to text for a new Sheet.
