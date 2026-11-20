# Runbook: getting 2027 camp listings onto the site

For the maintainer. You will run this for a few hours a week from mid-December 2026 to mid-March 2027. Read it top to bottom the first time. After that, the weekly routine (part 2) is enough.

The design and the reasons behind it are in `docs/superpowers/specs/2026-11-20-2027-camp-ingestion-design.md`. The instructions volunteers see are in `docs/ingestion/volunteer-how-to.md`.

## What the pieces are

- **`data/sources.csv`**: one row per organization. It holds the page to watch, how to read it (`kind`), and where it stands (`status_2027`).
- **The Google Sheet**: where volunteers check drafts and type in camps. Four tabs: Tasks, Drafts, Orgs, How to. Sheet id and the Tasks tab's `gid` are in `data/ingest_config.json`.
- **`data/staging/2027/`**: draft rows waiting for review, one file per organization, plus `_organizations.csv` for organization facts.
- **`data/staging/2027/sheet/`**: the saved copy of the Sheet from each pull, and the files `push` makes for you to paste.
- **`data/programs.csv` and `data/organizations.csv`**: the live data. Only `merge_staging.py` writes 2027 rows into them.

How `status_2027` moves:

| Status | Meaning | What moves it on |
|---|---|---|
| `not_posted` | Nothing for 2027 on the page yet | `watch_sources.py` sets `posted` when the page mentions 2027. For `rec1`, `gated` and `manual` sources you set it by hand (see below). |
| `posted` | 2027 is up; no rows yet | The extraction skill sets `extracted`. `enter_new` tasks (hand entry) are made for sources Claude may not read. |
| `extracted` | Draft rows are in staging | A volunteer checks them; `merge_staging.py` sets `merged`. |
| `merged` | Rows are live | `watch_sources.py` sets `changed` if the page text changes later. |
| `changed` | A live page changed | A volunteer updates the rows; merge sets `merged` again. |
| `not_running` | The camp is not running | You set it by hand. |

## 1. The weekly routine

The order matters. Do the steps in this order every time:

**pull, then merge, then watch, then extract, then push.**

Merge must come before push. `push` makes a new `check_changes` task when the old one is done but its organization is still `changed`. Merge is what sets the organization back to `merged`. Push first and you will hand volunteers the same task twice.

Run everything from the repo root, on a fresh branch from `main`. The scripts never run `git`; you commit at the end.

### 1.1 Pull what the volunteers did

```
python3 scripts/sheet_sync.py pull
```

It downloads the Tasks, Drafts and Orgs tabs, takes about four seconds (it waits two seconds between tabs), and prints:

- one line per task that changed, such as `2027-some-camp-check_draft: claimed -> done`, or `no task changes`;
- `Drafts rows: N added, N removed, N edited`;
- `staged N row(s) for <org_id>` for each task that was finished and passed the checks. Those rows are now in `data/staging/2027/<org_id>.csv`;
- `page for <org_id>: <url>` when a volunteer finished a `find_page` task. The address is now in `sources.csv`;
- `WARNING ...` for Tasks rows it skipped (no `task_id`, a repeated `task_id`, an odd status or kind);
- `NEEDS HELP <task_id>: <reason>` for finished tasks that failed the checks.

If it cannot read the Sheet it prints why and exits 1 without writing anything. The usual cause is that the Sheet is no longer shared so anyone with the link can view it (see parts 3 and 6).

**Rejected tasks.** A finished task whose rows fail the checks (a blank `review`, a bad date, a wrong `org_id`) is not staged. It goes into `data/staging/2027/sheet/_needs_help.csv` with the reason. On the next `push`, the task's `status` is set back to `needs_help` and the reason is put in its `volunteer_notes`. That is how the volunteer finds out. The change shows up as a `set status to needs_help` line in `_changes.txt` (see step 1.6). You do not need to do anything with these except paste the change.

**First real pull: check the Sheet's data before you trust it.** The pull code was tested only on made-up CSV, never on an actual Sheet. Google's CSV export guesses each column's type. When a cell does not match the rest of its column it may blank the cell, rewrite a date (`2027-07-05` as `7/5/2027`), or treat a blank row in a way the code does not expect. Setting every tab to Plain text (part 3) should prevent this, but nobody has confirmed it yet. On your first pull with real volunteer data:

1. Open `data/staging/2027/sheet/tasks.csv` and `drafts.csv` (the snapshot the pull just wrote).
2. Open the same rows in the Sheet. Compare five or six rows, including at least one with a date, one with a time, and one with an empty cell in the middle of the row.
3. If they match, carry on. If a value is blank, reformatted, or missing, **stop and do not merge.** Check that the tab is set to Plain text, fix it, run `pull` again, and compare again. If it still differs, open an issue and do not merge anything until it is understood.

Do this check again if you ever add a column or change the tab format.

The snapshot files under `data/staging/2027/sheet/` (`tasks.csv`, `drafts.csv`, `orgs.csv`, `snapshot.json`, `_needs_help.csv`) are committed. They are your backup of the Sheet (part 5). Three kinds of generated file are git-ignored and you do not commit them: `_to_paste_*.csv`, `_changes.txt`, and the `_init/` folder.

### 1.2 Look over each ready staging file

This is the last check before anything reaches the site. Volunteers and Claude both make mistakes, and the Sheet is open to anyone with the link until you lock it down (part 6). **Read each file you are about to merge.**

```
ls data/staging/2027/
```

Every `<org_id>.csv` there that is not already `merged` is a candidate. (`_organizations.csv` and the `sheet/` folder in that listing are not organization files; skip them.) For each one, open it and look at:

- **`source_quote` against the dates.** The quote is the sentence the row came from. Do the `start_date` and `end_date`, the price and the ages agree with it? Is any date in the wrong year?
- **Any row marked `delete`** in the `review` column. Make sure that camp really is gone. A `delete` sets the live row to `inactive`.
- **Rows with many blank cells.** A blank means "the page did not say", which is fine, but a row that is mostly blank may be a stub someone half-finished.
- **Rows with `review` blank.** Merge will skip the whole organization until every row has `ok`, `fixed` or `delete`.
- A sample of **prices and ages**. A typo here is the mistake nobody notices.

You can open the page behind a row with its `source_url`. For anything odd, fix the cell in the staging file, or set `review` to `delete`, then check it:

```
python3 scripts/staging.py check --org <org_id>
```

It prints the problems, or nothing wrong. An edit you make here is not copied back to the Sheet.

### 1.3 Merge

```
python3 scripts/merge_staging.py --all-ready
```

It merges every staging file that is not already `merged` and passes its checks. It prints `skipped <org_id>: not ready (<reason>)` for the others, and `merged a, b: N added, N updated, N retired` at the end. Or `nothing ready to merge`.

To merge only some:

```
python3 scripts/merge_staging.py --org <org_id> --org <other_org_id>
```

With `--org`, if any one of them is not ready, it refuses all of them and writes nothing.

Merge also copies the organization facts (`registration_opens` and so on) for each organization it merges, runs `validate_data.py` and `build_data_js.py`, and sets those organizations to `merged` in `sources.csv`.

**If it fails.** The message says `validation failed` or `build failed`, and it exits 1. It has already put `programs.csv` and `organizations.csv` back as they were, and left `sources.csv` alone, so nothing is half-done. The message lists `organizations whose rows may have caused it`, followed by the validator's output. Then:

1. Read the validator's message. It names a row and a column.
2. Fix that cell in the staging file for the organization named (or set that row's `review` to `delete`).
3. Run `python3 scripts/staging.py check --org <org_id>`, then merge again.
4. If you cannot fix it quickly, merge the others with `--org` and leave that one for next week.

`git diff data/` after a successful merge shows exactly what changed.

### 1.4 Watch the sources

```
python3 scripts/watch_sources.py
```

It fetches each `html` and `pdf` source that has not been checked in the last 6 days, one request every two seconds, so a full run takes several minutes. It prints:

- `POSTED: <org_id>`: a `not_posted` page now mentions 2027. Extract it (step 1.5).
- `CHANGED: <org_id>`: a `merged` page's text changed. The next `push` makes a `check_changes` task.
- `NEEDS A PERSON: <org_id> (<url>) <reason>`: the page failed to load or redirected to another site. A note `fetch failed <date>` is written in `sources.csv`, and the next `push` makes a `find_page` task for it.
- a last line: `checked N, N news, N need a person, N skipped (no checkable page), N checked recently`.

Useful options: `--org <org_id>` for one page, `--limit N` for the first N, `--dry-run` to write nothing, `--force` to check pages seen in the last 6 days. It saves every ten pages, so stopping it half way loses little.

**It only reads `html` and `pdf` sources.** Sources of kind `rec1`, `gated` or `manual` are never fetched. The Rec1 towns (Burlington, Colchester, South Burlington and Winooski) are among them. For those, check their sites by hand once a week. When one has 2027 sessions up, open `data/sources.csv`, set that row's `status_2027` to `posted`, and check the file:

```
python3 scripts/sources.py check
```

It prints `no problems`, or lists them. The next `push` makes an `enter_new` task for that organization.

### 1.5 Extract the newly posted sources

In Claude Code, from the repo root, ask:

> Use the extract-camp-listings skill on every source with status_2027 = posted.

(The skill is `.claude/skills/extract-camp-listings/SKILL.md`.) It does this on its own, in order:

- It picks only rows that are `posted`, of kind `html` or `pdf`, with `robots_ai_optout` exactly `FALSE`.
- It writes draft rows to `data/staging/2027/<org_id>.csv`, writes the organization's registration dates to `_organizations.csv`, and sets `status_2027` to `extracted`.
- It reports, for each organization, how many rows it drafted and what to look at first.

**Sources Claude may not read.** If `robots_ai_optout` is anything but exactly `FALSE`, the skill never fetches the site. When that source is `posted`, `push` makes an `enter_new` task, and a volunteer types the rows in. `TRUE` means the site's `robots.txt` names an AI crawler. A **blank** value means the check could not reach the site. To ask again for the blank ones only:

```
python3 scripts/sources.py robots
```

It checks one host at a time, two seconds apart, leaves `TRUE` and `FALSE` rows alone, and prints a count such as `12 hosts: 3 name AI crawlers, 1 failed`. A host that still failed stays blank, and is treated as off limits.

After the skill finishes, look at a couple of the files it made. Do not merge them yet: volunteers check them first.

### 1.6 Push the new tasks

```
python3 scripts/sheet_sync.py push
```

It writes to `data/staging/2027/sheet/` and prints something like:

```
Tasks: 12 row(s) to paste (12 find_page)
Drafts: 0 row(s) to paste
Orgs: 0 row(s) to paste
Cell changes: 0 (see _changes.txt)
Files are in .../data/staging/2027/sheet. For each tab: File > Import > Upload the _to_paste file, choose 'Append to current sheet', separator 'Comma', and turn OFF 'Convert text to numbers, dates and formulas'.
```

It does not touch the Sheet; nothing can write to the Sheet yet. You put the rows in by hand:

1. In the Sheet, click the tab (Tasks, then Drafts, then Orgs).
2. **File, Import, Upload** the matching file: `_to_paste_tasks.csv` into Tasks, `_to_paste_drafts.csv` into Drafts, `_to_paste_orgs.csv` into Orgs.
3. Import location: **Append to current sheet**. Separator type: **Comma**. **Turn off "Convert text to numbers, dates and formulas."**
4. Click **Import data**. Skip a file that has no rows.

**Do not copy the file's text and paste it.** Pasting CSV text puts every line into one cell instead of splitting it into columns. The files have no header row; they are meant to be appended under the existing headers.

Then apply `_changes.txt`. It holds one line per cell to change on the Tasks tab, each naming the row, the `task_id`, and the reason, such as `Tasks row 14 (2027-some-camp-check_draft): set status to needs_help — its rows did not pass the checks on the last pull`. Two kinds of change appear: a rejected task goes to `needs_help` with the reason in `volunteer_notes`; a claim over three days old goes back to `open` and loses its `claimed_by`. Edit those cells by hand. If the file is empty, there is nothing to do.

### 1.7 Test, commit, pull request

```
node --test tests/js/ && python3 -m unittest discover -s tests/py -v && python3 scripts/smoke_test.py
```

It should end with all three passing. (The smoke test needs Chrome.) From 2026-12-20 the Python suite fails on purpose until the Sheet is secured; see part 6. Then commit what changed under `data/` (the `sources.csv`, staging files, the snapshot in `data/staging/2027/sheet/`, `programs.csv`, `organizations.csv`) and `data.js`:

```
git status
git add data data.js
git commit -m "data: 2027 ingestion, week of <date>"
```

`data.js` changes only by running `python3 scripts/build_data_js.py`, which merge does for you. Do not edit it by hand. Open the pull request into the fork, not upstream:

```
gh pr create --repo Melvillian/Camp-and-Afterschool-Care-Finder --base main
```

Merging to the fork's `main` publishes the site. Upstream is handled separately.

## 2. The weekly routine in short

```
python3 scripts/sheet_sync.py pull            # read the volunteers' work; check the first real one against the Sheet
# read each staging file (part 1.2)
python3 scripts/merge_staging.py --all-ready
python3 scripts/watch_sources.py              # then check the rec1/gated/manual sites by hand
# Claude Code: run the extract-camp-listings skill
python3 scripts/sheet_sync.py push            # import the _to_paste files, apply _changes.txt
node --test tests/js/ && python3 -m unittest discover -s tests/py -v && python3 scripts/smoke_test.py
git add data data.js && git commit && gh pr create --repo Melvillian/Camp-and-Afterschool-Care-Finder --base main
```

Timing, from the design: weekly runs from mid-December. Early January is when the lottery and early-registration camps announce, so read those first (`expected_open` in `sources.csv` is filled in for only 7 organizations so far; add more as registration dates become known). February to mid-March is the long tail of camps that post late.

## 3. First-time Sheet setup

Do this once. The Sheet already exists (id in `data/ingest_config.json`). If it is empty, set it up like this.

```
python3 scripts/sheet_sync.py init
```

It writes four files to `data/staging/2027/sheet/_init/` (git-ignored): `tasks_header.csv`, `drafts_header.csv`, `orgs_header.csv` and `how_to.txt`. It prints the steps below.

1. **Name the four tabs exactly**: `Tasks`, `Drafts`, `Orgs`, `How to`. The scripts and the site look the tabs up by these names.
2. **Set every tab to Plain text before anything goes in.** In each tab, select all cells (click the grey square at the top left), then **Format, Number, Plain text**. Google's CSV export guesses column types and can blank cells or reformat dates. Plain text is what stops that. Do this first; changing the format afterwards does not repair values Google has already converted.
3. **Put the header row in each tab.** The files are one-line CSV. Pasting that line puts all the headers in one cell. Use **File, Import, Upload** instead, with **Append to current sheet**, **Comma**, and "Convert text to numbers, dates and formulas" **off**, the same as in step 1.6: `tasks_header.csv` into Tasks, `drafts_header.csv` into Drafts, `orgs_header.csv` into Orgs. On an empty tab, append starts at row 1. Check each tab ends up with its headers across row 1, one per column. (`init` says "paste"; import is the way that splits the columns.)
4. **How to tab.** Open `how_to.txt`, copy it all, double-click cell A1 of the How to tab so the cell is in edit mode, and paste. If the text spreads over many rows instead, undo and try again from edit mode.
5. Run `python3 scripts/sheet_sync.py push` and import the `_to_paste` files as in step 1.6. The first push on the real data makes 12 `find_page` tasks (one per source with no page or a broken one).
6. **Share it.** For now, "Anyone with the link" is an editor. Part 6 says how to tighten that. The Sheet must always stay viewable by anyone with the link, or `pull` and the Volunteer page stop working.
7. **Find the Tasks tab's gid.** Click the Tasks tab and look at the address bar: `.../edit#gid=123456789`. The number after `gid=` is the gid. Put it in `data/ingest_config.json` as `"tasks_gid": "123456789"` (keep the quotes). It is `"0"` now, which is right only if Tasks is the first tab. The Volunteer page (`volunteer.html`) uses it so its "Open in the Sheet" buttons jump to the right row. Commit the change.

Check it with one pull:

```
python3 scripts/sheet_sync.py pull
```

It should print the new tasks, and `Drafts rows: 0 added, 0 removed, 0 edited`.

## 4. Known limits

- A **duplicate `task_id`** in the Tasks tab (two rows with the same id) is skipped by the code (the later row), and the warning is printed on every pull until someone removes one of the rows.
- **`_changes.txt` row numbers assume there are no blank rows between tasks** in the Tasks tab. The code counts rows from the saved snapshot, which drops blank rows. If a blank row gets in, the row number can be off. Each line also names the `task_id`, so search for that instead. Better, delete the blank row.
- A Sheet that is not viewable by "anyone with the link" makes `pull` fail with a message that it came back as a web page.
- Anything you type into a staging file is not copied back to the Sheet, and the next `push` does not rewrite Drafts rows already pushed. The Sheet is the source until you merge.

## 5. Restoring a vandalised Sheet

Every pull saves the Sheet into `data/staging/2027/sheet/` (`tasks.csv`, `drafts.csv`, `orgs.csv`, `snapshot.json` with the date). They are committed, so git has the history.

1. Find the last good snapshot:
   ```
   git log --oneline -- data/staging/2027/sheet/tasks.csv
   ```
2. Check out those three files from that commit into a scratch folder, not over your working copy:
   ```
   git show <commit>:data/staging/2027/sheet/tasks.csv > /tmp/restore_tasks.csv
   git show <commit>:data/staging/2027/sheet/drafts.csv > /tmp/restore_drafts.csv
   git show <commit>:data/staging/2027/sheet/orgs.csv > /tmp/restore_orgs.csv
   ```
3. In the Sheet, try **File, Version history** first; Google may have a better copy than ours. If not, in each tab use **File, Import, Upload** with the matching restore file (it includes the header row), **Replace current sheet**, and "Convert text to numbers, dates and formulas" **off**. Then check the tab is still set to Plain text (part 3, step 2) and the headers are in row 1.
4. Run `python3 scripts/sheet_sync.py pull` and check the printed changes are what you expect.
5. If the vandal got to a staging file or the data before you merged, `git diff data/` shows it. Do not merge until it is clean.
6. Lock the Sheet down (part 6) if you have not already.

Work a volunteer did since the snapshot is lost; the Sheet's own version history may have it. Tell volunteers to redo anything finished since.

## 6. When the reminder test fails

From 2026-12-20, this test fails:

```
python3 -m unittest discover -s tests/py
```

with this message:

> REMINDER: the volunteer Google Sheet is still editable by anyone with the link. Restrict editing, set up the service-account key, then set "public_edit" to false in data/ingest_config.json.

It is on purpose. It stays red, and so does the full test command, until you lock the Sheet down and set `"public_edit": false` in `data/ingest_config.json`.

**What "locked down" means here:**

1. In the Sheet, click **Share**. Under General access, keep **Anyone with the link** but change the role from **Editor** to **Viewer**. Viewer is required: `pull` and the Volunteer page read the Sheet without signing in, and Restricted would break both.
2. Add each volunteer who should edit as an **Editor** by their Google account email. Anyone who is not on the list can still read the Sheet but cannot change it.
3. Set `"public_edit": false` in `data/ingest_config.json`. The test passes from then on.

Setting up the service-account key is the other half of the reminder, and it is **not built**. Writing to the Sheet directly would need the key and some code that does not exist yet (see part 8). Until then, you still paste by hand, as in step 1.6. You can do that as the owner, since you can edit; locking the Sheet down does not stop it. The test only asks that `public_edit` be false; it does not check for a key.

## 7. Testing the extraction skill after you edit it

If you change `.claude/skills/extract-camp-listings/SKILL.md`, test it on the invented page `tests/fixtures/pages/sample_camp.html`. The correct answer is in `tests/fixtures/pages/sample_camp.expected.json`. The page is for a made-up camp, `tumblestone-brook`, which is not in the real data. So run the test in a throwaway copy of the repo, never in the real one.

1. Copy the repo (no git history), and make the camp known in the copy:

   ```
   rsync -a --exclude .git --exclude node_modules ./ ../camp-finder-trial/
   cd ../camp-finder-trial
   ```

2. In the copy, add a row to `data/organizations.csv` (the columns are in its first line; fill `org_id` `tumblestone-brook`, `org_name`, `org_type` `nonprofit`, `city` `Burlington`, `county` `Chittenden`, `state` `VT`, `confidence` `likely`, and leave the rest blank). Add a row to `data/sources.csv`:

   ```
   tumblestone-brook,http://localhost:8765/sample_camp.html,html,FALSE,,,,posted,
   ```

   That gives `status_2027` `posted`, `kind` `html` and `robots_ai_optout` `FALSE`, so the skill will pick it. Check with `python3 scripts/sources.py check` (prints `no problems`) and `python3 scripts/validate_data.py` (ends `RESULT: PASS`).

3. Serve the fixture page, in a second terminal:

   ```
   python3 -m http.server 8765 --directory tests/fixtures/pages
   ```

4. In Claude Code, in the copy, ask it to run the extract-camp-listings skill for `tumblestone-brook`.

5. Compare `data/staging/2027/tumblestone-brook.csv` and `data/staging/2027/_organizations.csv` with `tests/fixtures/pages/sample_camp.expected.json`. It lists exact values to match and things that must not happen: three rows; dates and `cost_raw` as given; `end_time` blank on all rows; nothing built from the 2026 testimonial; `registration_opens` `2027-02-03`; `registration_opens_early` `2027-01-27`; every `source_quote` copied word for word from the page; no description copied from the page.

6. Delete the copy when done. Nothing in it belongs in the real repo. There is no script that does step 5 for you; it is a read-through against the file.

## 8. Not built yet, and when each is decided

- **Direct Sheet writes.** Today `push` makes files and you import them. Writing straight to the Sheet needs a Google service-account key, which you will create. It also needs a way to sign tokens that the Python standard library cannot do, so the choice (one optional dependency, or calling `openssl`) is made when the key exists. `push` already separates "work out what to write" from "write it", so the writing can be added alone. Decide this when the key exists, and not before.
- **The Rec1 browser route.** Burlington, Colchester, South Burlington and Winooski use Rec1, which blocks plain HTTP requests. A script that drives a real browser would get past that. It is not built. The plan is to ask the departments for a spreadsheet export first (the letter is `docs/ingestion/rec1-export-request.md`). **Decide in early January:** if no export has arrived by then, choose whether to build the browser route or let volunteers type those camps in. Until then, the Rec1 towns are `enter_new` tasks, set by hand (step 1.4).
- Who sends the export letter is still open. Nothing else waits on it.
