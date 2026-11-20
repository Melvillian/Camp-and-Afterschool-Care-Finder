# Ingesting 2027 Camp Listings — Design

*2026-11-20. Status: approved by Melvillian. See "Decisions" for who decided what. Plan: `docs/superpowers/plans/2026-11-20-2027-camp-ingestion.md`.*

## Intent

Families start signing up for summer camp in January. The site currently holds only 2026 programs, every dated one of which is now in the past. The goal is to have 2027 camps on the site as each one is announced, with the date registration opens, in time for families to act on it.

## What the research found

### Timing

For summer 2026, listings were posted in December and January and registration opened between early January and mid-February. As of today almost nothing is posted for 2027.

| Organization | 2026 registration opened | 2027 status today |
|---|---|---|
| Community Sailing Center | Lottery Jan 8–12; general Feb 3 | Nothing posted |
| Shelburne Farms | Lottery Jan 11–13 | Nothing posted |
| Williston Recreation | Residents Jan 12; non-residents Jan 26 | Nothing posted |
| Colchester Parks & Rec | Residents Jan 26; non-residents Feb 9 | Nothing posted |
| Greater Burlington YMCA | Jan 26 (from a third-party guide) | No date |
| Burlington Parks | Residents Feb 4; non-residents Feb 11 | Nothing posted |
| Essex Junction Rec | Not confirmed for 2026 (2024: Feb 19) | "Check back in January" |
| Camp Abnaki | Open by Jan 7 | Early-bird registration open now |
| Farm & Wilderness | Rolling | 2027 dates and rates posted |

So the working window is roughly mid-December 2026 to mid-March 2027, and a single pass is not enough: organizations post at different times.

### Where the current data comes from

The 392 programs come from 133 organizations. Four organizations account for 143 of them (Burlington Parks 70, Catamount 32, South Burlington Rec 24, YMCA 17), and 103 organizations have exactly one program.

Each source site was probed once today. Grouped by what sits behind the registration link:

| Behind the link | Programs | Sites |
|---|---|---|
| Rec1 / CivicRec catalog | 97 | 3 |
| Ordinary website, no registration platform visible | 93 | 65 |
| Link is dead (404, DNS or TLS failure) | 42 | 21 |
| UltraCamp | 40 | 2 |
| Events-calendar plugin | 20 | 7 |
| Ten other registration platforms | 80 | 23 |

- **No shared structured format.** No site publishes camp sessions as schema.org events or as an iCal feed, so there is no generic machine-readable path.
- **Rec1 is the one high-leverage platform.** Its catalogs are public and load their items as JSON. Burlington and Colchester use it as well as South Burlington. But it sits behind Cloudflare bot protection: a plain HTTP client gets an empty page, and only a real browser session works.
- **Most other platforms are login-gated or need an API key** (CampBrain, CampDoc, Amilia, ActiveNet).
- **The long tail is ordinary web pages and PDFs**, each laid out differently.
- **33 of the sites name AI crawlers in `robots.txt`.** None disallow all crawling.
- **Registration-open dates are not captured at all today.** The columns exist (`registration_opens`, `registration_opens_early`, `registration_notes`) and are blank in all 392 rows.

### Other sources

- **Directories** (Vermont Camp Association, campfindervt.com, Kids VT guide, ACA) list tens of camps each, without session dates or prices, and are copyrighted compilations with no export. They are useful for discovering organizations we lack, not as a data source.
- **Vermont DCF's regulated-provider dataset** on data.vermont.gov is the only open-licensed source (Open Database License, updated monthly, with a JSON API). It covers licensed afterschool programs and licensed camps with addresses and coordinates, but no sessions or prices.
- **No other open Vermont camp dataset or community project was found.**

## Goals

1. Each 2027 program on the site has dates, ages or grades, cost and a registration link taken from the provider's own 2027 material.
2. The date registration opens is recorded for every organization that announces one.
3. Every 2027 row can be traced to the page it came from and the day it was checked.
4. The work can be repeated each year by one maintainer in a few hours a week during the window.

## Non-goals

- Changing how the site displays programs, beyond the "Registration opening soon" list described below.
- Adding organizations in under-covered counties (upstream issue #2). The same pipeline will serve that later.
- Moving off CSV files (upstream issue #10).
- Fully automatic publishing.

## Anti-goals

- **Showing last year's details as this year's.** 93 current rows carry 2025 dates under `program_year = 2026`; that must not recur.
- **Publishing anything a person has not looked at.**
- **Copying another directory's compilation or a provider's marketing text wholesale.**
- **Hammering small organizations' websites.**

## Constraints

- Data stays in `data/programs.csv` and `data/organizations.csv`, validated by `scripts/validate_data.py` and built by `scripts/build_data_js.py`.
- `programs.csv` gets no new columns; its three blank registration columns are enough. `organizations.csv` gets two (see "Registration dates").
- `program_id` values are never reused or renamed.
- Scripts use the Python standard library; requests identify themselves and pause between calls, as the existing scripts do.
- Volunteers with no coding background must be able to contribute, in sittings as short as ten minutes, using only a Google account.
- Everything is built in Melvillian's fork. Upstream is not consulted until merge time, which Melvillian handles.

## Approaches

**A. Manual entry, as in 2026.** Volunteers visit each site and type rows into the admin page or a shared sheet. Accurate when done, and needs nothing built. It produced the 2026 data, including the 93 stale rows and the blank registration dates, and it does not tell anyone *when* a site has posted.

**B. A scraper per platform.** Deterministic code for Rec1, UltraCamp, MyRec and so on. Rec1 alone would cover about a quarter of programs. Everything else is a different site each time, so this cannot reach the 65-site long tail, and the Rec1 scraper has to drive a real browser past bot protection.

**C. Ask providers.** Request an export from the three municipal recreation departments on Rec1, and offer every provider a form to submit their own sessions. Best data and best relationships, and the only clean route for login-gated platforms. Response will be partial and slow, so it cannot be the only route.

**D. Assisted extraction with human review (recommended as the spine).** A script watches each organization's camp page and flags when 2027 content appears. For flagged pages, Claude reads the page or PDF and proposes rows in the project's schema, each with the source URL and the sentence it was taken from. A person reviews the proposals before they are merged. This handles any layout, including PDFs, and records provenance by construction. It can misread a page, which is why nothing merges unreviewed.

**Chosen (Melvillian, 2026-11-20):** D for all organizations, with C in parallel for the three Rec1 towns and B only as a fallback for Rec1 if no export arrives by early January. A remains available at every step, since a volunteer can always type a row.

## Design

Five stages, each a small script or a manual step. Scripts and reviewed data live in git; volunteer work happens in one Google Sheet.

### 1. Source list

A new `data/sources.csv`, one row per organization page worth watching:

`org_id, listing_url, kind, robots_ai_optout, expected_open, last_checked, last_hash, status_2027, notes`

- `kind` is `html`, `pdf`, `rec1`, `gated` or `manual`.
- `status_2027` is `not_posted`, `posted`, `extracted`, `merged`, `changed` or `not_running`.
- `expected_open` is seeded from the 2026 dates above, to order the work.

Seeding it is the first job, and includes repairing the 21 dead links (the YMCA's 16 programs point at a page that no longer exists).

### 2. Watch

`scripts/watch_sources.py` fetches each `listing_url` at most once a week, stores a hash of the visible text, and sets `status_2027 = posted` when the text changes and mentions 2027. It prints a short list of what changed. It makes one request per organization per run.

### 3. Extract

For each `posted` source, an extraction step writes proposed rows to `data/staging/2027/<org_id>.csv`. Columns are those of `programs.csv` plus `source_url`, `source_quote` and `extracted_on`.

- `html` and `pdf`: Claude, running locally in Claude Code as a project skill, reads the fetched page and fills the schema. Fields not stated on the page are left blank, never guessed. Descriptions are written fresh in one or two neutral sentences.
- `rec1`: from a department's export if we have one. The browser-driven fallback is not built in this round; if no export has arrived by early January it is decided then, and until then the Rec1 towns are `enter_new` tasks.
- `gated` and `manual`: no draft is produced; a volunteer enters the rows in the Sheet from scratch.
- Sites whose `robots.txt` names AI crawlers (`robots_ai_optout`) are treated as `manual`: Claude does not read them.

Organization-level facts found on the way (registration dates, financial aid) are proposed in `data/staging/2027/_organizations.csv`.

### 3a. Registration dates

Most organizations announce when registration opens before they list any sessions, so these dates live on the organization, not on its programs. `organizations.csv` already has `registration_opens`; it gains two columns after it:

| Column | Meaning |
|---|---|
| `registration_opens` | The date general registration opens, `YYYY-MM-DD`. |
| `registration_opens_early` | The first date anyone can register or apply: residents, members, or the start of a lottery window. Blank when there is no early date. |
| `registration_notes` | One line of plain text for what the dates do not say, e.g. `Residents Feb 4; non-residents Feb 11` or `Lottery applications Jan 8–12`. |

The Camps page gets a "Registration opening soon" list above the results. It shows each organization whose `registration_opens_early` or `registration_opens` falls between 30 days ago and any future date, soonest first, with the organization's name linked to its website, the date or dates, and the note. A date already passed is labelled "Open now". The list shows eight entries with a "Show all" control, and is absent when empty. Program-level registration dates keep working as they do today.

### 4. Volunteer review in the Google Sheet

All volunteer work happens in one Google Sheet, "Camp finder tasks":
`https://docs.google.com/spreadsheets/d/11fUBvE0_Vwm0ktqbYo-yE3rxMrpmpFQUYXMhSLoahHc`

One task is one organization. There are three kinds, sized so a volunteer can pick by the time they have:

| Kind | Time | What the volunteer does |
|---|---|---|
| `find_page` | 2 min | The link is dead or missing. Find the camp's current summer page and paste its URL. |
| `check_draft` | 10 min | Compare the drafted rows with the camp's page. Fix what is wrong, mark invented rows for deletion. |
| `enter_new` | 30–60 min | No draft exists. Type the camp's sessions in from its page. |
| `check_changes` | 10 min | The camp's page changed after its rows went live. Update the rows to match. |

The Sheet has four tabs:

- **Tasks** — one row per task: `task_id, org_id, org_name, kind, minutes, due, source_url, status, claimed_by, claimed_on, volunteer_notes`. `due` is the day that camp's registration is expected to open; the tab is sorted by it. `status` is `open`, `claimed`, `done` or `needs_help`. Volunteers edit only the last four columns.
- **Drafts** — one row per proposed program: `task_id, row_id, review` (`ok`, `fixed` or `delete`), then the `programs.csv` columns, then `source_quote`. Volunteers edit the program columns and `review`.
- **Orgs** — one row per organization with the registration facts to confirm: `task_id, org_id, registration_opens, registration_opens_early, registration_notes, financial_aid_available, financial_aid_notes, website`.
- **How to** — one screen of instructions with a worked example.

A volunteer claims a task by typing their name and setting `status` to `claimed`, does it, and sets `status` to `done`. A claim older than three days with no `done` goes back to `open` on the next sync.

### 4a. Sheet sync

`scripts/sheet_sync.py` has two commands.

`pull`:
1. Downloads each tab as CSV. The Sheet is readable without credentials through its CSV export URL (verified today).
2. Saves the tabs under `data/staging/2027/sheet/`, for the maintainer to commit, so every sync is a dated snapshot and a vandalised Sheet can be restored from the last good one.
3. Compares with the previous snapshot and prints what changed: tasks claimed, tasks done, rows edited.
4. For every task newly marked `done`, runs the project validator's rules on its rows. Valid tasks are written to `data/staging/2027/<org_id>.csv`, ready to merge. Invalid ones are listed with the reason, and flipped to `needs_help` on the next push.

`push`:
1. Adds new tasks and their draft rows.
2. Rewrites only the columns the script owns (everything on **Tasks** except the four volunteer columns), and never touches a **Drafts** row once it has been pushed. From that point the Sheet is the source of truth for that row until it is merged.
3. Releases expired claims and applies `needs_help` flags.

Writing to the Sheet needs credentials, which do not exist yet. For now `push` writes the rows it would add or change to `data/staging/2027/sheet/_to_paste.csv`, and a maintainer pastes them into the Sheet. That is all that gets built in this round. Writing directly through the Google Sheets API is deferred until Melvillian creates the service-account key; it needs token signing that the Python standard library cannot do, so the choice of how (one optional dependency, or calling `openssl`) is made then. `push` keeps "work out what to write" separate from "write it", so the direct path can be added without touching the rest.

A maintainer looks over each ready staging file before merging; that is the final check, and it is the reason an openly editable Sheet is tolerable.

### 4b. Task board page

A new `volunteer.html` on the site, linked from the nav, shows:

- progress: organizations done out of the total, and how many 2027 programs are live;
- open tasks in three groups by time (2, 10, 30–60 minutes), each sorted by `due`, each with the camp's name, the due date and a button that opens the Sheet at that task's row;
- a short "how this works" with the same worked example as the Sheet.

The page reads the **Tasks** tab live from the Sheet in the visitor's browser (Google's CSV endpoint allows this from the Pages origin; verified today), so it is current without a rebuild. If that request fails it falls back to the last committed snapshot and says how old it is. Claimed tasks show as "claimed", without the volunteer's name, so no personal details are published on the site.

### 4c. Reminder to secure the Sheet

The Sheet is currently editable by anyone with the link. Melvillian accepted this for the build period and asked for a loud reminder.

`data/ingest_config.json` records `{"season": 2027, "sheet_id": "...", "tasks_gid": "...", "public_edit": true, "secure_by": "2026-12-20"}`. A test in the Python suite fails on or after `secure_by` while `public_edit` is still `true`, with the message:

> REMINDER: the volunteer Google Sheet is still editable by anyone with the link. Restrict editing, set up the service-account key, then set "public_edit" to false in data/ingest_config.json.

It passes before that date, and passes permanently once `public_edit` is `false`.

### 5. Merge

Every drafted row is given its permanent `program_id` when it is first created, of the form `<org_id>-<name-slug>-2027`, with `-2`, `-3` and so on when an organization repeats a name. That value is the row's `row_id` in the Sheet and is never regenerated, so a volunteer can correct the name or dates without changing the row's identity. Rows a volunteer adds by hand get their id on the next pull.

`scripts/merge_staging.py` writes the rows of a ready staging file into `programs.csv` by `program_id`: a new id is appended, an existing id is updated in place, and a row marked `delete` is set to `confidence = inactive`, never removed. Merged rows get `program_year = 2027`, `confidence = confirmed` and `verified_date` set to the review date. It then runs the validator and the build.

### 6. Changes after merge

Providers often post dates first and prices or extra sessions later. The watch keeps running on merged organizations. When a merged organization's page changes, `status_2027` goes to a new value, `changed`, and the next push creates a `check_changes` task (10 minutes): the Sheet shows the organization's current rows, and the volunteer updates them against the page. Nothing is re-extracted over a reviewed row automatically.

2026 rows are left untouched. With the season notice from the bug-fix plan, they show as last season's until an organization's 2027 rows exist.

### Schedule

| When | Work |
|---|---|
| Now to mid-December | Build `sources.csv`, the watch, sync and merge scripts, the Sheet tabs and the task board; push `find_page` tasks for the 21 dead links; draft the export request for the three Rec1 towns |
| Mid-December to January | Weekly watch runs; extract as pages appear, in order of `expected_open` |
| First week of January | Registration-open dates recorded for the lottery and early-January organizations |
| February to mid-March | Long tail; specialty camps that post late |

### How we will know it worked

- Registration-open dates are on the site for the ten largest organizations before their registration opens.
- By February 1, 2027, organizations that held at least 60% of 2026's programs have 2027 rows.
- No 2027 row has a date outside 2027, and every one has a `verified_date` and a staging row with a source.

## Dependencies

The site bug-fix plan (`docs/superpowers/plans/2026-11-20-site-bug-fixes.md`) lands first. This work builds on its test setup, its stable program ids and its handling of last season's programs.

## Risks

- **Rec1 access.** If no department provides an export and the browser route breaks, about a quarter of programs fall back to manual entry.
- **Misread pages.** Mitigated by the quoted source sentence and mandatory review; unmitigated if review is skipped under time pressure.
- **Sites that opt out of AI crawlers.** See the decision below.
- **Licence of the DCF dataset.** Copying its rows into our data may place share-alike obligations on the combined dataset, so this design uses it only to discover organizations.

## Decisions

Made by Melvillian on 2026-11-20:

1. **Approach:** D, with C alongside and B as a Rec1 fallback.
2. **Volunteer input:** a Google Sheet, because most people already have a Google account. He created it and shared the link.
3. **Sheet access for now:** editable by anyone with the link, with a failing test in one month as the reminder to lock it down. He will set up the service-account key later.
4. **Upstream:** all work happens in the fork without consulting upstream; he handles the upstream merge.
5. **"Go with the defaults" for the rest**, which were:
   - extraction runs locally in Claude Code;
   - sites that name AI crawlers in `robots.txt` are entered by hand;
   - Claude drafts the export request to the three recreation departments; who sends it is still open (see below).

Made by Melvillian on 2026-11-20, after an independent review of this document:

6. **Registration dates** are stored per organization and shown in a "Registration opening soon" list on the Camps page.
7. **This document is approved.**

Settled in the bug-fix work (merged 2026-11-20): an organization's 2026 rows stay visible, marked with their year, until that organization has 2027 rows.

Claude's calls, not yet confirmed:

- Direct Sheet writes and the Rec1 browser fallback are deferred, not built in this round.
- A drafted row's permanent id is assigned at extraction; merge is by id; page changes after merge create a volunteer task.

- The task board is a page on the camp finder site that reads the Sheet live.
- The tab and column layout of the Sheet.
- Snapshots of the Sheet are saved on every pull for the maintainer to commit.
- The meaning of the three registration fields, and the 30-day and eight-entry limits of the list.
- Claims expire after three days.
- Until the service-account key exists, new tasks reach the Sheet by a maintainer pasting a generated file.

Still open:

- **Who sends the export request** to Burlington, South Burlington and Colchester. Nothing else is blocked on it.

## Left to the builder

- The exact change-detection rule in the watch script.
- The prompt and output checking for the extraction step.
- Whether the admin page gets a view of staging files.
- Layout and styling of `volunteer.html`, following the existing pages.
- Wording of the "How to" tab.
