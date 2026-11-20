import contextlib, csv, io, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import sheet_sync as ss
import sources
import staging

SRC = lambda **kw: {"org_id": "o1", "listing_url": "https://x.org", "kind": "html", "robots_ai_optout": "FALSE",
                    "expected_open": "2027-02-04", "status_2027": "not_posted", "notes": "", **kw}
ORGS = {"o1": {"org_name": "Org One"}, "o2": {"org_name": "Org Two"}}


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

    def test_unchecked_robots_is_entered_by_hand(self):
        self.assertEqual(ss.task_for(SRC(status_2027="posted", robots_ai_optout=""), False), ("enter_new", 45))

    def test_task_id_numbering(self):
        self.assertEqual(ss.task_id("o1", "check_changes", 2027), "2027-o1-check_changes")
        self.assertEqual(ss.task_id("o1", "check_changes", 2027, 3), "2027-o1-check_changes-3")


class Plan(unittest.TestCase):
    def test_new_tasks_drafts_and_no_duplicates(self):
        sources = [SRC(status_2027="extracted"), SRC(org_id="o2", listing_url="")]
        staged = {"o1": [{"program_id": "o1-camp-2027", "org_id": "o1", "program_name": "Camp", "source_url": "https://x.org", "source_quote": "July 5–9"}]}
        plan = ss.plan_push(sources, ORGS, staged, [], 2027, "2026-12-20")
        self.assertEqual([(t["task_id"], t["kind"], t["minutes"], t["status"], t["due"]) for t in plan.new_tasks],
                         [("2027-o1-check_draft", "check_draft", "10", "open", "2027-02-04"), ("2027-o2-find_page", "find_page", "2", "open", "")])
        self.assertEqual([(d["task_id"], d["row_id"], d["review"]) for d in plan.new_drafts], [("2027-o1-check_draft", "o1-camp-2027", "")])
        self.assertEqual([o["org_id"] for o in plan.new_orgs], ["o1"])
        again = ss.plan_push(sources, ORGS, staged, plan.new_tasks, 2027, "2026-12-20")
        self.assertEqual((again.new_tasks, again.new_drafts), ([], []))

    def test_stale_claims_are_released(self):
        snap = [{"task_id": "t1", "status": "claimed", "claimed_by": "Sam", "claimed_on": "2026-12-10"},
                {"task_id": "t2", "status": "claimed", "claimed_by": "Ana", "claimed_on": "2026-12-18"},
                {"task_id": "t3", "status": "claimed", "claimed_by": "Lee", "claimed_on": ""},
                {"task_id": "t4", "status": "claimed", "claimed_by": "Kim", "claimed_on": "last week"}]
        plan = ss.plan_push([], {}, {}, snap, 2027, "2026-12-20")
        self.assertEqual(sorted((c[0], c[1], c[2]) for c in plan.cell_changes),
                         [("t1", "claimed_by", ""), ("t1", "status", "open")])

    def test_claim_exactly_three_days_old_is_kept(self):
        snap = [{"task_id": "t1", "status": "claimed", "claimed_by": "Sam", "claimed_on": "2026-12-17"}]
        self.assertEqual(ss.plan_push([], {}, {}, snap, 2027, "2026-12-20").cell_changes, [])

    def test_find_page_has_no_orgs_row_and_no_drafts(self):
        plan = ss.plan_push([SRC(listing_url="")], ORGS, {"o1": [{"program_id": "x"}]}, [], 2027, "2026-12-20")
        self.assertEqual((len(plan.new_tasks), plan.new_drafts, plan.new_orgs), (1, [], []))

    def test_enter_new_has_orgs_row_prefilled_and_no_drafts(self):
        orgs = {"o1": {"org_name": "Org One", "website": "https://x.org", "registration_opens": "2027-01-10"}}
        plan = ss.plan_push([SRC(status_2027="posted", kind="manual")], orgs, {}, [], 2027, "2026-12-20")
        self.assertEqual([(t["kind"], t["minutes"]) for t in plan.new_tasks], [("enter_new", "45")])
        self.assertEqual(plan.new_drafts, [])
        org = plan.new_orgs[0]
        self.assertEqual((org["task_id"], org["org_id"], org["website"], org["registration_opens"]),
                         ("2027-o1-enter_new", "o1", "https://x.org", "2027-01-10"))
        self.assertEqual(list(org), ss.ORG_COLUMNS)

    def test_check_changes_plan(self):
        live = [{"program_id": "o1-camp-2027", "org_id": "o1", "program_name": "Camp", "cost_per_week": "300"}]
        plan = ss.plan_push([SRC(status_2027="changed")], ORGS, {"o1": live}, [], 2027, "2026-12-20")
        task = plan.new_tasks[0]
        self.assertEqual((task["task_id"], task["kind"], task["due"], task["source_url"]),
                         ("2027-o1-check_changes", "check_changes", "2027-02-04", "https://x.org"))
        draft = plan.new_drafts[0]
        self.assertEqual((draft["row_id"], draft["review"], draft["cost_per_week"], draft["source_url"]),
                         ("o1-camp-2027", "", "300", "https://x.org"))
        self.assertNotIn("program_id", draft)
        self.assertEqual(len(plan.new_orgs), 1)

    def test_no_second_task_while_first_is_open_then_numbered_repeat(self):
        src = [SRC(status_2027="changed")]
        first = {"task_id": "2027-o1-check_changes", "status": "open"}
        for status in ("open", "claimed", "needs_help"):
            plan = ss.plan_push(src, ORGS, {}, [{**first, "status": status}], 2027, "2026-12-20")
            self.assertEqual(plan.new_tasks, [], status)
        plan = ss.plan_push(src, ORGS, {}, [{**first, "status": "done"}], 2027, "2026-12-20")
        self.assertEqual([t["task_id"] for t in plan.new_tasks], ["2027-o1-check_changes-2"])
        snap = [{**first, "status": "done"}, {"task_id": "2027-o1-check_changes-2", "status": "done"}]
        self.assertEqual([t["task_id"] for t in ss.plan_push(src, ORGS, {}, snap, 2027, "x").new_tasks],
                         ["2027-o1-check_changes-3"])
        snap[1]["status"] = "open"
        self.assertEqual(ss.plan_push(src, ORGS, {}, snap, 2027, "x").new_tasks, [])

    def test_find_page_repeats_only_after_done(self):
        src = [SRC(listing_url="")]
        done = [{"task_id": "2027-o1-find_page", "status": "done"}]
        self.assertEqual([t["task_id"] for t in ss.plan_push(src, ORGS, {}, done, 2027, "x").new_tasks],
                         ["2027-o1-find_page-2"])

    def test_check_draft_and_enter_new_are_never_repeated(self):
        done = [{"task_id": "2027-o1-check_draft", "status": "done"}]
        plan = ss.plan_push([SRC(status_2027="extracted")], ORGS, {"o1": [{"program_id": "a"}]}, done, 2027, "x")
        self.assertEqual(plan.new_tasks, [])

    def test_rejected_done_task_does_not_spawn_a_repeat(self):
        src = [SRC(status_2027="changed")]
        snap = [{"task_id": "2027-o1-check_changes", "status": "done"}]
        plan = ss.plan_push(src, ORGS, {}, snap, 2027, "2026-12-20",
                            needs_help=[("2027-o1-check_changes", "row 1: bad date")])
        self.assertEqual(plan.new_tasks, [])
        self.assertEqual([(c[1], c[2]) for c in plan.cell_changes][0], ("status", "needs_help"))

    def test_needs_help_changes(self):
        snap = [{"task_id": "t1", "status": "done"}, {"task_id": "t2", "status": "needs_help"},
                {"task_id": "t3", "status": "done"}]
        plan = ss.plan_push([], {}, {}, snap, 2027, "2026-12-20",
                            needs_help=[("t1", "row 1: start_date is not a date"), ("t2", "again"), ("gone", "x")])
        self.assertEqual([(c[0], c[1], c[2]) for c in plan.cell_changes],
                         [("t1", "status", "needs_help"), ("t1", "volunteer_notes", "row 1: start_date is not a date")])


class PasteFormat(unittest.TestCase):
    def test_round_trip_and_formula_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            rows = [{"task_id": "t1", "org_id": "=SUM(A1)", "org_name": 'Camp, "Big"\nTop', "kind": "-x"}]
            ss.write_paste_file(path, ss.TASK_COLUMNS, rows)
            with open(path, newline="", encoding="utf-8") as f:
                back = list(csv.reader(f))
        self.assertEqual(len(back), 1)  # no header
        self.assertEqual(back[0][:4], ["t1", "'=SUM(A1)", 'Camp, "Big"\nTop', "'-x"])
        self.assertEqual(len(back[0]), len(ss.TASK_COLUMNS))

    def test_empty_file_is_still_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            ss.write_paste_file(path, ss.TASK_COLUMNS, [])
            self.assertEqual(path.read_text(), "")

    def test_change_lines_use_sheet_row_numbers(self):
        snap = [{"task_id": f"t{i}"} for i in range(13)] + [{"task_id": "2027-echo-check_draft"}]
        lines = ss.change_lines([("2027-echo-check_draft", "status", "open", "claim from 2026-12-10 expired"),
                                 ("2027-echo-check_draft", "claimed_by", "", "claim from 2026-12-10 expired")], snap)
        self.assertEqual(lines[0], "Tasks row 15 (2027-echo-check_draft): set status to open — claim from 2026-12-10 expired")
        self.assertTrue(lines[1].startswith("Tasks row 15 (2027-echo-check_draft): clear claimed_by"))


class Commands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        data = self.root / "data"
        data.mkdir()
        with open(data / "organizations.csv", "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["org_id", "org_name", "website", "registration_opens"])
            w.writerow(["alpha", "Alpha, Inc.", "https://alpha.org", "2027-01-10"])
            w.writerow(["beta", "Beta", "", ""])
            w.writerow(["gamma", "Gamma", "", ""])
        with open(data / "programs.csv", "w", newline="") as f:
            w = csv.DictWriter(f, staging.PROGRAM_COLUMNS, lineterminator="\n")
            w.writeheader()
            w.writerow({**dict.fromkeys(staging.PROGRAM_COLUMNS, ""), "program_id": "gamma-camp-2027", "org_id": "gamma",
                        "program_name": "Camp", "program_year": "2027", "confidence": "confirmed"})
            w.writerow({**dict.fromkeys(staging.PROGRAM_COLUMNS, ""), "program_id": "gamma-old-2027", "org_id": "gamma",
                        "program_name": "Old", "program_year": "2027", "confidence": "inactive"})
            w.writerow({**dict.fromkeys(staging.PROGRAM_COLUMNS, ""), "program_id": "gamma-2026", "org_id": "gamma",
                        "program_name": "Last", "program_year": "2026", "confidence": "confirmed"})
        sources.save([
            {**dict.fromkeys(sources.COLUMNS, ""), "org_id": "alpha", "listing_url": "https://alpha.org/c", "kind": "html",
             "robots_ai_optout": "FALSE", "expected_open": "2027-02-01", "status_2027": "extracted"},
            {**dict.fromkeys(sources.COLUMNS, ""), "org_id": "beta", "kind": "manual", "status_2027": "not_posted"},
            {**dict.fromkeys(sources.COLUMNS, ""), "org_id": "gamma", "listing_url": "https://gamma.org", "kind": "html",
             "robots_ai_optout": "FALSE", "status_2027": "changed"},
        ], data / "sources.csv")
        staging.save("alpha", 2027, [{**dict.fromkeys(staging.STAGING_COLUMNS, ""), "program_id": "alpha-camp-2027",
                                      "org_id": "alpha", "program_name": "Camp", "source_quote": "July 5, \"fun\""}],
                     root=self.root)
        self.sheet = self.root / "data" / "staging" / "2027" / "sheet"

    def run_main(self, *argv, today="2026-12-20"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ss.main(["sheet_sync.py", *argv], root=self.root, today=today)
        return code, out.getvalue()

    def snapshot_tasks(self, rows):
        ss._write_csv(self.sheet / "tasks.csv", ss.TASK_COLUMNS, rows)

    def tree(self):
        return {str(p): p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file()
                and not p.name.startswith("_to_paste") and p.name != ss.CHANGES_FILE}

    def paste(self, name):
        with open(self.sheet / name, newline="", encoding="utf-8") as f:
            return list(csv.reader(f))

    def test_push_end_to_end_with_no_snapshot(self):
        before = self.tree()
        code, out = self.run_main("push")
        self.assertEqual(code, 0)
        self.assertIn("Tasks: 3 row(s) to paste", out)
        self.assertIn("Drafts: 2 row(s) to paste", out)
        self.assertIn("Orgs: 2 row(s) to paste", out)
        self.assertEqual(self.tree(), before)  # snapshot, sources and staging untouched
        tasks = self.paste("_to_paste_tasks.csv")
        self.assertEqual([r[0] for r in tasks], ["2027-alpha-check_draft", "2027-beta-find_page", "2027-gamma-check_changes"])
        self.assertEqual(tasks[0][:8], ["2027-alpha-check_draft", "alpha", "Alpha, Inc.", "check_draft", "10", "2027-02-01",
                                        "https://alpha.org/c", "open"])
        drafts = self.paste("_to_paste_drafts.csv")
        self.assertEqual([(r[0], r[1]) for r in drafts],
                         [("2027-alpha-check_draft", "alpha-camp-2027"), ("2027-gamma-check_changes", "gamma-camp-2027")])
        self.assertEqual(drafts[0][-1], 'July 5, "fun"')
        self.assertEqual(drafts[0][-2], "https://alpha.org/c")  # blank staged source_url falls back to the listing
        self.assertEqual([r[:2] for r in self.paste("_to_paste_orgs.csv")],
                         [["2027-alpha-check_draft", "alpha"], ["2027-gamma-check_changes", "gamma"]])
        self.assertEqual(self.paste("_to_paste_orgs.csv")[0][2], "2027-01-10")
        self.assertEqual((self.sheet / "_changes.txt").read_text(), "")

    def test_push_overlays_org_facts_and_skips_existing_tasks(self):
        staging.upsert_org_facts(2027, [{"org_id": "alpha", "registration_opens": "2027-03-01", "website": ""}], self.root)
        self.snapshot_tasks([{c: "" for c in ss.TASK_COLUMNS} | {"task_id": "2027-beta-find_page", "status": "open"}])
        code, out = self.run_main("push")
        self.assertIn("Tasks: 2 row(s) to paste", out)
        orgs = self.paste("_to_paste_orgs.csv")
        self.assertEqual((orgs[0][2], orgs[0][7]), ("2027-03-01", "https://alpha.org"))

    def test_push_second_run_overwrites_paste_files_and_writes_changes(self):
        self.run_main("push")
        rows = [{c: "" for c in ss.TASK_COLUMNS} | {"task_id": t, "status": "open"}
                for t in ("2027-alpha-check_draft", "2027-beta-find_page", "2027-gamma-check_changes")]
        rows[1] |= {"task_id": "2027-beta-find_page", "status": "claimed", "claimed_by": "Sam", "claimed_on": "2026-12-10"}
        self.snapshot_tasks(rows)
        ss._write_csv(self.sheet / ss.NEEDS_HELP_FILE, ["task_id", "reason"],
                      [{"task_id": "2027-gamma-check_changes", "reason": "row 1: bad, date"}])
        code, out = self.run_main("push")
        self.assertIn("Tasks: 0 row(s) to paste", out)
        self.assertEqual((self.sheet / "_to_paste_tasks.csv").read_text(), "")
        lines = (self.sheet / "_changes.txt").read_text().splitlines()
        self.assertEqual(lines[0], "Tasks row 3 (2027-beta-find_page): set status to open — claim from 2026-12-10 expired")
        self.assertIn("Tasks row 4 (2027-gamma-check_changes): set status to needs_help", lines[2])
        self.assertIn("Cell changes: 4", out)

    def test_changed_org_uses_programs_rows_over_leftover_staging(self):
        staging.save("gamma", 2027, [{**dict.fromkeys(staging.STAGING_COLUMNS, ""), "program_id": "gamma-leftover-2027",
                                      "org_id": "gamma", "program_name": "Leftover"}], root=self.root)
        self.run_main("push")
        ids = [r[1] for r in self.paste("_to_paste_drafts.csv") if r[0] == "2027-gamma-check_changes"]
        self.assertEqual(ids, ["gamma-camp-2027"])  # inactive and 2026 rows are left out too

    def test_changed_org_without_live_rows_falls_back_to_leftover_staging(self):
        with open(self.root / "data" / "programs.csv", "w", newline="") as f:
            csv.DictWriter(f, staging.PROGRAM_COLUMNS, lineterminator="\n").writeheader()
        staging.save("gamma", 2027, [{**dict.fromkeys(staging.STAGING_COLUMNS, ""), "program_id": "gamma-leftover-2027",
                                      "org_id": "gamma", "program_name": "Leftover"}], root=self.root)
        self.run_main("push")
        ids = [r[1] for r in self.paste("_to_paste_drafts.csv") if r[0] == "2027-gamma-check_changes"]
        self.assertEqual(ids, ["gamma-leftover-2027"])

    def test_unreadable_snapshot_stops_push_and_missing_one_is_empty(self):
        self.sheet.mkdir(parents=True)
        (self.sheet / "tasks.csv").write_text("task_id,status\nt1,open\n", encoding="utf-8")  # columns missing
        code, out = self.run_main("push")
        self.assertEqual(code, 1)
        self.assertIn("Tasks", out)
        self.assertFalse((self.sheet / "_to_paste_tasks.csv").exists())
        (self.sheet / "tasks.csv").unlink()
        self.assertEqual(self.run_main("push")[0], 0)

    def test_init_writes_headers_how_to_and_steps(self):
        code, out = self.run_main("init")
        self.assertEqual(code, 0)
        init = self.sheet / "_init"
        for name, columns in (("tasks_header.csv", ss.TASK_COLUMNS), ("drafts_header.csv", ss.DRAFT_COLUMNS),
                              ("orgs_header.csv", ss.ORG_COLUMNS)):
            self.assertEqual(self.paste(f"_init/{name}"), [columns])
        self.assertEqual((init / "how_to.txt").read_text(encoding="utf-8"), ss.HOW_TO.read_text(encoding="utf-8"))
        self.assertIn("Tasks, Drafts, Orgs, How to", out)
        self.assertIn("Plain text BEFORE pasting", out)
        self.assertIn("row 1", out)


class HowTo(unittest.TestCase):
    def test_how_to_is_short_and_has_required_advice(self):
        text = ss.HOW_TO.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 45)
        for needle in ("2027-07-06", "Do not add, delete or reorder columns", "ok", "fixed", "delete", "blank"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
