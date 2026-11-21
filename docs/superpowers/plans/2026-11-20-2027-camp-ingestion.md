# 2027 Camp Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pipeline that gets 2027 camp listings and registration dates onto the site: watch each organization's page, draft rows, have volunteers check them in a Google Sheet, and merge the checked rows.

**Architecture:** Small standard-library Python scripts joined by CSV files in git (`data/sources.csv`, `data/staging/2027/`). Volunteers work only in one Google Sheet, which scripts read through its public CSV export and write to through a paste file until credentials exist. The site gains a "Registration opening soon" list and a volunteer task board that reads the Sheet live.

**Tech Stack:** Python 3 standard library, vanilla JS (`lib.js` helpers, no modules), Node built-in test runner, `unittest`, the headless-Chrome smoke script, Google Sheets CSV export.

**Spec:** `docs/superpowers/specs/2026-11-20-2027-camp-ingestion-design.md`

## Global Constraints

- No new dependencies: no `pip install`, no `npm install`.
- Every script that makes HTTP requests sends `User-Agent: CampFinderVT/1.0 (+https://github.com/kefortney/Camp-and-Afterschool-Care-Finder)`, waits at least 2 seconds between requests, and has a 20-second timeout.
- Tests never use the network. Network code is one thin function per script that tests replace.
- `programs.csv` gets no new columns. `organizations.csv` gets exactly two: `registration_opens_early` and `registration_notes`, immediately after `registration_opens`.
- A `program_id` is assigned once and never regenerated: `<org_id>-<name-slug>-2027`, with `-2`, `-3`… on repeats within an organization.
- Nothing reaches `programs.csv` or `organizations.csv` except through `scripts/merge_staging.py`.
- Sites with `robots_ai_optout = TRUE` in `data/sources.csv` are never read by Claude; they become `enter_new` tasks.
- Scripts do not run `git` commands. They print what changed and the maintainer commits.
- Season is read from `data/ingest_config.json` (`"season": 2027`), never hard-coded.
- Sheet id: `11fUBvE0_Vwm0ktqbYo-yE3rxMrpmpFQUYXMhSLoahHc`. Tab names, exactly: `Tasks`, `Drafts`, `Orgs`, `How to`.
- Reminder message, exactly: `REMINDER: the volunteer Google Sheet is still editable by anyone with the link. Restrict editing, set up the service-account key, then set "public_edit" to false in data/ingest_config.json.`
- Work on branch `feat/2027-ingestion`, one commit per task. `data.js` changes only by running `python3 scripts/build_data_js.py`.

## Review Focus

1. A volunteer leaves a required cell blank or types a date as `7/6/2027`: the task is reported with the reason and not staged; the other tasks in the same pull are unaffected. (Task 7)
2. Someone deletes or reorders columns in the Sheet, or wipes a tab: `pull` stops with a message naming the missing column and does not overwrite the last snapshot. (Task 7)
3. A watched page returns 404, times out, or redirects to a different domain: the row is flagged for a person, `status_2027` and `last_hash` are unchanged, and the run continues. (Task 5)
4. Two programs from one organization share a name, or a volunteer renames a drafted program: ids stay unique and the row keeps the id it was given. (Task 6)
5. An organization has a registration date in the past by more than 30 days, or only an early date: it is left out of, or shown correctly in, the "Registration opening soon" list. (Task 3)

## File Structure

| File | Responsibility |
|---|---|
| `data/ingest_config.json` (new) | Season, Sheet id, Tasks tab gid, `public_edit`, `secure_by`. |
| `scripts/ingest_config.py` (new) | Load the config; `reminder_due`. |
| `data/sources.csv` (new) | One row per organization page to watch. |
| `scripts/sources.py` (new) | Read, write and check `sources.csv`; seeding helpers. |
| `scripts/watch_sources.py` (new) | Fetch pages, detect 2027 content and changes. |
| `scripts/staging.py` (new) | Staging file format, id assignment, row checks, CLI to add a draft. |
| `scripts/merge_staging.py` (new) | Upsert staged rows into the two CSVs; run validator and build. |
| `scripts/sheet_sync.py` (new) | `pull` (snapshot, diff, stage) and `push` (paste files). |
| `scripts/validate_data.py` | Gains importable row checks used by `staging.py`. |
| `lib.js` | Gains `registrationList`, `parseCsv`, `groupTasks`. |
| `app.js`, `index.html` | Render the "Registration opening soon" list. |
| `volunteer.html`, `volunteer.js` (new) | Task board. |
| `.claude/skills/extract-camp-listings/SKILL.md` (new) | How Claude drafts rows from a posted page. |
| `docs/ingestion/` (new) | Runbook, volunteer how-to text, export-request letter. |

Full test command:

```bash
node --test tests/js/ && python3 -m unittest discover -s tests/py -v && python3 scripts/smoke_test.py
```

---

### Task 1: Config and the reminder to secure the Sheet

**Files:**
- Create: `data/ingest_config.json`, `scripts/ingest_config.py`, `tests/py/test_ingest_config.py`

**Interfaces:**
- Produces: `data/ingest_config.json` =
  `{"season": 2027, "sheet_id": "11fUBvE0_Vwm0ktqbYo-yE3rxMrpmpFQUYXMhSLoahHc", "tasks_gid": "0", "public_edit": true, "secure_by": "2026-12-20"}`
- Produces: `ingest_config.load() -> dict`; `ingest_config.reminder_due(config: dict, today: datetime.date) -> str | None` returning the reminder message from Global Constraints when `config["public_edit"]` is true and `today >= secure_by`, otherwise `None`.

- [ ] **Step 1: Write the failing tests**

```python
import datetime as dt, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ingest_config as ic

MSG = ('REMINDER: the volunteer Google Sheet is still editable by anyone with the link. '
       'Restrict editing, set up the service-account key, then set "public_edit" to false '
       'in data/ingest_config.json.')
CFG = {"public_edit": True, "secure_by": "2026-12-20"}

class Reminder(unittest.TestCase):
    def test_quiet_before_the_date(self):
        self.assertIsNone(ic.reminder_due(CFG, dt.date(2026, 12, 19)))
    def test_due_on_and_after_the_date(self):
        self.assertEqual(ic.reminder_due(CFG, dt.date(2026, 12, 20)), MSG)
        self.assertEqual(ic.reminder_due(CFG, dt.date(2027, 3, 1)), MSG)
    def test_quiet_once_secured(self):
        self.assertIsNone(ic.reminder_due({**CFG, "public_edit": False}, dt.date(2027, 3, 1)))
    def test_config_file_has_the_expected_keys(self):
        cfg = ic.load()
        self.assertEqual(cfg["season"], 2027)
        self.assertEqual(cfg["sheet_id"], "11fUBvE0_Vwm0ktqbYo-yE3rxMrpmpFQUYXMhSLoahHc")
        self.assertEqual(cfg["secure_by"], "2026-12-20")

class SheetIsSecured(unittest.TestCase):
    def test_the_volunteer_sheet_is_secured_by_the_deadline(self):
        """Fails loudly from secure_by onward until public_edit is false."""
        due = ic.reminder_due(ic.load(), dt.date.today())
        self.assertIsNone(due, "\n\n" + "=" * 78 + "\n" + str(due) + "\n" + "=" * 78 + "\n")
```

- [ ] **Step 2: Run** `python3 -m unittest discover -s tests/py -v` — Expected: `ModuleNotFoundError: No module named 'ingest_config'`.
- [ ] **Step 3: Create the config file and `scripts/ingest_config.py`.**
- [ ] **Step 4: Run** the same command — Expected: PASS (today is before 2026-12-20).
- [ ] **Step 5: Commit** — `git add data/ingest_config.json scripts/ingest_config.py tests/py/test_ingest_config.py && git commit -m "feat: ingest config and a dated reminder to secure the volunteer Sheet"`

---

### Task 2: Registration dates on organizations

**Files:**
- Modify: `data/organizations.csv` (two new blank columns), `scripts/validate_data.py` (`validate_orgs`), `scripts/build_data_js.py` (`build_org_obj`), `admin.html` (`ORG_HEADERS`, `orgToCsvRow`, `blankOrg`, org edit form and its load/save), `camp-finder-data-architecture.md` (organizations schema table), `data.js` (regenerated)
- Test: `tests/py/test_build_data_js.py`, `tests/py/test_validate_orgs.py` (new)

**Interfaces:**
- Produces: `ORGANIZATIONS[i].registrationOpens`, `.registrationOpensEarly`, `.registrationNotes` (strings, `""` when blank) in `data.js`.
- Produces: `validate_data.check_org_registration(row: dict, label: str) -> list[str]` — error strings when `registration_opens` or `registration_opens_early` is non-blank and not `YYYY-MM-DD`, or when early is after opens.

- [ ] **Step 1: Write the failing tests**

```python
# tests/py/test_build_data_js.py (add)
class OrgRegistration(unittest.TestCase):
    def test_org_object_carries_registration_fields(self):
        o = b.build_org_obj({"org_id": "o1", "org_name": "Org", "registration_opens": "2027-02-04",
                             "registration_opens_early": "2027-01-28", "registration_notes": "Residents first"})
        self.assertEqual((o["registrationOpens"], o["registrationOpensEarly"], o["registrationNotes"]),
                         ("2027-02-04", "2027-01-28", "Residents first"))
    def test_blank_fields_are_empty_strings(self):
        o = b.build_org_obj({"org_id": "o1", "org_name": "Org"})
        self.assertEqual((o["registrationOpensEarly"], o["registrationNotes"]), ("", ""))

# tests/py/test_validate_orgs.py
import validate_data as v
class OrgRegistrationChecks(unittest.TestCase):
    def test_good_and_blank_rows_pass(self):
        self.assertEqual(v.check_org_registration({"registration_opens": "2027-02-04", "registration_opens_early": "2027-01-28"}, "x"), [])
        self.assertEqual(v.check_org_registration({}, "x"), [])
    def test_bad_date_format(self):
        self.assertEqual(len(v.check_org_registration({"registration_opens": "2/4/2027"}, "x")), 1)
    def test_early_after_opens(self):
        self.assertEqual(len(v.check_org_registration({"registration_opens": "2027-02-04", "registration_opens_early": "2027-02-11"}, "x")), 1)
```

Add to `scripts/smoke_test.py`: `check("org csv has registration columns", "registration_opens,registration_opens_early,registration_notes," in (ROOT / "data/organizations.csv").read_text(encoding="utf-8").splitlines()[0])`.

- [ ] **Step 2: Run** the full test command — Expected: the three new groups FAIL.
- [ ] **Step 3: Add the two columns to `data/organizations.csv`** with a short throwaway `csv.DictReader`/`DictWriter` snippet (not by hand), keeping every other value byte-identical; `git diff --stat data/organizations.csv` should show each line changed once.
- [ ] **Step 4: Implement `check_org_registration`, call it from `validate_orgs` (adding its results as errors), and emit the two fields from `build_org_obj`.**
- [ ] **Step 5: Admin page.** Add both column names to `ORG_HEADERS` after `registration_opens`; map them in `orgToCsvRow` from `registrationOpensEarly` / `registrationNotes`; add them to `blankOrg()`; add two inputs to the organization edit form (`type="date"` for the early date, `type="text"` for notes) next to the existing registration-opens input, loaded and saved with the form. Without this, "Save All" would drop the columns.
- [ ] **Step 6: Update the organizations schema table** in `camp-finder-data-architecture.md` with the three meanings from the spec's "Registration dates" table.
- [ ] **Step 7: Rebuild and run everything** — `python3 scripts/build_data_js.py` then the full test command. Expected: all pass.
- [ ] **Step 8: Commit** — `git commit -m "feat: per-organization registration dates"` with the files above.

---

### Task 3: "Registration opening soon" list

**Files:**
- Modify: `lib.js`, `tests/js/lib.test.js`, `app.js` (`init`), `index.html` (add `<section id="regSoon" class="reg-soon hidden" aria-label="Registration opening soon"></section>` directly above the filter panel), `styles.css`, `tests/fixtures/data.fixture.js`, `scripts/smoke_test.py`

**Interfaces:**
- Consumes: the organization fields from Task 2; `CampLib.escapeHtml`, `CampLib.safeUrl`.
- Produces: `CampLib.registrationList(orgs: object[], todayIso: string) -> {orgId, name, website, sortDate, early, opens, notes, openNow}[]`
  - An organization is included when at least one of `registrationOpensEarly`, `registrationOpens` is a valid ISO date on or after `todayIso` minus 30 days.
  - `sortDate` is the earliest of its dates that is on or after `todayIso`; if none is, the latest of its dates.
  - `openNow` is true when its earliest date is on or before `todayIso`.
  - Sorted by `sortDate`, then `name`.

- [ ] **Step 1: Write the failing tests**

```js
const T2 = '2027-01-20';
const O = (orgId, early, opens, notes = '') => ({ orgId, name: orgId.toUpperCase(), website: 'https://example.org/' + orgId,
  registrationOpensEarly: early, registrationOpens: opens, registrationNotes: notes });

test('registrationList picks, labels and sorts', () => {
  const list = registrationList([
    O('later', '', '2027-02-11'),
    O('lottery', '2027-01-08', '2027-02-03', 'Lottery Jan 8–12'),   // early passed, general upcoming
    O('soon', '2027-01-26', '2027-02-09'),
    O('longago', '', '2026-12-01'),                                 // more than 30 days ago
    O('recent', '', '2027-01-05'),                                  // opened 15 days ago
    O('blank', '', ''),
    O('bad', '', '2/4/2027'),
  ], T2);
  assert.deepEqual(list.map(x => [x.orgId, x.sortDate, x.openNow]), [
    ['recent', '2027-01-05', true],
    ['soon', '2027-01-26', false],
    ['lottery', '2027-02-03', true],
    ['later', '2027-02-11', false],
  ]);
  assert.equal(list[2].notes, 'Lottery Jan 8–12');
});
test('registrationList: only an early date', () => {
  assert.deepEqual(registrationList([O('e', '2027-01-25', '')], T2).map(x => [x.sortDate, x.openNow]), [['2027-01-25', false]]);
});
test('registrationList: boundary is 30 days inclusive', () => {
  assert.equal(registrationList([O('a', '', '2026-12-21')], T2).length, 1);
  assert.equal(registrationList([O('a', '', '2026-12-20')], T2).length, 0);
});
```

Fixture: give `ORGANIZATIONS` one organization `reg-org` (name `Reg Org`, website `https://example.org/reg`) with `registrationOpens: "2099-02-04"`, `registrationOpensEarly: "2099-01-28"`, `registrationNotes: "<b>Residents</b> first"`. Smoke checks:

```python
dom = dump_dom("index.html")
check("reg list: shown", 'id="regSoon" class="reg-soon"' in dom and "Registration opening soon" in dom)
check("reg list: org and dates", "Reg Org" in dom and "Jan 28" in dom and "Feb 4" in dom)
check("reg list: note escaped", "&lt;b&gt;Residents&lt;/b&gt; first" in dom)
dom = dump_dom("afterschool.html")
check("reg list: camps page only", 'id="regSoon"' not in dom)
```

- [ ] **Step 2: Run** both suites — Expected: Node `registrationList is not a function`; four smoke checks FAIL.
- [ ] **Step 3: Implement `registrationList` in `lib.js`.**
- [ ] **Step 4: Render it from `init()` in `app.js`** when `#regSoon` exists: heading `Registration opening soon`; one row per entry with the name as a link (only when `safeUrl(website)` is non-empty), `Open now` when `openNow`, the early date as `Early: Jan 28` and the general date as `Opens Feb 4` (existing `MONTH_ABBR`), and the note. Show the first eight; when there are more, a `Show all N` button reveals the rest. Leave the section hidden when the list is empty. All text through `escapeHtml`.
- [ ] **Step 5: Style `.reg-soon`** like the existing export panel card.
- [ ] **Step 6: Run the full test command** — Expected: all pass.
- [ ] **Step 7: Commit** — `git commit -m "feat: registration opening soon list on the Camps page"`.

---

### Task 4: The source list

**Files:**
- Create: `scripts/sources.py`, `tests/py/test_sources.py`, `data/sources.csv`

**Interfaces:**
- Produces: `sources.COLUMNS = ["org_id", "listing_url", "kind", "robots_ai_optout", "expected_open", "last_checked", "last_hash", "status_2027", "notes"]`; `KINDS = {"html", "pdf", "rec1", "gated", "manual"}`; `STATUSES = {"not_posted", "posted", "extracted", "merged", "changed", "not_running"}`.
- Produces: `sources.load(path=SOURCES_CSV) -> list[dict]`, `sources.save(rows, path=SOURCES_CSV) -> None` (columns in `COLUMNS` order), `sources.check(rows, org_ids: set[str]) -> list[str]` (unknown org, duplicate org, bad kind/status, bad date, non-http URL).
- Produces: `sources.pick_listing_url(org: dict, program_urls: list[str]) -> str` — the most common non-blank program URL, ties broken by first seen; otherwise the organization's `website`; otherwise `""`.
- Produces: `sources.guess_kind(url: str) -> str` — `pdf` when the path ends `.pdf`; `rec1` when the host is `secure.rec1.com` or `summerbtvparks.com`; `manual` when blank; otherwise `html`.
- Produces: `sources.names_ai_crawler(robots_txt: str) -> bool` — true when any `User-agent` line names `GPTBot`, `ClaudeBot`, `anthropic-ai`, `CCBot`, `Google-Extended` or `PerplexityBot` (case-insensitive).
- Produces CLI: `python3 scripts/sources.py seed` (writes `data/sources.csv` from the two CSVs, one row per organization that is not `inactive`, `status_2027 = not_posted`, never overwriting an existing row), `robots` (fetches each distinct host's `robots.txt` once and fills `robots_ai_optout` with `TRUE`/`FALSE`, leaving blank on failure), `check`.

- [ ] **Step 1: Write the failing tests**

```python
import sources as s
class Seeding(unittest.TestCase):
    def test_pick_listing_url(self):
        org = {"website": "https://org.example"}
        self.assertEqual(s.pick_listing_url(org, ["https://a/x", "https://a/y", "https://a/y", ""]), "https://a/y")
        self.assertEqual(s.pick_listing_url(org, ["https://a/x", "https://a/y"]), "https://a/x")
        self.assertEqual(s.pick_listing_url(org, []), "https://org.example")
        self.assertEqual(s.pick_listing_url({}, []), "")
    def test_guess_kind(self):
        self.assertEqual(s.guess_kind("https://x.org/camp.PDF"), "pdf")
        self.assertEqual(s.guess_kind("https://secure.rec1.com/VT/burlington-vt/catalog"), "rec1")
        self.assertEqual(s.guess_kind("https://summerbtvparks.com/"), "rec1")
        self.assertEqual(s.guess_kind(""), "manual")
        self.assertEqual(s.guess_kind("https://x.org/camps"), "html")
    def test_names_ai_crawler(self):
        self.assertTrue(s.names_ai_crawler("User-agent: GPTBot\nDisallow: /\n"))
        self.assertTrue(s.names_ai_crawler("user-agent:  claudebot\nDisallow: /"))
        self.assertFalse(s.names_ai_crawler("User-agent: *\nCrawl-delay: 10\n"))
        self.assertFalse(s.names_ai_crawler(""))
class Checking(unittest.TestCase):
    def row(self, **kw):
        return {**dict.fromkeys(s.COLUMNS, ""), "org_id": "o1", "kind": "html", "status_2027": "not_posted",
                "listing_url": "https://x.org", **kw}
    def test_clean_row(self):
        self.assertEqual(s.check([self.row()], {"o1"}), [])
    def test_problems_are_reported(self):
        rows = [self.row(), self.row(), self.row(org_id="nope"), self.row(org_id="o2", kind="web"),
                self.row(org_id="o3", status_2027="done"), self.row(org_id="o4", expected_open="Jan 5"),
                self.row(org_id="o5", listing_url="ftp://x")]
        self.assertEqual(len(s.check(rows, {"o1", "o2", "o3", "o4", "o5"})), 6)
    def test_save_load_round_trip(self):
        import tempfile, pathlib
        p = pathlib.Path(tempfile.mkdtemp()) / "s.csv"
        s.save([self.row(notes="a, b")], p)
        self.assertEqual(s.load(p), [self.row(notes="a, b")])
```

- [ ] **Step 2: Run** `python3 -m unittest discover -s tests/py -v` — Expected: `No module named 'sources'`.
- [ ] **Step 3: Implement `scripts/sources.py`.**
- [ ] **Step 4: Run the tests** — Expected: PASS.
- [ ] **Step 5: Seed.** Run `python3 scripts/sources.py seed`, then `python3 scripts/sources.py robots`, then `check`. Expected: about 147 rows; `check` prints no problems. Fill `expected_open` by hand for the organizations in the spec's timing table, using the same month and day in 2027 (for example Williston `2027-01-12`, Burlington Parks `2027-02-04`).
- [ ] **Step 6: Commit** — `git add scripts/sources.py tests/py/test_sources.py data/sources.csv && git commit -m "feat: source list of organization pages to watch"`.

---

### Task 5: The watch script

**Files:**
- Create: `scripts/watch_sources.py`, `tests/py/test_watch_sources.py`

**Interfaces:**
- Consumes: `sources.load/save`, `ingest_config.load()`.
- Produces: `visible_text(html: str) -> str` (tags, scripts, styles removed; whitespace collapsed), `content_hash(data: bytes | str) -> str` (first 16 hex chars of SHA-256).
- Produces: `FetchResult = namedtuple("FetchResult", "ok status final_url content_type body error")`; `fetch(url: str) -> FetchResult` (the only network function).
- Produces: `assess(row: dict, result: FetchResult, season: int, today: str) -> tuple[dict, str | None]` — the updated row and a one-line message or `None`:
  - not `ok`, or final host differs from the requested host: row unchanged except `notes` gains `fetch failed <today>: <reason>`; message `NEEDS A PERSON: …`.
  - `kind` not `html`/`pdf`: never fetched; `assess` is not called.
  - otherwise set `last_checked = today`, `last_hash` = hash of visible text (HTML) or bytes (PDF). Then:
    - status `not_posted` and the text contains the season year as a whole word → `posted`, message `POSTED: <org_id>`. PDFs count as mentioning the season when the URL or the bytes contain it.
    - status `merged` and the hash differs from the previous non-blank hash → `changed`, message `CHANGED: <org_id>`.
    - any other case: status unchanged, no message.
- Produces CLI: `python3 scripts/watch_sources.py [--org ORG_ID] [--limit N] [--dry-run] [--force]` — skips rows checked within the last 6 days unless `--force`; saves after every 10 rows; prints messages and a summary line.

- [ ] **Step 1: Write the failing tests**

```python
import watch_sources as w
R = lambda body, **kw: w.FetchResult(**{"ok": True, "status": 200, "final_url": "https://x.org/camps",
        "content_type": "text/html", "body": body, "error": "", **kw})
ROW = lambda **kw: {"org_id": "o1", "listing_url": "https://x.org/camps", "kind": "html", "status_2027": "not_posted",
                    "last_hash": "", "last_checked": "", "notes": "", **kw}
class Text(unittest.TestCase):
    def test_visible_text(self):
        self.assertEqual(w.visible_text("<html><style>a{}</style><script>var y=2027</script><p>Camp  <b>2026</b></p></html>"), "Camp 2026")
    def test_hash_is_stable_and_short(self):
        self.assertEqual(w.content_hash("abc"), w.content_hash(b"abc"))
        self.assertEqual(len(w.content_hash("abc")), 16)
class Assess(unittest.TestCase):
    def test_posted_when_season_appears(self):
        row, msg = w.assess(ROW(), R(b"<p>Summer 2027 camps</p>"), 2027, "2026-12-15")
        self.assertEqual((row["status_2027"], row["last_checked"], msg), ("posted", "2026-12-15", "POSTED: o1"))
    def test_not_posted_stays_when_season_absent_or_only_in_script(self):
        for body in (b"<p>Summer 2026</p>", b"<script>x=2027</script><p>Soon</p>", b"<p>call 802-555-20270</p>"):
            row, msg = w.assess(ROW(), R(body), 2027, "2026-12-15")
            self.assertEqual((row["status_2027"], msg), ("not_posted", None))
    def test_changed_after_merge(self):
        old = w.content_hash(w.visible_text("<p>2027 dates</p>"))
        row, msg = w.assess(ROW(status_2027="merged", last_hash=old), R(b"<p>2027 dates and prices</p>"), 2027, "2027-02-01")
        self.assertEqual((row["status_2027"], msg), ("changed", "CHANGED: o1"))
        row, msg = w.assess(ROW(status_2027="merged", last_hash=old), R(b"<p>2027 dates</p>"), 2027, "2027-02-01")
        self.assertEqual((row["status_2027"], msg), ("merged", None))
    def test_failures_change_nothing_but_notes(self):
        for res in (R(b"", ok=False, status=404, error="HTTP 404"),
                    R(b"<p>2027</p>", final_url="https://parked.example/")):
            row, msg = w.assess(ROW(last_hash="abc"), res, 2027, "2026-12-15")
            self.assertEqual((row["status_2027"], row["last_hash"], row["last_checked"]), ("not_posted", "abc", ""))
            self.assertTrue(msg.startswith("NEEDS A PERSON:"))
            self.assertIn("2026-12-15", row["notes"])
    def test_pdf_uses_bytes_and_url(self):
        row, msg = w.assess(ROW(kind="pdf", listing_url="https://x.org/camp_guide_2027.pdf"),
                            R(b"%PDF-1.7 ...", content_type="application/pdf", final_url="https://x.org/camp_guide_2027.pdf"), 2027, "2026-12-15")
        self.assertEqual(row["status_2027"], "posted")
```

- [ ] **Step 2: Run** — Expected: `No module named 'watch_sources'`.
- [ ] **Step 3: Implement `scripts/watch_sources.py`**, with the User-Agent, delay and timeout from Global Constraints.
- [ ] **Step 4: Run the tests** — Expected: PASS.
- [ ] **Step 5: Try it for real on three rows** — `python3 scripts/watch_sources.py --limit 3 --dry-run`. Expected: three lines or fewer of messages, a summary, and `git status` shows `data/sources.csv` unchanged.
- [ ] **Step 6: Commit** — `git commit -m "feat: watch organization pages for 2027 listings and later changes"`.

---

### Task 6: Staging files, permanent ids and the merge

**Files:**
- Create: `scripts/staging.py`, `scripts/merge_staging.py`, `tests/py/test_staging.py`, `tests/py/test_merge_staging.py`
- Modify: `scripts/validate_data.py` (extract row checks)

**Interfaces:**
- Produces, in `validate_data.py`: `check_program_row(row: dict, org_ids: set[str], label: str) -> tuple[list[str], list[str]]` (errors, warnings) containing the per-row checks now inline in `validate_programs`, which calls it. `python3 scripts/validate_data.py` output is unchanged, line for line.
- Produces, in `staging.py`:
  - `PROGRAM_COLUMNS` (the `programs.csv` header, read from the file), `EXTRA = ["review", "source_url", "source_quote", "extracted_on"]`, `STAGING_COLUMNS = PROGRAM_COLUMNS + EXTRA`.
  - `slugify(text: str) -> str` (lowercase, non-alphanumerics to `-`, collapsed, trimmed, at most 50 characters).
  - `assign_program_id(org_id: str, name: str, season: int, taken: set[str]) -> str`.
  - `staging_path(org_id: str, season: int) -> Path` (`data/staging/<season>/<org_id>.csv`), `load(org_id, season) -> list[dict]`, `save(org_id, season, rows) -> None`.
  - `prepare(rows: list[dict], org_id: str, season: int, taken: set[str], today: str) -> list[dict]` — fills `org_id`, `program_year`, `program_type` (`camp` when blank), `session_type` (`day`), `schedule_type` (`weekly`), `confidence` (`likely`), `extracted_on`, and a `program_id` for rows without one. Rows that already have a `program_id` keep it.
  - `check_rows(rows: list[dict], org_ids: set[str]) -> list[str]` — `check_program_row` errors, plus: a date outside the season year, a blank `source_url` unless `review` is `delete`, a `review` value other than `""`, `ok`, `fixed`, `delete`.
  - CLI: `python3 scripts/staging.py add --org ORG_ID --json FILE` (FILE is a JSON list of row objects; prepares, checks, appends to the staging file, prints the ids; exits 1 and writes nothing when any check fails) and `python3 scripts/staging.py check [--org ORG_ID]`.
- Produces, in `merge_staging.py`:
  - `merge_programs(existing: list[dict], staged: list[dict], today: str) -> tuple[list[dict], dict]` — upsert by `program_id`. A staged row with `review == "delete"`: if the id exists, set its `confidence` to `inactive`; otherwise drop it. Any other staged row: written with `confidence = confirmed`, `verified_date = today`, only `PROGRAM_COLUMNS` kept. Existing rows not staged are untouched and keep their order; new rows are appended. The dict counts `added`, `updated`, `retired`.
  - `merge_orgs(existing: list[dict], staged: list[dict], today: str) -> tuple[list[dict], int]` — for each staged row, copy its non-blank values over the organization's, and set `verified_date`.
  - CLI: `python3 scripts/merge_staging.py --org ORG_ID [--org …] | --all-ready` then runs `validate_data.py` and `build_data_js.py`, and sets those organizations' `status_2027` to `merged` in `sources.csv`. Refuses any organization whose staging file fails `check_rows` or has a row with blank `review`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/py/test_staging.py
import staging as st
class Ids(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(st.slugify("  Arts & Crafts: Week #1! "), "arts-crafts-week-1")
        self.assertLessEqual(len(st.slugify("x" * 200)), 50)
    def test_ids_are_unique_within_an_org(self):
        taken = set()
        a = st.assign_program_id("echo", "Animal Superpowers", 2027, taken); taken.add(a)
        b = st.assign_program_id("echo", "Animal Superpowers", 2027, taken); taken.add(b)
        c = st.assign_program_id("echo", "Animal Superpowers", 2027, taken)
        self.assertEqual((a, b, c), ("echo-animal-superpowers-2027", "echo-animal-superpowers-2027-2", "echo-animal-superpowers-2027-3"))
class Prepare(unittest.TestCase):
    def test_fills_defaults_and_keeps_existing_ids(self):
        rows = st.prepare([{"program_name": "Camp A"}, {"program_name": "Renamed", "program_id": "o1-camp-b-2027"}],
                          "o1", 2027, {"o1-camp-b-2027"}, "2026-12-15")
        self.assertEqual(rows[0]["program_id"], "o1-camp-a-2027")
        self.assertEqual(rows[1]["program_id"], "o1-camp-b-2027")
        self.assertEqual((rows[0]["org_id"], rows[0]["program_year"], rows[0]["program_type"], rows[0]["confidence"],
                          rows[0]["extracted_on"]), ("o1", "2027", "camp", "likely", "2026-12-15"))
class Checks(unittest.TestCase):
    def good(self, **kw):
        return {**dict.fromkeys(st.STAGING_COLUMNS, ""), "program_id": "o1-a-2027", "org_id": "o1", "program_name": "A",
                "program_type": "camp", "program_year": "2027", "session_type": "day", "schedule_type": "weekly",
                "confidence": "likely", "start_date": "2027-07-05", "end_date": "2027-07-09",
                "source_url": "https://x.org/camps", **kw}
    def test_good_row(self):
        self.assertEqual(st.check_rows([self.good()], {"o1"}), [])
    def test_each_problem_is_one_error(self):
        for bad in (dict(start_date="7/5/2027"), dict(start_date="2026-07-05"), dict(source_url=""),
                    dict(review="maybe"), dict(org_id="nope"), dict(program_name="")):
            self.assertEqual(len(st.check_rows([self.good(**bad)], {"o1"})), 1, bad)
    def test_deleted_rows_need_no_source(self):
        self.assertEqual(st.check_rows([self.good(review="delete", source_url="")], {"o1"}), [])

# tests/py/test_merge_staging.py
import merge_staging as m, staging as st
P = lambda pid, **kw: {**dict.fromkeys(st.PROGRAM_COLUMNS, ""), "program_id": pid, "org_id": "o1", "program_name": pid, **kw}
S = lambda pid, **kw: {**P(pid, **kw), **dict.fromkeys(st.EXTRA, ""), **{k: v for k, v in kw.items() if k in st.EXTRA}}
class MergePrograms(unittest.TestCase):
    def test_add_update_retire(self):
        existing = [P("old-2026", confidence="confirmed"), P("b-2027", cost_raw="$100", confidence="confirmed"), P("c-2027", confidence="confirmed")]
        staged = [S("a-2027", review="ok", source_url="https://x"), S("b-2027", review="fixed", cost_raw="$120"),
                  S("c-2027", review="delete"), S("d-2027", review="delete")]
        out, counts = m.merge_programs(existing, staged, "2027-01-10")
        self.assertEqual([r["program_id"] for r in out], ["old-2026", "b-2027", "c-2027", "a-2027"])
        by = {r["program_id"]: r for r in out}
        self.assertEqual((by["b-2027"]["cost_raw"], by["b-2027"]["verified_date"], by["b-2027"]["confidence"]), ("$120", "2027-01-10", "confirmed"))
        self.assertEqual(by["c-2027"]["confidence"], "inactive")
        self.assertEqual(by["old-2026"], existing[0])
        self.assertEqual(set(by["a-2027"]), set(st.PROGRAM_COLUMNS))
        self.assertEqual(counts, {"added": 1, "updated": 1, "retired": 1})
    def test_merging_twice_changes_nothing_more(self):
        staged = [S("a-2027", review="ok")]
        once, _ = m.merge_programs([], staged, "2027-01-10")
        twice, counts = m.merge_programs(once, staged, "2027-01-10")
        self.assertEqual(once, twice)
        self.assertEqual(counts["added"], 0)
class MergeOrgs(unittest.TestCase):
    def test_non_blank_values_overwrite(self):
        existing = [{"org_id": "o1", "website": "https://old", "registration_opens": "", "verified_date": ""}]
        out, n = m.merge_orgs(existing, [{"org_id": "o1", "website": "", "registration_opens": "2027-02-04"}], "2027-01-10")
        self.assertEqual((out[0]["website"], out[0]["registration_opens"], out[0]["verified_date"], n),
                         ("https://old", "2027-02-04", "2027-01-10", 1))
```

- [ ] **Step 2: Run** — Expected: both new modules missing.
- [ ] **Step 3: Extract `check_program_row` in `validate_data.py`.** Before and after: `python3 scripts/validate_data.py > /tmp/before.txt` … `diff` must be empty.
- [ ] **Step 4: Implement `staging.py` and `merge_staging.py`.**
- [ ] **Step 5: Run the tests** — Expected: PASS.
- [ ] **Step 6: Commit** — `git commit -m "feat: staging files with permanent ids, and merge into the data CSVs"`.

---

### Task 7: Sheet sync — pull

**Files:**
- Create: `scripts/sheet_sync.py`, `tests/py/test_sheet_pull.py`, `tests/fixtures/sheet/` (small `tasks.csv`, `drafts.csv`, `orgs.csv`)

**Interfaces:**
- Consumes: `staging.STAGING_COLUMNS`, `staging.check_rows`, `staging.prepare`, `staging.save`, `validate_data.check_org_registration`, `ingest_config.load()`.
- Produces:
  - `TASK_COLUMNS = ["task_id", "org_id", "org_name", "kind", "minutes", "due", "source_url", "status", "claimed_by", "claimed_on", "volunteer_notes"]`; `TASK_KINDS = {"find_page", "check_draft", "enter_new", "check_changes"}`; `TASK_STATUSES = {"open", "claimed", "done", "needs_help"}`.
  - `DRAFT_COLUMNS = ["task_id", "row_id", "review"] + PROGRAM_COLUMNS (without program_id) + ["source_url", "source_quote"]`. `row_id` is the row's `program_id`.
  - `ORG_COLUMNS = ["task_id", "org_id", "registration_opens", "registration_opens_early", "registration_notes", "financial_aid_available", "financial_aid_notes", "website"]`.
  - `tab_url(sheet_id: str, tab: str) -> str` — `https://docs.google.com/spreadsheets/d/<id>/gviz/tq?tqx=out:csv&sheet=<tab, URL-encoded>`.
  - `download_tab(sheet_id, tab) -> str` (the only network function).
  - `parse_tab(text: str, required: list[str], tab: str) -> list[dict]` — raises `SheetError("<tab> tab is missing column(s): …")` when a required column is absent, and `SheetError("<tab> tab is empty")` when there is no header. Blank rows are dropped; values are stripped.
  - `diff_tasks(old: list[dict], new: list[dict]) -> list[str]` — one line per task whose `status` or `claimed_by` changed, or that appeared or disappeared.
  - `collect_done(tasks, drafts, orgs, already_done: set[str], org_ids: set[str], season: int, today: str) -> tuple[dict[str, list[dict]], list[dict], list[tuple[str, str]]]` — for each task with `status == "done"` not in `already_done`: its draft rows converted to staging rows (`row_id` → `program_id`; rows without one get an id from `staging.prepare`); a `find_page` task contributes no rows, only its URL (from `volunteer_notes` or the Orgs `website`); its Orgs row, if any. Tasks whose rows fail `staging.check_rows` or `check_org_registration`, or have a blank `review`, go to the third value as `(task_id, reason)` and contribute nothing.
  - CLI: `python3 scripts/sheet_sync.py pull` — downloads the three tabs; on any `SheetError` exits 1 before writing anything; writes `data/staging/<season>/sheet/{tasks,drafts,orgs}.csv`; prints the diff against the previous snapshot; writes staging files and `_organizations.csv` for ready tasks; applies `find_page` URLs to `sources.csv`; writes rejected tasks to `data/staging/<season>/sheet/_needs_help.csv`.

- [ ] **Step 1: Write the failing tests**

```python
import sheet_sync as ss
class Parse(unittest.TestCase):
    def test_parses_and_strips(self):
        rows = ss.parse_tab("task_id,status\n t1 , done \n,\n", ["task_id", "status"], "Tasks")
        self.assertEqual(rows, [{"task_id": "t1", "status": "done"}])
    def test_missing_column_and_empty_tab(self):
        with self.assertRaisesRegex(ss.SheetError, "Tasks tab is missing column\\(s\\): status"):
            ss.parse_tab("task_id,claimed_by\nt1,x\n", ["task_id", "status"], "Tasks")
        with self.assertRaisesRegex(ss.SheetError, "Drafts tab is empty"):
            ss.parse_tab("", ["task_id"], "Drafts")
    def test_tab_url_encodes_the_name(self):
        self.assertTrue(ss.tab_url("ID", "How to").endswith("/d/ID/gviz/tq?tqx=out:csv&sheet=How%20to"))
class Diff(unittest.TestCase):
    def test_reports_status_and_claim_changes(self):
        old = [{"task_id": "t1", "status": "open", "claimed_by": ""}, {"task_id": "t2", "status": "open", "claimed_by": ""}]
        new = [{"task_id": "t1", "status": "claimed", "claimed_by": "Sam"}, {"task_id": "t3", "status": "open", "claimed_by": ""}]
        lines = ss.diff_tasks(old, new)
        self.assertEqual(len(lines), 3)
        self.assertTrue(any("t1" in l and "claimed" in l for l in lines))
class Collect(unittest.TestCase):
    def setUp(self):
        self.tasks = [{"task_id": "t1", "org_id": "o1", "kind": "check_draft", "status": "done"},
                      {"task_id": "t2", "org_id": "o2", "kind": "check_draft", "status": "done"},
                      {"task_id": "t3", "org_id": "o3", "kind": "check_draft", "status": "claimed"},
                      {"task_id": "t4", "org_id": "o4", "kind": "find_page", "status": "done", "volunteer_notes": "https://o4.org/camps"}]
        d = lambda task, rid, **kw: {**dict.fromkeys(ss.DRAFT_COLUMNS, ""), "task_id": task, "row_id": rid, "org_id": task.replace("t", "o"),
                                     "program_name": "Camp", "start_date": "2027-07-05", "end_date": "2027-07-09",
                                     "source_url": "https://x.org", "review": "ok", **kw}
        self.drafts = [d("t1", "o1-camp-2027"), d("t1", "", program_name="Added by hand"),
                       d("t2", "o2-camp-2027", start_date="7/5/2027"), d("t3", "o3-camp-2027")]
    def run_collect(self, **kw):
        return ss.collect_done(self.tasks, self.drafts, [], kw.get("already", set()), {"o1", "o2", "o3", "o4"}, 2027, "2027-01-10")
    def test_ready_rejected_and_untouched(self):
        staged, orgs, rejected = self.run_collect()
        self.assertEqual(set(staged), {"o1"})
        self.assertEqual([r["program_id"] for r in staged["o1"]], ["o1-camp-2027", "o1-added-by-hand-2027"])
        self.assertEqual([t for t, _ in rejected], ["t2"])
    def test_bad_task_does_not_block_good_one_and_blank_review_rejects(self):
        self.drafts[0]["review"] = ""
        staged, _, rejected = self.run_collect()
        self.assertEqual(set(staged), set())
        self.assertEqual(sorted(t for t, _ in rejected), ["t1", "t2"])
    def test_already_done_tasks_are_skipped(self):
        staged, _, rejected = self.run_collect(already={"t1", "t2"})
        self.assertEqual((staged, rejected), ({}, []))
```

Also a test that the CLI's snapshot step is skipped on error: call the function `pull(download=fake)` with a fake that returns a Tasks tab without `status`, in a temp directory holding a previous snapshot, and assert the previous files are byte-identical afterwards and the return code is 1.

- [ ] **Step 2: Run** — Expected: `No module named 'sheet_sync'`.
- [ ] **Step 3: Implement the pull half of `scripts/sheet_sync.py`**, with `pull(download=download_tab, root=ROOT) -> int` so the test can inject both.
- [ ] **Step 4: Run the tests** — Expected: PASS.
- [ ] **Step 5: Try it for real** — `python3 scripts/sheet_sync.py pull`. The Sheet is still empty, so expected output is `Tasks tab is empty`, exit code 1, nothing written.
- [ ] **Step 6: Commit** — `git commit -m "feat: pull volunteer work from the Google Sheet into staging"`.

---

### Task 8: Sheet sync — push

**Files:**
- Modify: `scripts/sheet_sync.py`, `scripts/sources.py` (nothing new; consumed)
- Create: `tests/py/test_sheet_push.py`, `docs/ingestion/volunteer-how-to.md`

**Interfaces:**
- Consumes: Task 7's column lists; `sources.load`; `staging.load`.
- Produces:
  - `task_for(source: dict, has_draft: bool) -> tuple[str, int] | None` — the task kind and minutes a source row needs:
    - blank `listing_url`, or `notes` containing `fetch failed` → `("find_page", 2)`
    - `status_2027 == "changed"` → `("check_changes", 10)`
    - `status_2027 == "extracted"` and `has_draft` → `("check_draft", 10)`
    - `status_2027 == "posted"` and (`kind` in `rec1`, `gated`, `manual` or `robots_ai_optout == "TRUE"`) → `("enter_new", 45)`
    - otherwise `None`
  - `task_id(org_id: str, kind: str, season: int) -> str` — `<season>-<org_id>-<kind>`; a second `check_changes` for the same organization gets `-2`, `-3`….
  - `plan_push(sources, orgs: dict[str, dict], staged: dict[str, list[dict]], snapshot_tasks: list[dict], season: int, today: str) -> PushPlan`, where `orgs` maps `org_id` to its `organizations.csv` row, with fields `new_tasks`, `new_drafts`, `new_orgs` (lists of dicts in the tab column order) and `cell_changes` (list of `(task_id, column, new_value, reason)`):
    - a new task for every source needing one whose `task_id` is not already in the snapshot;
    - draft rows for new `check_draft` and `check_changes` tasks, from the staging file (for `check_changes`, the organization's merged rows from `programs.csv`, passed in through `staged`);
    - an Orgs row for every new task except `find_page`, pre-filled from its `orgs` row;
    - `cell_changes`: a task `claimed` whose `claimed_on` is more than 3 days before `today` → `status` back to `open` and `claimed_by` cleared (a blank or unreadable `claimed_on` is left alone); a task listed in `_needs_help.csv` → `status = needs_help` with the reason in `volunteer_notes`.
  - CLI: `python3 scripts/sheet_sync.py push` writes `data/staging/<season>/sheet/_to_paste_tasks.csv`, `_to_paste_drafts.csv`, `_to_paste_orgs.csv` (rows only, no header, in tab column order) and `_changes.txt` (one line per cell change), then prints how many rows to paste into which tab. `python3 scripts/sheet_sync.py init` writes the three header rows and the "How to" text to `_init/` for the first paste.

- [ ] **Step 1: Write the failing tests**

```python
import sheet_sync as ss
SRC = lambda **kw: {"org_id": "o1", "listing_url": "https://x.org", "kind": "html", "robots_ai_optout": "FALSE",
                    "expected_open": "2027-02-04", "status_2027": "not_posted", "notes": "", **kw}
class TaskFor(unittest.TestCase):
    def test_rules(self):
        self.assertIsNone(ss.task_for(SRC(), False))
        self.assertEqual(ss.task_for(SRC(listing_url=""), False), ("find_page", 2))
        self.assertEqual(ss.task_for(SRC(notes="fetch failed 2026-12-15: HTTP 404"), False), ("find_page", 2))
        self.assertEqual(ss.task_for(SRC(status_2027="extracted"), True), ("check_draft", 10))
        self.assertIsNone(ss.task_for(SRC(status_2027="extracted"), False))
        self.assertEqual(ss.task_for(SRC(status_2027="posted", kind="rec1"), False), ("enter_new", 45))
        self.assertEqual(ss.task_for(SRC(status_2027="posted", robots_ai_optout="TRUE"), False), ("enter_new", 45))
        self.assertIsNone(ss.task_for(SRC(status_2027="posted"), False))      # waits for extraction
        self.assertEqual(ss.task_for(SRC(status_2027="changed"), False), ("check_changes", 10))
class Plan(unittest.TestCase):
    def test_new_tasks_drafts_and_no_duplicates(self):
        sources = [SRC(status_2027="extracted"), SRC(org_id="o2", listing_url="")]
        staged = {"o1": [{"program_id": "o1-camp-2027", "org_id": "o1", "program_name": "Camp", "source_url": "https://x.org", "source_quote": "July 5–9"}]}
        plan = ss.plan_push(sources, {"o1": {"org_name": "Org One"}, "o2": {"org_name": "Org Two"}}, staged, [], 2027, "2026-12-20")
        self.assertEqual([(t["task_id"], t["kind"], t["minutes"], t["status"], t["due"]) for t in plan.new_tasks],
                         [("2027-o1-check_draft", "check_draft", "10", "open", "2027-02-04"), ("2027-o2-find_page", "find_page", "2", "open", "")])
        self.assertEqual([(d["task_id"], d["row_id"], d["review"]) for d in plan.new_drafts], [("2027-o1-check_draft", "o1-camp-2027", "")])
        self.assertEqual([o["org_id"] for o in plan.new_orgs], ["o1"])
        again = ss.plan_push(sources, {"o1": {"org_name": "Org One"}, "o2": {"org_name": "Org Two"}}, staged, plan.new_tasks, 2027, "2026-12-20")
        self.assertEqual((again.new_tasks, again.new_drafts), ([], []))
    def test_stale_claims_are_released(self):
        snap = [{"task_id": "t1", "status": "claimed", "claimed_by": "Sam", "claimed_on": "2026-12-10"},
                {"task_id": "t2", "status": "claimed", "claimed_by": "Ana", "claimed_on": "2026-12-18"},
                {"task_id": "t3", "status": "claimed", "claimed_by": "Lee", "claimed_on": ""}]
        plan = ss.plan_push([], {}, {}, snap, 2027, "2026-12-20")
        self.assertEqual(sorted((c[0], c[1], c[2]) for c in plan.cell_changes),
                         [("t1", "claimed_by", ""), ("t1", "status", "open")])
```

- [ ] **Step 2: Run** — Expected: `task_for` not defined.
- [ ] **Step 3: Implement the push half.**
- [ ] **Step 4: Write `docs/ingestion/volunteer-how-to.md`** — the text for the Sheet's "How to" tab: what the site is for; how to claim a task (type your name, set status to `claimed`); what each of the four task kinds asks, with one worked `check_draft` example showing a source sentence beside a row; the three `review` values; "leave a cell blank when the page does not say"; who to ask. One screen, plain language, no jargon.
- [ ] **Step 5: Run the tests, then** `python3 scripts/sheet_sync.py init && python3 scripts/sheet_sync.py push` — Expected: PASS; the `_init/` files exist; `push` reports one `find_page` task for each source with a blank URL.
- [ ] **Step 6: Commit** — `git commit -m "feat: generate task, draft and org rows to paste into the volunteer Sheet"`.

---

### Task 9: The extraction skill

**REQUIRED SUB-SKILL for this task:** superpowers:writing-skills.

**Files:**
- Create: `.claude/skills/extract-camp-listings/SKILL.md`, `tests/fixtures/pages/sample_camp.html`, `tests/fixtures/pages/sample_camp.expected.json`
- Modify: `.gitignore` (replace the line `.claude/` with `.claude/*`, `!.claude/settings.json`, `!.claude/skills/`)

**Interfaces:**
- Consumes: `python3 scripts/staging.py add --org ORG_ID --json FILE`, `python3 scripts/staging.py check`, `sources.csv`.

The skill must tell Claude, in this order:

1. Pick sources with `status_2027 = posted`, `kind` `html` or `pdf`, and `robots_ai_optout = FALSE`. Never fetch a `TRUE` or blank one.
2. Fetch the `listing_url` once. Follow at most three links on the same host that clearly lead to 2027 session details. Pause two seconds between requests.
3. Produce one row per session a family could register for, using the `programs.csv` column names. Dates as `YYYY-MM-DD`, times as `9:00 AM`, `cost_raw` copied as written, `activities` only from the canonical list in `camp-finder-data-architecture.md`.
4. Leave a field blank when the page does not state it. Never carry a value over from the organization's 2026 rows, and never infer a 2027 date from a 2026 one.
5. Write `description` fresh, one or two neutral sentences, not copied.
6. Give every row `source_url` (the page it came from) and `source_quote` (the sentence or table line containing its dates, at most 200 characters, copied exactly).
7. Put organization facts (the three registration fields, financial aid) in `data/staging/<season>/_organizations.csv`.
8. Save the rows as JSON and run `python3 scripts/staging.py add --org ORG_ID --json FILE`. If it reports errors, fix the rows, not the checks.
9. Set the source's `status_2027` to `extracted`. If the page turns out to have no 2027 sessions after all, set it back to `not_posted` with a note.
10. Report per organization: rows drafted, fields mostly blank, anything a volunteer should look at first.

- [ ] **Step 1: Build the fixture.** `sample_camp.html` is an invented camp page (no real organization) with: three 2027 weekly sessions in a table, one with a different price; a line "Registration opens February 3, 2027; members January 27"; an ages line; a 2026 testimonial mentioning last year's dates; no stated end time. `sample_camp.expected.json` lists what a correct extraction must contain: three rows with the exact dates and `cost_raw` values, `end_time` blank on all three, no row dated 2026, `registration_opens` `2027-02-03` and `registration_opens_early` `2027-01-27`.
- [ ] **Step 2: Baseline (RED), per writing-skills.** Give a fresh subagent only the fixture page and "extract this camp's 2027 sessions into programs.csv rows", without the skill. Record what it gets wrong against the expected file (typically: an invented end time, a copied description, no source quote).
- [ ] **Step 3: Write `SKILL.md`** with the ten points above, a frontmatter `description` starting "Use when an organization in data/sources.csv has status_2027 = posted…", and the baseline's actual mistakes named as things not to do.
- [ ] **Step 4: Verify (GREEN).** Give a fresh subagent the skill and the fixture, with a temp copy of the repo so `staging.py add` can run. Expected: the staging file passes `python3 scripts/staging.py check` and matches every assertion in the expected file.
- [ ] **Step 5: Update `.gitignore`** and confirm `git status` shows the skill as untracked-but-addable and nothing else under `.claude/`.
- [ ] **Step 6: Commit** — `git add .gitignore .claude/skills tests/fixtures/pages && git commit -m "feat: skill for drafting 2027 rows from a posted camp page"`.

---

### Task 10: Volunteer task board

**Files:**
- Create: `volunteer.html`, `volunteer.js`
- Modify: `lib.js`, `tests/js/lib.test.js`, `nav.js` (add `{ label: 'Volunteer', href: 'volunteer.html', key: 'volunteer' }` before Admin), `styles.css`, `scripts/smoke_test.py`

**Interfaces:**
- Consumes: Task 7's `TASK_COLUMNS`; `data/ingest_config.json`; `CampLib.escapeHtml`.
- Produces:
  - `CampLib.parseCsv(text: string) -> object[]` — RFC 4180: quoted fields, doubled quotes, commas and newlines inside quotes, CRLF or LF; first row is the header; fully blank rows dropped.
  - `CampLib.groupTasks(tasks: object[]) -> {quick: object[], medium: object[], long: object[], counts: {open, claimed, done, needs_help, total}}` — `quick` is `open` tasks with `minutes <= 5`, `medium` 6–15, `long` over 15; each sorted by `due` ascending with blank `due` last, then `org_name`.
- Page behaviour: `volunteer.js` fetches `data/ingest_config.json`, then the Tasks tab from `https://docs.google.com/spreadsheets/d/<sheet_id>/gviz/tq?tqx=out:csv&sheet=Tasks`. If that fails, or the URL has `?source=snapshot`, it fetches `data/staging/<season>/sheet/tasks.csv` and shows `Showing the last saved copy.` It renders a progress line (`<done> of <total> tasks done · <claimed> in progress`), three groups headed `2 minutes`, `10 minutes`, `30–60 minutes`, each task as the organization's name, the due date when present (`Registration opens Feb 4`), and a button `Open in the Sheet` linking to `https://docs.google.com/spreadsheets/d/<sheet_id>/edit#gid=<tasks_gid>&range=A<row>` where `<row>` is the task's position in the tab plus 2. When there are no tasks at all it shows `No tasks yet. New ones appear here as camps post their 2027 listings.` Volunteer names are never rendered.

- [ ] **Step 1: Write the failing tests**

```js
test('parseCsv handles quotes, commas, newlines and CRLF', () => {
  const rows = parseCsv('a,b\r\n1,"x, y"\r\n2,"line1\nline2"\r\n3,"say ""hi"""\r\n,\r\n');
  assert.deepEqual(rows, [{ a: '1', b: 'x, y' }, { a: '2', b: 'line1\nline2' }, { a: '3', b: 'say "hi"' }]);
  assert.deepEqual(parseCsv(''), []);
});
test('groupTasks buckets open tasks by time and sorts by due date', () => {
  const t = (task_id, minutes, status, due = '', org_name = task_id) => ({ task_id, minutes, status, due, org_name });
  const g = groupTasks([t('a', '2', 'open'), t('b', '10', 'open', '2027-02-04'), t('c', '10', 'open', '2027-01-12'),
    t('d', '10', 'open'), t('e', '45', 'open'), t('f', '10', 'claimed'), t('g', '10', 'done'), t('h', '2', 'needs_help')]);
  assert.deepEqual(g.quick.map(x => x.task_id), ['a']);
  assert.deepEqual(g.medium.map(x => x.task_id), ['c', 'b', 'd']);
  assert.deepEqual(g.long.map(x => x.task_id), ['e']);
  assert.deepEqual(g.counts, { open: 5, claimed: 1, done: 1, needs_help: 1, total: 8 });
});
```

Smoke: extend the staging step of `smoke_test.py` to also copy `data/ingest_config.json` into the temp site and to write `data/staging/2027/sheet/tasks.csv` there with four fixture tasks (one of each time group open, with `claimed_by` set to `Zed Volunteer` on a fourth, claimed, task; one `org_name` of `<i>Evil</i> Org`). Checks, loading `volunteer.html?source=snapshot`:

```python
check("board: groups", all(h in dom for h in ("2 minutes", "10 minutes", "30–60 minutes")))
check("board: progress", "0 of 4 tasks done · 1 in progress" in dom)
check("board: snapshot note", "Showing the last saved copy." in dom)
check("board: sheet link", "edit#gid=0&amp;range=A2" in dom or "edit#gid=0&range=A2" in dom)
check("board: names not shown", "Zed Volunteer" not in dom)
check("board: escaped", "&lt;i&gt;Evil&lt;/i&gt; Org" in dom)
check("nav: volunteer link", 'href="volunteer.html"' in dump_dom("index.html"))
```

- [ ] **Step 2: Run** both suites — Expected: Node `parseCsv is not a function`; smoke `board:` checks FAIL.
- [ ] **Step 3: Implement the two helpers, the page and the nav item.** `volunteer.html` follows `about.html`'s structure and loads `lib.js`, `volunteer.js`, `nav.js` with `window.NAV_PAGE = 'volunteer'` and the favicon link.
- [ ] **Step 4: Run the full test command** — Expected: all pass.
- [ ] **Step 5: Commit** — `git commit -m "feat: volunteer task board that reads the Sheet"`.

---

### Task 11: Runbook, first run and the export request

**Files:**
- Create: `docs/ingestion/runbook.md`, `docs/ingestion/rec1-export-request.md`
- Modify: `README.md` (a short "2027 ingestion" section linking to the runbook), `scripts/README.md` (the new scripts)

- [ ] **Step 1: Write `docs/ingestion/runbook.md`**, the weekly routine in order, each step a command and what to expect:
  1. `python3 scripts/sheet_sync.py pull` — read what volunteers did.
  2. Look over each ready staging file, then `python3 scripts/merge_staging.py --all-ready`.
  3. `python3 scripts/watch_sources.py` — see what has posted or changed.
  4. Run the `extract-camp-listings` skill in Claude Code for the newly posted sources.
  5. `python3 scripts/sheet_sync.py push`, then paste the three files into the Sheet and apply `_changes.txt`.
  6. Run the full test command; commit `data/`, `data.js`; open a pull request.
  Plus: how to set up the Sheet the first time (`init`, four tabs, paste the headers and the how-to text, find the Tasks tab's `gid` in its URL and put it in `data/ingest_config.json`); how to restore a vandalised Sheet from the last snapshot; what to do when the reminder test starts failing; the two deferred pieces (direct Sheet writes, the Rec1 browser route) and when each gets decided.

- [ ] **Step 2: Write `docs/ingestion/rec1-export-request.md`** — a short letter to a municipal recreation department asking for a spreadsheet export of summer 2027 camp sessions (name, dates, times, ages or grades, price, location, registration link), saying what the site is, that it is free and volunteer-run, that each listing links back to their registration page, and what the alternative is (volunteers retyping it). Placeholders for the sender's name and the department; one version, addressed generically, with the three departments (Burlington Parks, Recreation & Waterfront; South Burlington Recreation & Parks; Colchester Parks & Recreation) listed beneath.

- [ ] **Step 3: First run.** Follow the runbook's first-time setup far enough to produce the `_init/` files and the first `push` output. Do not paste into the Sheet; report the row counts and leave the pasting to the maintainer.

- [ ] **Step 4: Run the full test command** — Expected: all pass, including the reminder test (it fails from 2026-12-20).

- [ ] **Step 5: Commit** — `git commit -m "docs: ingestion runbook and export request"`.
