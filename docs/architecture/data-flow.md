# How data flows through Camp Finder

Camp Finder is a static site. There is no server and no database: the data lives in CSV files in this repo, a script turns them into one JavaScript file, and GitHub Pages serves the result. The 2027 ingestion pipeline adds a way to get new-season camp listings into those CSV files, with a person checking every row first.

This page describes the flow as the code stands in November 2026. For the weekly commands, see [the runbook](../ingestion/runbook.md). For column definitions, see [the data architecture reference](../../camp-finder-data-architecture.md).

## 1. The whole system

```mermaid
flowchart TD
    subgraph outside["Outside the repo"]
        orgsites["Organization web pages"]
        sheet["Google Sheet<br/>Tasks, Drafts, Orgs, How to"]
        volunteers["Volunteers"]
    end

    subgraph machine["Maintainer's machine"]
        watch["watch_sources.py"]
        skill["extract-camp-listings skill<br/>(Claude Code)"]
        sync["sheet_sync.py<br/>pull / push"]
        merge["merge_staging.py"]
        build["validate_data.py<br/>then build_data_js.py"]
    end

    subgraph repo["Files in the repo"]
        sources["data/sources.csv<br/>one row per organization"]
        staging["data/staging/2027/<br/>draft rows, one file per organization"]
        live["data/programs.csv<br/>data/organizations.csv"]
        datajs["data.js (generated)"]
        tasks["data/staging/2027/sheet/tasks.csv<br/>redacted copy of the Tasks tab"]
    end

    subgraph site["GitHub Pages (fork's main branch)"]
        pages["Camps and Afterschool pages"]
        volpage["Volunteer page"]
    end

    orgsites -->|"fetched weekly"| watch
    watch -->|"sets posted / changed"| sources
    sources -->|"posted, and the site allows AI"| skill
    orgsites -->|"read once"| skill
    skill -->|"draft rows"| staging

    sources --> sync
    staging --> sync
    sync -->|"files the maintainer imports by hand"| sheet
    volunteers -->|"type directly into"| sheet
    orgsites -->|"read by"| volunteers
    sheet -->|"public CSV export"| sync
    sync -->|"checked rows"| staging
    sync -->|"found page addresses"| sources
    sync --> tasks

    staging -->|"maintainer reviews, then"| merge
    merge --> live
    merge -->|"sets merged"| sources
    live --> build
    build --> datajs

    datajs --> pages
    datajs --> volpage
    sheet -->|"Tasks tab, read live in the browser"| volpage
    tasks -->|"fallback when the Sheet is unreachable"| volpage
```

Three things in this picture are easy to get wrong:

- **Nothing writes to the Sheet by program.** There is no Google API token anywhere in the repo or the site. Volunteers type into the Sheet themselves, signed in to their own Google accounts or as anonymous link visitors. `push` only writes files on the maintainer's machine, and the maintainer imports them into the Sheet by hand.
- **Everything reads the Sheet anonymously.** `pull` and the Volunteer page both use the Sheet's public CSV export, which works only while the Sheet is shared as "Anyone with the link: Viewer" or wider.
- **Only `merge_staging.py` writes 2027 rows into the live CSV files.** The skill, the volunteers and `pull` all stop at `data/staging/2027/`.

## 2. One organization, from "nothing posted" to "live"

Each organization's row in `data/sources.csv` carries a `status_2027`. The pipeline is the movement of that status.

```mermaid
stateDiagram-v2
    [*] --> not_posted
    not_posted --> posted: watch finds 2027 on the page, or the maintainer sets it by hand
    posted --> extracted: the skill stages draft rows
    posted --> not_posted: the skill finds no real sessions
    extracted --> merged: a volunteer checks the drafts, then merge
    posted --> merged: a volunteer types the rows in, then merge
    merged --> changed: watch sees the page text change
    changed --> merged: a volunteer updates the rows, then merge
    not_posted --> not_running: set by hand
    merged --> [*]
```

Which path an organization takes after `posted` depends on two columns of its row:

| `kind` | `robots_ai_optout` | Who reads the page | Task a volunteer gets |
|---|---|---|---|
| `html` or `pdf` | `FALSE` | The skill drafts rows | `check_draft` (10 minutes) |
| `html` or `pdf` | `TRUE` or blank | A volunteer | `enter_new` (45 minutes) |
| `rec1`, `gated`, `manual` | any | A volunteer | `enter_new` (45 minutes) |

Two other tasks exist. `find_page` (2 minutes) is made when an organization has no page address or the watcher could not fetch it. `check_changes` (10 minutes) is made when a merged organization's page changes.

## 3. The Sheet round trip

The Sheet is the only place volunteers work. The scripts treat it as an untrusted inbox: they read it, check what they read, and never let it write to the live data directly.

```mermaid
sequenceDiagram
    autonumber
    participant M as Maintainer
    participant S as sheet_sync.py
    participant G as Google Sheet
    participant V as Volunteer
    participant P as Volunteer page

    M->>S: push
    S-->>M: _to_paste_tasks.csv, _to_paste_drafts.csv,<br/>_to_paste_orgs.csv, _changes.txt
    M->>G: File > Import each file, apply cell changes by hand

    P->>G: fetch the Tasks tab as CSV (no sign-in)
    P-->>V: list of open tasks, with a link to each row
    V->>G: claim a task, check or type rows, set status to done

    M->>S: pull
    S->>G: download Tasks, Drafts, Orgs as CSV (no sign-in)
    S-->>M: what changed since the last pull
    Note over S: Finished tasks that pass the checks<br/>become staging files.<br/>Failed ones go to _needs_help.csv.
    M->>M: read each staging file, then run merge_staging.py
```

What `pull` writes, and where it ends up:

| File | Contents | Committed? |
|---|---|---|
| `data/staging/2027/<org_id>.csv` | Rows from finished tasks that passed the checks | Yes |
| `data/sources.csv` | Page addresses from finished `find_page` tasks | Yes |
| `data/staging/2027/sheet/tasks.csv` | The Tasks tab with names, claim dates and notes blanked | Yes |
| `data/staging/2027/sheet/snapshot.json` | The date of the pull | Yes |
| `data/staging/2027/sheet/private/` | The three tabs exactly as downloaded, and `_needs_help.csv` | No, git-ignored |

Volunteer names are kept out of the repo by that redaction. They are still readable in the Sheet itself by anyone with the link.

## 4. From CSV to the page a family sees

```mermaid
flowchart LR
    programs["data/programs.csv"] --> validate["validate_data.py"]
    orgs["data/organizations.csv"] --> validate
    validate -->|"no errors"| build["build_data_js.py"]
    build --> datajs["data.js<br/>PROGRAMS, ORGANIZATIONS, TOWN_COORDS"]

    datajs --> app["app.js + lib.js"]
    app --> camps["index.html (Camps)"]
    app --> after["afterschool.html"]
    datajs --> admin["admin.html<br/>(local editing tool)"]
    admin -->|"POST /__save_csv,<br/>local dev server only"| programs
    admin --> orgs
```

- `build_data_js.py` runs the validator first and stops on any error. It joins each program with its organization's fields, drops inactive programs, and writes `data.js`.
- `data.js` is committed. GitHub Pages serves the repo's files as they are, so a change reaches the site when a commit containing the new `data.js` lands on the fork's `main` branch.
- The Camps and Afterschool pages load `data.js` with a script tag and do all filtering, mapping and calendar work in the browser. They make no data requests of their own.
- The Admin page can save only through `scripts/dev_server.py` on the maintainer's machine. On the published site its save has nowhere to go.

## 5. Where untrusted input enters

| Input | Who can supply it | What stands between it and the site |
|---|---|---|
| Organization page text | The organization, or whoever controls the page | The skill treats page text as data; a volunteer checks each draft row against the page; the maintainer reads each staging file before merge |
| Sheet cells | Anyone with edit access to the Sheet | `pull` rejects formula cells, bad dates, wrong ids and non-public addresses; the maintainer reads each staging file before merge |
| A page address from a `find_page` task | Anyone with edit access to the Sheet | `pull` refuses non-http addresses, bare IP addresses and local names. It cannot tell whether a site belongs to the organization, so the maintainer opens each new address before running the watcher |

The last row is the reason to restrict who can edit the Sheet: the watcher fetches every address in `data/sources.csv` from the maintainer's machine.

## Not built

These appear in the plans but do not exist in the code:

- Writing to the Sheet from a script (a service-account key).
- A reader for Rec1 registration sites; those four towns are checked and typed in by hand.
- An importer for a spreadsheet export from a recreation department.
- Any check of the Sheet's real sharing setting. `public_edit` in `data/ingest_config.json` is a note the maintainer keeps, not a measurement.
