# Site Bug Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the eight defects found on the Camps, Afterschool and Admin pages, and add the project's first automated tests so they stay fixed.

**Architecture:** Pure logic moves out of `app.js` and `admin.html` into a new `lib.js`, which attaches `CampLib` to `window` in the browser and exports the same object under Node. Build-time logic stays in `scripts/` and is tested with `unittest`. A smoke script serves a copy of the site with fixture data and reads the rendered DOM from headless Chrome.

**Tech Stack:** Vanilla JS (no modules, no bundler), Python 3 standard library, Node built-in test runner (`node --test`), headless Chrome for the smoke test.

**Spec:** `docs/superpowers/specs/2026-11-20-site-bug-fixes-design.md`

## Global Constraints

- No new dependencies: no `package.json`, no `npm install`, no `pip install`.
- The site is served by GitHub Pages from the `main` branch root; scripts load with plain `<script src>` tags.
- `lib.js` loads before `app.js` on `index.html` and `afterschool.html`, and before the inline script on `admin.html`.
- `data.js` is only ever changed by running `python3 scripts/build_data_js.py`; commit the regenerated file in the same commit as the script change.
- No CSV schema changes.
- Work on branch `fix/site-bugs`, one commit per task.
- Notice copy (Task 4), exactly: `Listings marked {year} are last season's, shown for reference until that camp posts new dates.`
- Banner copy (Task 7), exactly: `You are viewing edits saved in this browser on {date}, not the published data.` and, when stale, ` The published data has changed since then.`

## Review Focus

1. A program with a `startDate` but no `endDate` on the season boundary: it counts as past once its start date has passed, and is hidden, not shown as last season, while a sibling program from the same organization is still upcoming. (Task 4)
2. A town name that is not in the GeoJSON, such as `St. Albans` or a typo: the map still renders, and the program is counted in the "not shown on the map" note. (Task 5)
3. Corrupt or non-JSON content in the admin's localStorage keys: the admin loads the published data instead of throwing. (Task 7)
4. A `website` value that is not `http:` or `https:` (for example `javascript:alert(1)`): no "Visit Website" link is rendered. (Task 2)
5. A `uid` containing characters that are special in a CSS selector, such as the admin-created `prog-12` or a future id with a dot: toggling "Save for export" still finds the card. (Task 6)

## File Structure

| File | Responsibility |
|---|---|
| `lib.js` (new) | Pure helpers: `escapeHtml`, `safeUrl`, `formatAddress`, `isPast`, `defaultVisibility`, `lastSeasonYear`, `localEditsStatus`. No DOM access. |
| `app.js` | Rendering and events for the two public pages. Calls `CampLib`. |
| `admin.html` | Admin editor. Calls `CampLib.localEditsStatus` and `CampLib.escapeHtml`. |
| `scripts/vt_towns.py` (new) | Town names, aliases, county lookup and centroids from the GeoJSON. |
| `scripts/build_data_js.py` | Emits `PROGRAMS`, `ORGANIZATIONS`, plus new `TOWN_COORDS` and `DATA_GENERATED`. |
| `scripts/infer_counties.py` | Imports aliases and the county map from `vt_towns.py` instead of defining them. |
| `scripts/smoke_test.py` (new) | Serves a temp copy of the site with fixture data; asserts on the DOM from headless Chrome. |
| `tests/js/lib.test.js` (new) | Node tests for `lib.js`. |
| `tests/py/test_vt_towns.py`, `tests/py/test_build_data_js.py` (new) | `unittest` tests for the build scripts. |
| `tests/fixtures/data.fixture.js` (new) | Small deterministic `data.js` for the smoke test. |
| `favicon.svg` (new) | Site icon. |

Run all tests with:

```bash
node --test tests/js/ && python3 -m unittest discover -s tests/py -v && python3 scripts/smoke_test.py
```

---

### Task 1: Smoke test harness and the Afterschool page crash (bug 1)

**Files:**
- Create: `scripts/smoke_test.py`, `tests/fixtures/data.fixture.js`
- Modify: `app.js:1023` (in `clearAllFilters`), `app.js:1054-1057` (date listener), `README.md` (add a "Tests" section with the three commands above)

**Interfaces:**
- Produces: `scripts/smoke_test.py` with `check(name: str, condition: bool) -> None` (records a failure and keeps going) and `dump_dom(path: str) -> str` (rendered HTML of `http://127.0.0.1:<port>/<path>`). Later tasks add checks to its `main()`. Exit code 0 when every check passes, 1 otherwise, 2 when Chrome is not found.
- Produces: `tests/fixtures/data.fixture.js` defining `PROGRAMS` and `ORGANIZATIONS` with these programs, using the same field names `build_program_obj` emits:

| uid | category | name | city | startDate / endDate | other |
|---|---|---|---|---|---|
| `camp-future` | camp | `Future Camp` | Burlington | 2099-07-06 / 2099-07-10 | `website: "https://example.org/camp"` |
| `camp-past` | camp | `Past Camp` | Montpelier | 2001-07-02 / 2001-07-06 | |
| `camp-undated` | camp | `Undated Camp` | `""` | blank | |
| `camp-evil` | camp | `<img src=x onerror="document.title='XSS'">` | `Nowhereville` | blank | `website: "javascript:alert(1)"`, `description: "<b>bold</b>"` |
| `after-1`, `after-2` | afterschool | `After One`, `After Two` | Winooski | blank | |

- [ ] **Step 1: Write the smoke script and fixture**

`smoke_test.py` copies `*.html`, `*.js`, `*.css` and `favicon.svg` (if present) into a temp directory, overwrites `data.js` there with the fixture, serves it with `http.server` on an ephemeral port in a thread, and runs Chrome with `--headless=new --disable-gpu --virtual-time-budget=5000 --dump-dom` and a temp `--user-data-dir`. Chrome is located from the `CHROME` environment variable, then `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`, then `google-chrome`, `chromium`, `chromium-browser` on `PATH`.

First checks:

```python
dom = dump_dom("afterschool.html")
check("afterschool: count line", "of <strong>2</strong> afterschool programs" in dom)
check("afterschool: two cards", dom.count('class="program-card') == 2)
dom = dump_dom("index.html")
check("camps: count line", "of <strong>4</strong> camps" in dom)
```

- [ ] **Step 2: Run it and confirm the afterschool checks fail**

Run: `python3 scripts/smoke_test.py`
Expected: `FAIL afterschool: count line`, `FAIL afterschool: two cards`, `PASS camps: count line`, exit code 1.

- [ ] **Step 3: Make `filterDate` optional in `app.js`**

Wrap the `filterDate.addEventListener('change', ...)` block and the `filterDate.value = ''` line in `clearAllFilters` in `if (filterDate) { ... }`. `clearAllFilters` must rebuild `activeFilters` with exactly the keys of the initial declaration at `app.js:30-44` (it currently omits `data` and `showPast`).

- [ ] **Step 4: Run the smoke test**

Run: `python3 scripts/smoke_test.py`
Expected: all three checks `PASS`, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_test.py tests/fixtures/data.fixture.js app.js README.md
git commit -m "fix: afterschool page crash when date filter is absent; add smoke test"
```

---

### Task 2: Escape CSV-derived text (bug 8)

**Files:**
- Create: `lib.js`, `tests/js/lib.test.js`
- Modify: `index.html`, `afterschool.html`, `admin.html` (add `<script src="lib.js"></script>` immediately before `data.js`), `app.js` (`renderCards`, `renderCalendar`, `renderMap`, `openModal`), `scripts/smoke_test.py`

**Interfaces:**
- Produces: `lib.js`, wrapped so that it sets `window.CampLib` in a browser and `module.exports` under Node, with:
  - `escapeHtml(value: any) -> string` — `null`/`undefined` become `""`; escapes `& < > " '`.
  - `safeUrl(value: any) -> string` — returns the trimmed value when it starts with `http://` or `https://` (case-insensitive), otherwise `""`.

- [ ] **Step 1: Write the failing tests**

```js
const test = require('node:test');
const assert = require('node:assert/strict');
const { escapeHtml, safeUrl } = require('../../lib.js');

test('escapeHtml escapes markup characters', () => {
  assert.equal(escapeHtml(`<img src=x onerror="a('b')">&`),
    '&lt;img src=x onerror=&quot;a(&#39;b&#39;)&quot;&gt;&amp;');
});
test('escapeHtml handles null, undefined and numbers', () => {
  assert.equal(escapeHtml(null), '');
  assert.equal(escapeHtml(undefined), '');
  assert.equal(escapeHtml(42), '42');
});
test('safeUrl allows only http and https', () => {
  assert.equal(safeUrl(' https://example.org/a '), 'https://example.org/a');
  assert.equal(safeUrl('HTTP://example.org'), 'HTTP://example.org');
  assert.equal(safeUrl('javascript:alert(1)'), '');
  assert.equal(safeUrl('data:text/html,x'), '');
  assert.equal(safeUrl(''), '');
  assert.equal(safeUrl(null), '');
});
```

Add to `smoke_test.py`, against `index.html` (the page loads with the default filters, so `camp-evil` is visible because it is undated):

```python
check("xss: no injected img element", '<img src="x"' not in dom)
check("xss: title untouched", "<title>Summer Camps" in dom)
check("xss: name shown as text", "&lt;img src=x" in dom)
check("xss: description shown as text", "&lt;b&gt;bold&lt;/b&gt;" in dom)
check("unsafe url: no javascript link", 'href="javascript:' not in dom)
check("safe url: link kept", 'href="https://example.org/camp"' in dom)
```

- [ ] **Step 2: Run both and confirm failure**

Run: `node --test tests/js/` — Expected: FAIL, `Cannot find module '../../lib.js'`.
Run: `python3 scripts/smoke_test.py` — Expected: the four `xss:` checks and `unsafe url` FAIL.

- [ ] **Step 3: Implement `lib.js` and load it on the three pages**

- [ ] **Step 4: Apply the helpers in `app.js`**

Every `${...}` inside an `innerHTML` template or HTML string that carries a program or organization field goes through `CampLib.escapeHtml`: in `renderCards` (`p.type`, `p.name`, `p.organization`, `p.description`, `locationText`, `p.county`, `gradesText`, `p.hours`, `p.starsLevel`, each subject tag, `p.uid` in attributes), `renderCalendar` (`label`, the `title` attribute, `p.uid`), `renderMap` (`city`, `p.name`, `p.uid`) and `openModal` (subject tags, `orgLabel`, sibling `q.name` and `q.uid`). Website links use `const site = CampLib.safeUrl(p.website)` and render only when `site` is non-empty; the modal's `modalWebsiteLink` uses the same value.

- [ ] **Step 5: Run all tests**

Run: `node --test tests/js/ && python3 scripts/smoke_test.py`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add lib.js tests/js/lib.test.js app.js index.html afterschool.html admin.html scripts/smoke_test.py
git commit -m "fix: escape program text and restrict links to http(s)"
```

---

### Task 3: Clean "Full Address" in the CSV export (bug 4)

**Files:**
- Modify: `lib.js`, `tests/js/lib.test.js`, `app.js:505` (in `downloadExportCsv`)

**Interfaces:**
- Produces: `CampLib.formatAddress(p: {address, city, state, zip}) -> string` — `street, city, ST zip`, skipping blank parts.

- [ ] **Step 1: Write the failing tests**

```js
test('formatAddress joins the parts that exist', () => {
  assert.equal(formatAddress({ address: '1 Main St', city: 'Burlington', state: 'VT', zip: '05401' }),
    '1 Main St, Burlington, VT 05401');
  assert.equal(formatAddress({ address: '', city: 'Hinesburg', state: 'VT', zip: '' }), 'Hinesburg, VT');
  assert.equal(formatAddress({ address: '  ', city: '', state: '', zip: '' }), '');
  assert.equal(formatAddress({}), '');
});
```

- [ ] **Step 2: Run** `node --test tests/js/` — Expected: FAIL, `formatAddress is not a function`.

- [ ] **Step 3: Implement `formatAddress` and use it in `downloadExportCsv`** in place of the template string that builds `address`.

- [ ] **Step 4: Run** `node --test tests/js/` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib.js tests/js/lib.test.js app.js
git commit -m "fix: skip blank parts in exported Full Address"
```

---

### Task 4: Keep last season's programs visible until that organization posts new ones (bug 2)

*Revised 2026-11-20: the rule is per organization, not per page. A per-page rule would hide every other organization's 2026 camps as soon as one organization's 2027 camps were added.*

**Files:**
- Modify: `lib.js`, `tests/js/lib.test.js`, `app.js` (`isPast`, `applyFilters`, `init`, `activeFilters`/`clearAllFilters`), `index.html` and `afterschool.html` (add `<p id="seasonNotice" class="season-notice hidden"></p>` directly above the results-count row), `styles.css`, `tests/fixtures/data.fixture.js`, `scripts/smoke_test.py`

**Interfaces:**
- Produces:
  - `CampLib.isPast(p: {startDate, endDate}, todayIso: string) -> boolean` — same rule as today's `isPast` in `app.js`, with the date passed in.
  - `CampLib.defaultVisibility(programs: object[], todayIso: string) -> Map<string, 'current' | 'lastSeason' | 'hidden'>`, keyed by `uid`. Programs are grouped by `orgId`; a program with a blank `orgId` is a group of its own. A program's year is `Number(programYear)`, falling back to the year of `endDate || startDate`, then `0`. For each program, in order:
    1. its group has a program with a later year → `'hidden'` (superseded);
    2. it is not past → `'current'`;
    3. its group has a dated program that is not past → `'hidden'` (mid-season, as today);
    4. otherwise → `'lastSeason'`.
  - `CampLib.lastSeasonYear(programs: object[], visibility: Map) -> string | null` — the greatest year among `'lastSeason'` programs, or `null` when there are none.

- [ ] **Step 1: Write the failing tests**

```js
const T = '2026-11-20';
const P = (uid, orgId, programYear, startDate = '', endDate = '') => ({ uid, orgId, programYear, startDate, endDate });

test('isPast uses end date, then start date', () => {
  assert.equal(isPast(P('x', 'o', '2026', '2026-07-06', '2026-07-10'), T), true);
  assert.equal(isPast(P('x', 'o', '2027', '2027-07-05', '2027-07-09'), T), false);
  assert.equal(isPast(P('x', 'o', '2026'), T), false);
  assert.equal(isPast(P('x', 'o', '2026', '2026-11-20'), T), false);
  assert.equal(isPast(P('x', 'o', '2026', '2026-11-19'), T), true);
  assert.equal(isPast(P('x', 'o', '2026', '2026-11-01', '2026-11-20'), T), false);
});

test('defaultVisibility decides per organization', () => {
  const programs = [
    P('a1', 'A', '2026', '2026-07-06', '2026-07-10'),   // only last season exists
    P('b1', 'B', '2026', '2026-07-06', '2026-07-10'),   // superseded by b2
    P('b2', 'B', '2027', '2027-07-05', '2027-07-09'),
    P('c1', 'C', '2026'),                               // undated, superseded by c2
    P('c2', 'C', '2027'),
    P('d1', 'D', '2026', '2026-11-01', '2026-11-05'),   // mid-season: sibling still upcoming
    P('d2', 'D', '2026', '2026-12-01', '2026-12-05'),
    P('e1', 'E', '2026'),                               // undated, nothing newer
    P('n1', '', '2026', '2026-07-06', '2026-07-10'),    // no org: judged alone
    P('n2', '', '2027', '2027-07-05', '2027-07-09'),
  ];
  const v = defaultVisibility(programs, T);
  assert.deepEqual(Object.fromEntries(v), {
    a1: 'lastSeason', b1: 'hidden', b2: 'current', c1: 'hidden', c2: 'current',
    d1: 'hidden', d2: 'current', e1: 'current', n1: 'lastSeason', n2: 'current',
  });
  assert.equal(lastSeasonYear(programs, v), '2026');
});

test('defaultVisibility: start date only, on the boundary', () => {
  const programs = [P('s1', 'S', '2026', '2026-11-19'), P('s2', 'S', '2026', '2026-11-20')];
  assert.deepEqual(Object.fromEntries(defaultVisibility(programs, T)), { s1: 'hidden', s2: 'current' });
});

test('defaultVisibility: year falls back to the dates, then to 0', () => {
  const programs = [P('f1', 'F', '', '2026-07-06', '2026-07-10'), P('f2', 'F', '', '2027-07-05', '2027-07-09'), P('f3', 'F', '')];
  assert.deepEqual(Object.fromEntries(defaultVisibility(programs, T)), { f1: 'hidden', f2: 'current', f3: 'hidden' });
});

test('lastSeasonYear is null when nothing is last season', () => {
  const programs = [P('b2', 'B', '2027', '2027-07-05', '2027-07-09')];
  assert.equal(lastSeasonYear(programs, defaultVisibility(programs, T)), null);
  assert.equal(lastSeasonYear([], new Map()), null);
});
```

Add one program to the fixture: `camp-old`, category `camp`, name `Old Camp`, city `Burlington`, the same `orgId` as `camp-future`, `programYear` `"2001"`, dates 2001-07-09 / 2001-07-13. Give `camp-future` `programYear` `"2099"` and `camp-past` `programYear` `"2001"`, and make sure `camp-past` and `camp-future` have different `orgId` values. Update Task 1's check to `"of <strong>5</strong> camps"`.

Add to `smoke_test.py`:

```python
dom = dump_dom("index.html")
check("season: superseded row hidden, last-season row shown", "Showing <strong>4</strong> of <strong>5</strong> camps" in dom)
check("season: notice text", "Listings marked 2001 are last season's, shown for reference until that camp posts new dates." in dom)
check("season: notice visible", 'id="seasonNotice" class="season-notice"' in dom)
dom = dump_dom("index.html", drop_uids=["camp-past"])
check("season: no last-season rows, notice hidden", 'id="seasonNotice" class="season-notice hidden"' in dom)
dom = dump_dom("afterschool.html")
check("season: undated category, notice hidden", 'id="seasonNotice" class="season-notice hidden"' in dom)
```

`dump_dom` gains an optional `drop_uids: list[str]` that rewrites the temp `data.js` with those programs filtered out before loading.

- [ ] **Step 2: Run both** — Expected: Node FAIL (`isPast is not a function`); smoke `season:` checks FAIL.

- [ ] **Step 3: Implement the three helpers in `lib.js`**

- [ ] **Step 4: Wire into `app.js`**

`isPast(p)` in `app.js` delegates to `CampLib.isPast(p, TODAY)`. In `init()`, after `allPrograms` is set, compute `visibility = CampLib.defaultVisibility(categoryPrograms(), TODAY)` into a module-level variable. In `applyFilters`, the first test becomes: when `activeFilters.showPast` is false, drop the program if `visibility.get(p.uid) === 'hidden'`. "Include past programs" stays unchecked by default and still shows everything when ticked. When `CampLib.lastSeasonYear(...)` is not null, unhide `#seasonNotice` with the text `Listings marked {year} are last season's, shown for reference until that camp posts new dates.` Last-season cards already sort last and carry the year chip through the existing `isPast` handling in `renderCards`; leave that as it is.

Also rename the mistyped key `data` to `date` in the `activeFilters` declaration and in `clearAllFilters`.

- [ ] **Step 5: Style `.season-notice`** in `styles.css` to match the results-count text scale, with a pale yellow background.

- [ ] **Step 6: Run all tests** — Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add lib.js tests/js/lib.test.js app.js index.html afterschool.html styles.css tests/fixtures/data.fixture.js scripts/smoke_test.py
git commit -m "fix: keep last season's programs visible until that organization posts new ones"
```

---

### Task 5: Plot every Vermont town on the map (bug 3)

**Files:**
- Create: `scripts/vt_towns.py`, `tests/py/test_vt_towns.py`
- Modify: `scripts/infer_counties.py:27-49`, `scripts/build_data_js.py` (`main`), `app.js:740-762` and `renderMap`, `index.html` and `afterschool.html` (add `<p id="mapUnplacedNote" class="cal-undated-note" style="display:none"></p>` after `#mapContainer`), `tests/fixtures/data.fixture.js`, `scripts/smoke_test.py`, `data.js` (regenerated)

**Interfaces:**
- Produces, in `scripts/vt_towns.py`:
  - `CITY_ALIASES: dict[str, str]` — moved verbatim from `infer_counties.py`.
  - `load_town_county_map() -> dict[str, str]` — moved verbatim, including alias entries.
  - `load_town_centroids() -> dict[str, tuple[float, float]]` — `{town: (lat, lng)}` for every `TOWNNAMEMC` plus every alias, rounded to 4 decimals. The centroid is the area-weighted centroid of the outer ring; for a `MultiPolygon`, of its largest outer ring by vertex count.
- Produces, in `data.js`: `const TOWN_COORDS = { "Burlington": [44.4874, -73.2312], ... };` containing only towns that some program or organization in the CSVs uses.
- Consumes: `CampLib.escapeHtml` (Task 2).

- [ ] **Step 1: Write the failing Python tests**

```python
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import vt_towns

class TownCentroids(unittest.TestCase):
    def setUp(self):
        self.c = vt_towns.load_town_centroids()

    def test_known_towns(self):
        for town, lat, lng in [("Burlington", 44.4874, -73.2312),
                               ("Montpelier", 44.2659, -72.5717),
                               ("Hinesburg", 44.3244, -73.0902)]:
            self.assertAlmostEqual(self.c[town][0], lat, places=3)
            self.assertAlmostEqual(self.c[town][1], lng, places=3)

    def test_aliases_resolve(self):
        self.assertEqual(self.c["St. Albans"], self.c["Saint Albans City"])

    def test_all_towns_inside_vermont(self):
        self.assertGreaterEqual(len(self.c), 256)
        for lat, lng in self.c.values():
            self.assertTrue(42.7 < lat < 45.1 and -73.5 < lng < -71.4)

    def test_county_map_still_works(self):
        self.assertEqual(vt_towns.load_town_county_map()["Burlington"], "Chittenden")
```

- [ ] **Step 2: Run** `python3 -m unittest discover -s tests/py -v` — Expected: `ModuleNotFoundError: No module named 'vt_towns'`.

- [ ] **Step 3: Create `scripts/vt_towns.py`; make `infer_counties.py` import `CITY_ALIASES` and `load_town_county_map` from it**

- [ ] **Step 4: Run** `python3 -m unittest discover -s tests/py -v` — Expected: PASS. Run `python3 scripts/infer_counties.py` and confirm `git diff --stat data/` shows no change.

- [ ] **Step 5: Emit `TOWN_COORDS` from `build_data_js.py`**

In `main()`, collect the distinct non-blank `city` values of `program_objs` and `org_objs`, look each up in `load_town_centroids()`, and append `const TOWN_COORDS = {...};` to the output. Print `towns without coordinates: [...]` for any miss.

- [ ] **Step 6: Add the smoke checks, then switch `app.js` to `TOWN_COORDS`**

Add `const TOWN_COORDS = { "Burlington": [44.4874, -73.2312], "Montpelier": [44.2659, -72.5717], "Winooski": [44.4951, -73.1846] };` to the fixture. So the smoke test can open the map directly, `init()` calls `switchView('map')` when `location.hash === '#map'` (and likewise `'#calendar'`).

```python
dom = dump_dom("index.html#map")
check("map: note counts programs with no usable town", "2 results have no mapped town and are not shown on the map." in dom)
```

(Of the four camps shown by default, `camp-undated` has no city and `camp-evil` is in `Nowhereville`; `camp-future` in Burlington and `camp-past` in Montpelier are placed.)

In `app.js`, delete `CITY_COORDS` and read coordinates from `typeof TOWN_COORDS !== 'undefined' ? TOWN_COORDS : {}`. At the top of `renderMap`, before any use of Leaflet's `L` (the smoke test may run without network access to the Leaflet CDN), count programs whose city is blank or has no coordinates and write the note into `#mapUnplacedNote`: `1 result has no mapped town and is not shown on the map.` for one, `${n} results have no mapped town and are not shown on the map.` for more, hidden when `n` is 0. Return early from `renderMap` when `typeof L === 'undefined'`.

- [ ] **Step 7: Rebuild and run everything**

Run: `python3 scripts/build_data_js.py && node --test tests/js/ && python3 -m unittest discover -s tests/py && python3 scripts/smoke_test.py`
Expected: build prints `towns without coordinates: []`; all tests pass.

- [ ] **Step 8: Commit**

```bash
git add scripts/vt_towns.py scripts/infer_counties.py scripts/build_data_js.py tests/py/test_vt_towns.py tests/fixtures/data.fixture.js scripts/smoke_test.py app.js index.html afterschool.html data.js
git commit -m "fix: map every Vermont town using centroids from the town GeoJSON"
```

---

### Task 6: Stable program IDs (bug 7)

**Files:**
- Create: `tests/py/test_build_data_js.py`
- Modify: `scripts/build_data_js.py:297` (in `build_program_obj`), `app.js:413` (in `refreshExportUiForUid`), `data.js` (regenerated)

**Interfaces:**
- Produces: each `PROGRAMS` entry has `uid` equal to its CSV `program_id`. `id` stays a 1-based integer in row order.

- [ ] **Step 1: Write the failing test**

```python
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_data_js as b

ORG = {"org_id": "o1", "org_name": "Org One"}
def prog(pid, ptype="camp"):
    return {"program_id": pid, "org_id": "o1", "program_name": pid, "program_type": ptype}

class StableUid(unittest.TestCase):
    def test_uid_is_program_id(self):
        self.assertEqual(b.build_program_obj(prog("echo-animal-superpowers-2026"), ORG, 7)["uid"],
                         "echo-animal-superpowers-2026")

    def test_uid_does_not_depend_on_row_position(self):
        first = b.build_program_obj(prog("a-camp"), ORG, 1)
        later = b.build_program_obj(prog("a-camp"), ORG, 99)
        self.assertEqual(first["uid"], later["uid"])
        self.assertEqual((first["id"], later["id"]), (1, 99))
```

- [ ] **Step 2: Run** `python3 -m unittest discover -s tests/py -v` — Expected: FAIL, `'camp-7' != 'echo-animal-superpowers-2026'`.

- [ ] **Step 3: Set `"uid": prog.get("program_id", "") or f"{prog_type}-{uid}"` in `build_program_obj`**

- [ ] **Step 4: Make the card lookup selector-safe in `app.js`**

In `refreshExportUiForUid`, build the selector with `CSS.escape(uid)`. The smoke test cannot click, so pin this statically in `smoke_test.py`: `check("selector uses CSS.escape", "CSS.escape(uid)" in (ROOT / "app.js").read_text(encoding="utf-8"))`.

- [ ] **Step 5: Rebuild and run everything** — same command as Task 5 Step 7. Expected: all pass; `grep -c '"uid": "camp-[0-9]' data.js` prints `0`.

- [ ] **Step 6: Commit**

```bash
git add scripts/build_data_js.py tests/py/test_build_data_js.py app.js scripts/smoke_test.py data.js
git commit -m "fix: use program_id as the stable program uid"
```

---

### Task 7: Make the admin's local copy visible and discardable (bug 6)

**Files:**
- Modify: `lib.js`, `tests/js/lib.test.js`, `scripts/build_data_js.py` (`main`), `admin.html:881-888` (load), `admin.html:1486-1489` (save), toolbar markup near `id="dirtyBadge"`, `data.js` (regenerated)

**Interfaces:**
- Produces, in `data.js`: `const DATA_GENERATED = "2026-11-20T10:15:00";` using the same timestamp as the header comment.
- Produces: `CampLib.localEditsStatus(saved: {programs: string|null, orgs: string|null, stamp: string|null, savedAt: string|null}, currentStamp: string) -> {useLocal: boolean, stale: boolean, programs: object[]|null, orgs: object[]|null, savedAt: string|null}`
  - `useLocal` is `true` when `saved.programs` or `saved.orgs` parses as a JSON array; the unparseable or missing one comes back as `null`.
  - `stale` is `true` when `useLocal` and `saved.stamp !== currentStamp`.
- localStorage keys: existing `adminProgramData`, `adminOrgData`; new `adminDataStamp`, `adminSavedAt`.

- [ ] **Step 1: Write the failing tests**

```js
test('localEditsStatus: nothing saved', () => {
  const s = localEditsStatus({ programs: null, orgs: null, stamp: null, savedAt: null }, 'A');
  assert.deepEqual([s.useLocal, s.stale], [false, false]);
});
test('localEditsStatus: saved against the current build', () => {
  const s = localEditsStatus({ programs: '[{"uid":"x"}]', orgs: '[]', stamp: 'A', savedAt: '2026-11-20T09:00:00' }, 'A');
  assert.deepEqual([s.useLocal, s.stale], [true, false]);
  assert.deepEqual(s.programs, [{ uid: 'x' }]);
});
test('localEditsStatus: saved against an older build is stale', () => {
  const s = localEditsStatus({ programs: '[]', orgs: '[]', stamp: 'A', savedAt: 'x' }, 'B');
  assert.deepEqual([s.useLocal, s.stale], [true, true]);
});
test('localEditsStatus: copies from before stamping are stale', () => {
  assert.equal(localEditsStatus({ programs: '[]', orgs: null, stamp: null, savedAt: null }, 'B').stale, true);
});
test('localEditsStatus: corrupt JSON is ignored', () => {
  const s = localEditsStatus({ programs: '{not json', orgs: '"a string"', stamp: 'A', savedAt: 'x' }, 'A');
  assert.deepEqual([s.useLocal, s.programs, s.orgs], [false, null, null]);
});
```

- [ ] **Step 2: Run** `node --test tests/js/` — Expected: FAIL, `localEditsStatus is not a function`.

- [ ] **Step 3: Implement `localEditsStatus`; emit `DATA_GENERATED` from `build_data_js.py`**

- [ ] **Step 4: Wire into `admin.html`**

On load, call `CampLib.localEditsStatus` with the four keys and `typeof DATA_GENERATED !== 'undefined' ? DATA_GENERATED : ''`; `campRows` and `orgRows` come from the result when non-null, otherwise from `PROGRAMS` and `ORGANIZATIONS`. When `useLocal`, create a banner `<div id="localEditsBanner">` above the toolbar (the element does not exist otherwise) with the banner copy from Global Constraints (`{date}` from `savedAt`, or `an earlier visit` when null), the stale sentence when `stale`, and a `Discard local edits` button that removes all four keys and reloads. `saveAll` writes `adminDataStamp` and `adminSavedAt` (`new Date().toISOString()`) with the other two keys, and removes all four on a successful CSV save.

- [ ] **Step 5: Add smoke checks**

```python
dom = dump_dom("admin.html")
check("admin: table renders", dom.count("<tr") >= 6)
check("admin: no banner without local edits", 'id="localEditsBanner"' not in dom)
```

- [ ] **Step 6: Rebuild and run everything** — same command as Task 5 Step 7. Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add lib.js tests/js/lib.test.js scripts/build_data_js.py admin.html scripts/smoke_test.py data.js
git commit -m "fix: show and allow discarding the admin's local data copy"
```

---

### Task 8: Favicon (bug 5)

**Files:**
- Create: `favicon.svg`
- Modify: `index.html`, `afterschool.html`, `about.html`, `data.html`, `hackathon.html`, `admin.html` (add `<link rel="icon" href="favicon.svg" type="image/svg+xml" />` in `<head>`), `scripts/smoke_test.py`

- [ ] **Step 1: Add the failing check**

```python
for page in ["index", "afterschool", "about", "data", "hackathon", "admin"]:
    html = (ROOT / f"{page}.html").read_text(encoding="utf-8")
    check(f"favicon link: {page}", 'rel="icon" href="favicon.svg"' in html)
check("favicon file exists", (ROOT / "favicon.svg").is_file())
```

- [ ] **Step 2: Run** `python3 scripts/smoke_test.py` — Expected: seven `favicon` checks FAIL.

- [ ] **Step 3: Create `favicon.svg`** — a 64×64 SVG containing the 🏕️ emoji as centred text, matching the nav brand icon — **and add the link to all six pages.**

- [ ] **Step 4: Run** `python3 scripts/smoke_test.py` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add favicon.svg index.html afterschool.html about.html data.html hackathon.html admin.html scripts/smoke_test.py
git commit -m "fix: add a favicon"
```

---

### Task 9: Verify on the real data, locally and on GitHub Pages

**Files:** none changed.

- [ ] **Step 1: Run the full test command** from File Structure. Expected: all pass.

- [ ] **Step 2: Check the real pages locally**

Run `python3 scripts/dev_server.py`, then load each page in a browser:
- `index.html`: count line reads `Showing 382 of 382 camps` (no organization has 2027 programs yet, so nothing is superseded) with the 2026 notice; no console errors.
- Map view: more than 13 markers; the note counts the camps with no town (60 at the time of writing).
- `afterschool.html`: `Showing 10 of 10 afterschool programs`; no console errors.
- Save one camp for export, download the CSV, confirm "Full Address" has no leading comma.

- [ ] **Step 3: Deploy to the fork and repeat Step 2 there**

Merge `fix/site-bugs` into the fork's `main`, push, wait for the Pages build (`gh api repos/Melvillian/Camp-and-Afterschool-Care-Finder/pages/builds/latest --jq .status` prints `built`), then repeat the checks at `https://melvillian.github.io/Camp-and-Afterschool-Care-Finder/`.

- [ ] **Step 4: Open a pull request to `kefortney/Camp-and-Afterschool-Care-Finder`** once Melvillian confirms.
