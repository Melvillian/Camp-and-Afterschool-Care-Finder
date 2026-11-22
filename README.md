# Camp & Afterschool Care Finder

An open-source tool helping Vermont families find summer camps and afterschool programs for K–12 students. Filter by grade, city, county, activity, cost, and schedule. Browse as a list, calendar, or interactive map.

**Live site:** [https://kefortney.github.io/Camp-and-Afterschool-Care-Finder/](https://kefortney.github.io/Camp-and-Afterschool-Care-Finder/)

## Run locally

```bash
python -m http.server 8000
```

Then open `http://localhost:8000`. For live reload while editing:

```bash
python scripts/dev_server.py
```

## Tests

```bash
node --test tests/js/
python3 -m unittest discover -s tests/py
python3 scripts/smoke_test.py
```

The smoke test drives headless Chrome against the site with a fixture `data.js`, so Chrome is required (set `CHROME` to override the path).

## Data pipeline

All program and organization data lives in two CSV files:

| File | Contents |
| --- | --- |
| `data/programs.csv` | One row per program session: summer camps and afterschool programs |
| `data/organizations.csv` | One row per organization |

The About and Data pages show the current counts, taken from `data.js` when the page loads.

After editing either CSV, rebuild `data.js`:

```bash
python scripts/validate_data.py   # check for errors first
python scripts/build_data_js.py   # generates data.js
```

The validation script exits 1 on errors (blocks the build). Warnings are printed but do not block.

## 2027 ingestion

New-season listings come in through a small pipeline: a script watches each organization's camp page, Claude Code drafts rows from pages that have posted, volunteers check the drafts in a shared Google Sheet, and a maintainer merges the checked rows. The weekly routine, first-time Sheet setup and recovery steps are in [`docs/ingestion/runbook.md`](docs/ingestion/runbook.md). What volunteers are told is in [`docs/ingestion/volunteer-how-to.md`](docs/ingestion/volunteer-how-to.md). The site's Volunteer page (`volunteer.html`) lists the open tasks. The letter asking the three Rec1 recreation departments for an export is [`docs/ingestion/rec1-export-request.md`](docs/ingestion/rec1-export-request.md).

## Utility scripts

| Script | What it does |
| --- | --- |
| `scripts/backfill_age_grade.py` | Fill `grades_min`/`grades_max` from age data (and vice versa) |
| `scripts/enrich_locations.py` | Geocode and standardize `site_address` via Nominatim |
| `scripts/fetch_descriptions.py` | Fetch missing `description` values from `registration_url` |
| `scripts/infer_activities.py` | Infer `activities` tags from `description` and `program_name` |
| `scripts/infer_counties.py` | Fill `site_county` using the GeoJSON town→county map |
| `scripts/infer_org_types.py` | Infer `org_type` from `org_name` keywords |
| `scripts/normalize_times.py` | Standardize `start_time`/`end_time` to 12-hour format |
| `scripts/parse_costs.py` | Parse `cost_raw` into a normalized `cost_per_week` value |
| `scripts/dev_server.py` | Local server (this machine only) with live reload; lets the Admin page save the two data CSVs |

## Admin editor

A local tool for correcting rows of earlier seasons. It is not part of the public site: the Admin link shows only on `localhost`, and anywhere else the page is read-only.

1. Run `python scripts/dev_server.py` and open `http://127.0.0.1:8000/admin.html`. Editing is on only through this server (plain `python -m http.server` cannot save).
2. Edit, then click **Save All**. It changes only the cells you edited in `data/programs.csv` and `data/organizations.csv`; every other row and column, including inactive rows, is written back as it was.
3. Run `python scripts/build_data_js.py`. Editing stays off until you do, because the page lists what is in `data.js`.
4. Commit `data/programs.csv`, `data/organizations.csv`, and `data.js`.

Rows for the season being ingested (2027, from `data/ingest_config.json`) are locked in the Admin page, and it will not add them: `scripts/merge_staging.py` is the only writer of those rows. The `notes` columns are public (they are in this repository and in `data.js`).

## Data constraints

**`site_city` must match a town name in `data/Vermont_Town_GEOID_RPC_County.geojson`.**
The `infer_counties.py` script uses this file to fill `site_county` automatically. If a town name does not match, the county field will be left blank and the program will be excluded from county-based filters.

Use the `TOWNNAMEMC` property in the GeoJSON as the canonical spelling (e.g. `Burlington`, `South Burlington`, `Saint Albans City`). Common aliases like `St. Albans` are handled by the script, but new aliases must be added to `CITY_ALIASES` in `scripts/infer_counties.py`.

## Contributing

1. Fork the repo and clone it locally
2. To help with 2027 listings, use the site's Volunteer page and the shared Sheet (see "2027 ingestion" above); do not add 2027 rows to the CSVs by hand. To correct an earlier season, edit rows in `data/programs.csv` or `data/organizations.csv`
3. Run `python scripts/validate_data.py` — fix any ERRORs
4. Run `python scripts/build_data_js.py` — regenerates `data.js`
5. Test with `python scripts/dev_server.py`
6. Submit a pull request

See the [Data docs](https://kefortney.github.io/Camp-and-Afterschool-Care-Finder/data.html) for full field-by-field schema documentation.
