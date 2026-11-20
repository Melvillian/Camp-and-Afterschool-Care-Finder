"""One season's weekly cycle, end to end, over a temp root: the seams between the scripts.

staging add -> push -> volunteers edit the Sheet -> pull -> merge -> watch marks `changed` -> push makes
check_changes -> a merge while the volunteer is unfinished -> the volunteer finishes -> pull -> merge.
Along the way: an enter_new task done exactly as docs/ingestion/volunteer-how-to.md says, and an Orgs-tab
cell the volunteer cleared. No network: the Sheet, the watched page, validate and build are all fakes.
"""
import contextlib, csv, io, json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import merge_staging
import sheet_sync as ss
import sources
import staging as st
import watch_sources

O1_PAGE = "https://one.example.org/camps"
O2_PAGE = "https://two.example.org/summer"


class FakeSheet:
    """The three tabs as lists of dicts. Rows arrive the way the runbook says: importing the _to_paste files."""

    def __init__(self):
        self.tabs = {"Tasks": [], "Drafts": [], "Orgs": []}

    def import_paste_files(self, folder: Path):
        for tab, name, columns in ss.PASTE_FILES:
            with open(folder / name, newline="", encoding="utf-8") as f:
                self.tabs[tab] += [dict(zip(columns, cells)) for cells in csv.reader(f)]

    def download(self, sheet_id, tab):
        columns = dict(ss.TABS)[tab]
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(columns)
        for row in self.tabs[tab]:
            w.writerow([row.get(c, "") for c in columns])
        return buf.getvalue()

    def task(self, task_id):
        return next(t for t in self.tabs["Tasks"] if t["task_id"] == task_id)

    def drafts(self, task_id):
        return [d for d in self.tabs["Drafts"] if d["task_id"] == task_id]

    def org_row(self, task_id):
        return next(o for o in self.tabs["Orgs"] if o["task_id"] == task_id)


def quiet(fn, *args, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = fn(*args, **kw)
    return code, out.getvalue()


class WeeklyCycle(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        data = self.root / "data"
        data.mkdir()
        (data / "programs.csv").write_text(",".join(st.PROGRAM_COLUMNS) + "\r\n", encoding="utf-8")
        (data / "organizations.csv").write_bytes(
            b"org_id,org_name,website,registration_opens,registration_opens_early,registration_notes,"
            b"financial_aid_available,financial_aid_notes,verified_date\r\n"
            b"o1,Org One,https://one.example.org,,,,,,\r\n"
            b"o2,Org Two,https://two.example.org,,,,,,\r\n")
        sources.save([
            {"org_id": "o1", "listing_url": O1_PAGE, "kind": "html", "robots_ai_optout": "FALSE",
             "status_2027": "not_posted"},
            {"org_id": "o2", "listing_url": O2_PAGE, "kind": "manual", "robots_ai_optout": "",
             "status_2027": "posted"},
        ], data / "sources.csv")
        self.sheet = FakeSheet()
        self.page = "<p>Summer 2027: Art Week, July 5-9, $250</p>"

    # ---- the maintainer's commands ------------------------------------------------------------------

    def skill_drafts(self, today):
        """What the extraction skill does for o1: staging add, add-org, status extracted."""
        rows = self.root / "rows.json"
        rows.write_text(json.dumps([{"program_name": "Art Week", "start_date": "2027-07-05", "end_date": "2027-07-09",
                                     "cost_raw": "$250", "source_url": O1_PAGE, "source_quote": "Art Week, July 5-9, $250"}]))
        self.assertEqual(quiet(st.main, ["staging.py", "add", "--org", "o1", "--json", str(rows)], root=self.root, today=today)[0], 0)
        facts = self.root / "facts.json"
        facts.write_text(json.dumps({"registration_opens": "2027-02-03"}))
        self.assertEqual(quiet(st.main, ["staging.py", "add-org", "--org", "o1", "--json", str(facts)], root=self.root)[0], 0)
        self.set_source("o1", status_2027="extracted")

    def push(self, today):
        code, out = quiet(ss.push, root=self.root, today=today)
        self.assertEqual(code, 0, out)
        self.sheet.import_paste_files(self.root / "data" / "staging" / "2027" / "sheet")
        return out

    def pull(self, today):
        code, out = quiet(ss.pull, download=self.sheet.download, root=self.root, today=today, sleep=lambda s: None)
        self.assertEqual(code, 0, out)
        return out

    def merge(self, today):
        code, out = quiet(merge_staging.main, ["merge_staging.py", "--all-ready"], root=self.root,
                          validate=lambda root: (True, ""), build=lambda root: (True, ""), today=today)
        self.assertEqual(code, 0, out)
        return out

    def watch(self, today):
        def fetch(url):
            return watch_sources.FetchResult(True, 200, url, "text/html", self.page.encode(), "")
        code, out = quiet(watch_sources.main, ["watch_sources.py", "--force"], fetch=fetch, sleep=lambda s: None,
                          today=today, path=self.root / "data" / "sources.csv")
        self.assertEqual(code, 0, out)
        return out

    # ---- reading the files ------------------------------------------------------------------------

    def source(self, org):
        return next(r for r in sources.load(self.root / "data" / "sources.csv") if r["org_id"] == org)

    def set_source(self, org, **values):
        rows = sources.load(self.root / "data" / "sources.csv")
        next(r for r in rows if r["org_id"] == org).update(values)
        sources.save(rows, self.root / "data" / "sources.csv")

    def programs(self, org):
        with open(self.root / "data" / "programs.csv", newline="", encoding="utf-8") as f:
            return [r for r in csv.DictReader(f) if r["org_id"] == org]

    def organization(self, org):
        with open(self.root / "data" / "organizations.csv", newline="", encoding="utf-8") as f:
            return next(r for r in csv.DictReader(f) if r["org_id"] == org)

    # ---- the season -------------------------------------------------------------------------------

    def test_a_season_of_weekly_runs(self):
        # Week 1 (2026-12-20): the skill drafts o1; push hands out check_draft (o1) and enter_new (o2).
        self.skill_drafts("2026-12-20")
        self.push("2026-12-20")
        self.assertEqual(sorted(t["task_id"] for t in self.sheet.tabs["Tasks"]),
                         ["2027-o1-check_draft", "2027-o2-enter_new"])
        self.assertEqual(self.sheet.org_row("2027-o1-check_draft")["registration_opens"], "2027-02-03")

        # Volunteers. o1: every draft row ok; the page does not give a registration date, so the
        # volunteer clears that Orgs cell ("Leave a cell blank if the page does not say").
        for d in self.sheet.drafts("2027-o1-check_draft"):
            d["review"] = "ok"
        self.sheet.org_row("2027-o1-check_draft")["registration_opens"] = ""
        self.sheet.task("2027-o1-check_draft")["status"] = "done"
        # o2, enter_new exactly as the how-to says: copy task_id and org_id, leave row_id blank, fill in
        # what the page says, mark the row's review, set the task done. Nothing about source_url.
        hand = dict.fromkeys(ss.DRAFT_COLUMNS, "")
        hand.update(task_id="2027-o2-enter_new", org_id="o2", program_name="Nature Camp", start_date="2027-07-12",
                    end_date="2027-07-16", start_time="09:00 AM", end_time="03:00 PM", cost_per_week="200", review="ok")
        self.sheet.tabs["Drafts"].append(hand)
        self.sheet.task("2027-o2-enter_new")["status"] = "done"

        # Week 2 (2026-12-27): pull stages both, merge publishes both.
        out = self.pull("2026-12-27")
        with self.subTest("F2: an enter_new row done as the how-to says is staged, source_url from sources.csv"):
            self.assertNotIn("NEEDS HELP", out)
            self.assertEqual([r["source_url"] for r in st.load("o2", 2027, self.root)], [O2_PAGE])
        self.merge("2026-12-27")
        self.assertEqual([(r["program_id"], r["cost_raw"], r["verified_date"], r["program_year"]) for r in self.programs("o1")],
                         [("o1-art-week-2027", "$250", "2026-12-27", "2027")])
        with self.subTest("F2: the hand-entered camp is published"):
            self.assertEqual([r["program_name"] for r in self.programs("o2")], ["Nature Camp"])
        with self.subTest("F5: a date the volunteer cleared in the Orgs tab is not published"):
            self.assertEqual(self.organization("o1")["registration_opens"], "")

        # The watch records the page, then sees it change: o1 becomes `changed`; push makes check_changes.
        self.watch("2026-12-27")
        self.page = "<p>Summer 2027: Art Week, July 5-9, now $275</p>"
        self.assertIn("CHANGED: o1", self.watch("2027-01-02"))
        self.push("2027-01-02")
        self.assertEqual([d["row_id"] for d in self.sheet.drafts("2027-o1-check_changes")], ["o1-art-week-2027"])

        # Week 3 (2027-01-03): nobody has done the check_changes task yet. Merge must not re-publish the
        # old staging file or stamp the old price as freshly verified.
        self.pull("2027-01-03")
        before = (self.root / "data" / "programs.csv").read_bytes()
        out = self.merge("2027-01-03")
        with self.subTest("F1: an unfinished check_changes task leaves the live rows alone"):
            self.assertIn("nothing ready to merge", out)
            self.assertEqual((self.root / "data" / "programs.csv").read_bytes(), before)
            self.assertEqual(self.source("o1")["status_2027"], "changed")

        # Week 4 (2027-01-10): the volunteer finishes; pull stages the new price; merge publishes it.
        draft = self.sheet.drafts("2027-o1-check_changes")[0]
        draft.update(review="fixed", cost_raw="$275")
        self.sheet.task("2027-o1-check_changes")["status"] = "done"
        self.pull("2027-01-10")
        out = self.merge("2027-01-10")
        with self.subTest("F1: the volunteer's update is merged once finished"):
            self.assertIn("merged o1", out)
            self.assertEqual([(r["cost_raw"], r["verified_date"]) for r in self.programs("o1")], [("$275", "2027-01-10")])
            self.assertEqual(self.source("o1")["status_2027"], "merged")

        # Week 5: nothing new anywhere, so nothing is merged again.
        self.pull("2027-01-17")
        self.assertIn("nothing ready to merge", self.merge("2027-01-17"))


if __name__ == "__main__":
    unittest.main()
