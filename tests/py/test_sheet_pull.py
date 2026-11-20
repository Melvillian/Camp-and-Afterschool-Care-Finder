import contextlib, csv, hashlib, io, json, shutil, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import sheet_sync as ss
import sources
import staging

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sheet"
TODAY = "2027-01-10"
KNOWN = {"o1": {"o1-camp-2027"}, "o2": {"o2-camp-2027"}, "o3": {"o3-camp-2027"}}  # ids a Drafts row_id may name


class Parse(unittest.TestCase):
    def test_parses_and_strips(self):
        rows = ss.parse_tab("task_id,status\n t1 , done \n,\n", ["task_id", "status"], "Tasks")
        self.assertEqual(rows, [{"task_id": "t1", "status": "done"}])

    def test_missing_column_and_empty_tab(self):
        with self.assertRaisesRegex(ss.SheetError, "Tasks tab is missing column\\(s\\): status"):
            ss.parse_tab("task_id,claimed_by\nt1,x\n", ["task_id", "status"], "Tasks")
        with self.assertRaisesRegex(ss.SheetError, "Drafts tab is empty"):
            ss.parse_tab("", ["task_id"], "Drafts")

    def test_reordered_padded_and_extra_columns(self):
        rows = ss.parse_tab(" status , my note ,task_id\ndone,hi,t1\nopen\n", ["task_id", "status"], "Tasks")
        self.assertEqual([(r["task_id"], r["status"]) for r in rows], [("t1", "done"), ("", "open")])

    def test_header_only_tab_is_no_rows(self):
        self.assertEqual(ss.parse_tab("task_id,status\n", ["task_id", "status"], "Tasks"), [])

    def test_sign_in_page_is_an_error(self):
        with self.assertRaisesRegex(ss.SheetError, "Tasks tab .*not CSV"):
            ss.parse_tab("<!DOCTYPE html><html><body>Sign in</body></html>", ["task_id"], "Tasks")

    def test_tab_url_encodes_the_name(self):
        self.assertTrue(ss.tab_url("ID", "How to").endswith("/d/ID/gviz/tq?tqx=out:csv&headers=1&sheet=How%20to"))


    def test_positions_count_blank_rows(self):
        rows, positions = ss.parse_tab_rows('task_id\nt1\n,\n\n"t\n2"\nt3\n', ["task_id"], "Tasks")
        self.assertEqual(([r["task_id"] for r in rows], positions), (["t1", "t\n2", "t3"], [2, 5, 6]))

    def test_guard_apostrophe_is_dropped_once(self):
        rows = ss.parse_tab("a,b,c,d\n'=SUM(1),'-5,''+x,'plain\n", ["a"], "Tasks")
        self.assertEqual(rows, [{"a": "=SUM(1)", "b": "-5", "c": "''+x", "d": "'plain"}])


class Redact(unittest.TestCase):
    def test_names_and_notes_blank_org_name_from_organizations_and_rows_keep_their_place(self):
        text = ("task_id,org_id,org_name,kind,minutes,due,source_url,status,claimed_by,claimed_on,volunteer_notes,extra\n"
                "t1,o1,<i>Evil</i>,find_page,2,,,claimed,Sam Smith,2027-01-05,call me 555-1234,junk\n"
                ",,,,,,,,,,,\n"
                "t2,zz,Typed,check_draft,10,2027-02-04,https://x.org,open,,,,\n")
        back = list(csv.DictReader(io.StringIO(ss.redacted_tasks(text, {"o1": "Org One"}))))
        self.assertEqual(list(back[0]), ss.TASK_COLUMNS)
        self.assertEqual([(r["task_id"], r["org_name"], r["claimed_by"], r["claimed_on"], r["volunteer_notes"]) for r in back],
                         [("t1", "Org One", "", "", ""), ("", "", "", "", ""), ("t2", "", "", "", "")])
        self.assertEqual((back[0]["status"], back[2]["due"]), ("claimed", "2027-02-04"))


class Diff(unittest.TestCase):
    def test_reports_status_and_claim_changes(self):
        old = [{"task_id": "t1", "status": "open", "claimed_by": ""}, {"task_id": "t2", "status": "open", "claimed_by": ""}]
        new = [{"task_id": "t1", "status": "claimed", "claimed_by": "Sam"}, {"task_id": "t3", "status": "open", "claimed_by": ""}]
        lines = ss.diff_tasks(old, new)
        self.assertEqual(len(lines), 3)
        self.assertTrue(any("t1" in l and "claimed" in l for l in lines))

    def test_no_changes(self):
        t = [{"task_id": "t1", "status": "open", "claimed_by": ""}]
        self.assertEqual(ss.diff_tasks(t, t), [])


class Collect(unittest.TestCase):
    def setUp(self):
        self.tasks = [{"task_id": "t1", "org_id": "o1", "kind": "check_draft", "status": "done"},
                      {"task_id": "t2", "org_id": "o2", "kind": "check_draft", "status": "done"},
                      {"task_id": "t3", "org_id": "o3", "kind": "check_draft", "status": "claimed"},
                      {"task_id": "t4", "org_id": "o4", "kind": "find_page", "status": "done", "volunteer_notes": "https://o4.org/camps"}]
        d = lambda task, rid, **kw: {**dict.fromkeys(ss.DRAFT_COLUMNS, ""), "task_id": task, "row_id": rid, "org_id": task.replace("t", "o"),
                                     "program_name": "Camp", "start_date": "2027-07-05", "end_date": "2027-07-09",
                                     "source_url": "https://x.org", "review": "ok", **kw}
        self.d = d
        self.drafts = [d("t1", "o1-camp-2027"), d("t1", "", program_name="Added by hand"),
                       d("t2", "o2-camp-2027", start_date="7/5/2027"), d("t3", "o3-camp-2027")]

    def run_collect(self, **kw):
        return ss.collect_done(self.tasks, self.drafts, kw.get("orgs", []), kw.get("already", set()),
                               {"o1", "o2", "o3", "o4"}, 2027, "2027-01-10", known_ids=KNOWN, **kw.get("extra", {}))

    def test_ready_rejected_and_untouched(self):
        staged, orgs, rejected = self.run_collect()
        self.assertEqual(set(staged), {"o1"})
        self.assertEqual([r["program_id"] for r in staged["o1"]], ["o1-camp-2027", "o1-added-by-hand-2027"])
        self.assertEqual([t for t, _ in rejected], ["t2"])
        self.assertIn("start_date", rejected[0][1])

    def test_staged_rows_are_complete_and_keep_review(self):
        staged, _, _ = self.run_collect()
        row = staged["o1"][1]
        self.assertEqual((row["review"], row["org_id"], row["program_year"], row["extracted_on"], row["program_type"]),
                         ("ok", "o1", "2027", "2027-01-10", "camp"))
        self.assertEqual(list(row), staging.STAGING_COLUMNS)

    def test_bad_task_does_not_block_good_one_and_blank_review_rejects(self):
        self.drafts[0]["review"] = ""
        staged, _, rejected = self.run_collect()
        self.assertEqual(set(staged), set())
        self.assertEqual(sorted(t for t, _ in rejected), ["t1", "t2"])
        self.assertIn("review", dict(rejected)["t1"])

    def test_blank_required_cell_rejects_only_that_task(self):
        self.drafts[3]["program_name"] = ""
        self.tasks[2]["status"] = "done"
        self.drafts[2]["start_date"] = "2027-07-05"
        staged, _, rejected = self.run_collect()
        self.assertEqual(set(staged), {"o1", "o2"})
        self.assertEqual([t for t, _ in rejected], ["t3"])
        self.assertIn("program_name", rejected[0][1])

    def test_taken_extra_ids_are_never_reused(self):
        staged, _, _ = ss.collect_done(self.tasks, self.drafts, [], set(), {"o1", "o2", "o3", "o4"}, 2027,
                                       "2027-01-10", taken_extra={"o1-added-by-hand-2027"}, known_ids=KNOWN)
        self.assertEqual([r["program_id"] for r in staged["o1"]], ["o1-camp-2027", "o1-added-by-hand-2027-2"])

    def test_already_done_tasks_are_skipped(self):
        staged, _, rejected = self.run_collect(already={"t1", "t2"})
        self.assertEqual((staged, rejected), ({}, []))

    def test_row_ids_are_unique_across_tasks_of_one_org(self):
        self.tasks.append({"task_id": "t5", "org_id": "o1", "kind": "enter_new", "status": "done"})
        self.drafts.append(self.d("t5", "", org_id="o1", program_name="Added by hand"))
        staged, _, rejected = self.run_collect()
        self.assertEqual([r["program_id"] for r in staged["o1"]],
                         ["o1-camp-2027", "o1-added-by-hand-2027", "o1-added-by-hand-2027-2"])
        self.assertEqual([t for t, _ in rejected], ["t2"])

    def test_done_task_without_rows_or_org_row_is_rejected(self):
        self.tasks.append({"task_id": "t6", "org_id": "o3", "kind": "check_changes", "status": "done"})
        _, _, rejected = self.run_collect()
        self.assertIn(("t6", "no rows"), rejected)

    def test_org_facts_for_ready_task(self):
        org = {"task_id": "t1", "org_id": "o1", "registration_opens": "2027-03-01", "website": "https://o1.org"}
        _, orgs, rejected = self.run_collect(orgs=[org])
        self.assertEqual(orgs, [{**dict.fromkeys(staging.ORG_STAGING_COLUMNS, ""), "org_id": "o1",
                                 "registration_opens": "2027-03-01", "website": "https://o1.org"}])
        self.assertEqual([t for t, _ in rejected], ["t2"])

    def test_bad_org_date_rejects_the_task(self):
        org = {"task_id": "t1", "org_id": "o1", "registration_opens": "3/1/2027"}
        staged, orgs, rejected = self.run_collect(orgs=[org])
        self.assertEqual((staged, orgs), ({}, []))
        self.assertEqual(sorted(t for t, _ in rejected), ["t1", "t2"])

    def test_org_row_only_task_is_ready_without_staging_rows(self):
        self.tasks.append({"task_id": "t7", "org_id": "o3", "kind": "check_changes", "status": "done"})
        org = {"task_id": "t7", "org_id": "o3", "registration_notes": "Lottery"}
        staged, orgs, rejected = self.run_collect(orgs=[org])
        self.assertNotIn("o3", staged)
        self.assertEqual([o["org_id"] for o in orgs], ["o3"])
        self.assertNotIn("t7", [t for t, _ in rejected])

    def test_typed_row_id_must_be_a_current_row_of_the_org(self):
        # F3: an id from another season, another org, or nowhere rejects the task
        for rid in ("o1-camp-2026", "o2-camp-2027", "o1-made-up-2027"):
            self.drafts[0]["row_id"] = rid
            staged, _, rejected = self.run_collect()
            self.assertNotIn("o1", staged, rid)
            self.assertIn("is not one of o1's current rows", dict(rejected)["t1"])

    def test_hand_added_row_gets_the_listing_url_from_sources(self):
        # F2: a blank source_url on a row with no row_id comes from sources.csv, never from the Tasks row
        self.drafts[1]["source_url"] = ""
        self.tasks[0]["source_url"] = "https://evil.example"
        staged, _, rejected = self.run_collect(extra={"listing_urls": {"o1": "https://o1.org/camps"}})
        self.assertEqual(staged["o1"][1]["source_url"], "https://o1.org/camps")
        self.drafts[0]["source_url"] = ""  # a row with a row_id is not filled in
        _, _, rejected = self.run_collect(extra={"listing_urls": {"o1": "https://o1.org/camps"}})
        self.assertIn("source_url is blank", dict(rejected)["t1"])

    def test_bad_orgs_row_website_rejects_the_task(self):
        org = {"task_id": "t1", "org_id": "o1", "website": "javascript:alert(1)"}
        staged, _, rejected = self.run_collect(orgs=[org])
        self.assertNotIn("o1", staged)
        self.assertIn("website", dict(rejected)["t1"])

    def test_formula_cells_reject_the_task(self):
        # F17: a cell still starting with '=' after the apostrophe guard is dropped
        self.drafts[0]["description"] = '=HYPERLINK("https://evil.example","Book now")'
        staged, _, rejected = self.run_collect()
        self.assertNotIn("o1", staged)
        self.assertIn("Drafts row 1: description starts with '='", dict(rejected)["t1"])
        self.drafts[0]["description"] = ""
        _, _, rejected = self.run_collect(orgs=[{"task_id": "t1", "org_id": "o1", "registration_notes": "=1+1"}])
        self.assertIn("Orgs row: registration_notes starts with '='", dict(rejected)["t1"])

    def test_find_page_tasks_are_ignored_here(self):
        staged, orgs, rejected = self.run_collect()
        self.assertNotIn("o4", staged)
        self.assertNotIn("t4", [t for t, _ in rejected])


class FindPage(unittest.TestCase):
    def tasks(self, **kw):
        return [{"task_id": "t4", "org_id": "o4", "kind": "find_page", "status": "done", "volunteer_notes": "", **kw}]

    def test_url_from_notes_then_orgs_website(self):
        urls, rejected = ss.find_page_urls(self.tasks(volunteer_notes="https://o4.org/camps"), [], set())
        self.assertEqual((urls, rejected), ({"o4": "https://o4.org/camps"}, []))
        orgs = [{"task_id": "t4", "website": "https://o4.org"}]
        urls, _ = ss.find_page_urls(self.tasks(), orgs, set())
        self.assertEqual(urls, {"o4": "https://o4.org"})

    def test_no_url_is_rejected(self):
        urls, rejected = ss.find_page_urls(self.tasks(volunteer_notes="could not find it"), [], set())
        self.assertEqual(urls, {})
        self.assertEqual([t for t, _ in rejected], ["t4"])

    def test_local_and_bare_ip_addresses_are_rejected(self):
        # F8
        for url in ("http://127.0.0.1:8000/admin", "http://localhost/x", "http://10.0.0.5/", "http://router.lan/",
                    "http://2130706433/"):
            urls, rejected = ss.find_page_urls(self.tasks(volunteer_notes=url), [], set())
            self.assertEqual((urls, [t for t, _ in rejected]), ({}, ["t4"]), url)
        urls, rejected = ss.find_page_urls(self.tasks(volunteer_notes="=HYPERLINK(\"https://o4.org\")"), [], set())
        self.assertEqual((urls, [t for t, _ in rejected]), ({}, ["t4"]))

    def test_skips_already_done_open_and_other_kinds(self):
        self.assertEqual(ss.find_page_urls(self.tasks(volunteer_notes="https://a.org"), [], {"t4"}), ({}, []))
        self.assertEqual(ss.find_page_urls(self.tasks(status="open"), [], set()), ({}, []))
        self.assertEqual(ss.find_page_urls(self.tasks(kind="enter_new", volunteer_notes="https://a.org"), [], set()), ({}, []))


def fake_download(**overrides):
    """A download function serving the fixture tabs; overrides maps a tab name to text."""
    files = {"Tasks": "tasks.csv", "Drafts": "drafts.csv", "Orgs": "orgs.csv"}

    def download(sheet_id, tab):
        if tab in overrides:
            value = overrides[tab]
            if isinstance(value, Exception):
                raise value
            return value
        return (FIXTURES / files[tab]).read_text(encoding="utf-8")
    return download


class PullCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        data = self.root / "data"
        data.mkdir()
        with open(data / "organizations.csv", "w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["org_id", "org_name"])
            for o in ("alpha", "beta", "gamma", "delta"):
                w.writerow([o, o.title()])
        sources.save([
            {"org_id": "gamma", "listing_url": "https://old.example.org", "kind": "html", "status_2027": "not_posted",
             "notes": "fetch failed 2026-12-01: HTTP 404; keep me"},
            {"org_id": "alpha", "listing_url": "https://alpha.example.org/camps", "kind": "html", "status_2027": "not_posted"},
        ], data / "sources.csv")
        # The fixture's Drafts rows name these live rows (as a check_changes task would).
        self.write_programs([("alpha-camp-2027", "alpha"), ("beta-camp-2027", "beta")])
        self.sheet = self.root / "data" / "staging" / "2027" / "sheet"
        self.private = self.sheet / "private"

    def write_programs(self, ids_and_orgs, year="2027"):
        with open(self.root / "data" / "programs.csv", "w", newline="") as f:
            w = csv.DictWriter(f, staging.PROGRAM_COLUMNS, lineterminator="\n")
            w.writeheader()
            for pid, org in ids_and_orgs:
                w.writerow({**dict.fromkeys(staging.PROGRAM_COLUMNS, ""), "program_id": pid, "org_id": org,
                            "program_name": "Camp", "program_year": year, "confidence": "confirmed"})

    def pull(self, download=None, today=TODAY):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ss.pull(download=download or fake_download(), root=self.root, today=today, sleep=lambda s: None)
        return code, out.getvalue() + err.getvalue()

    def tree(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.root.rglob("*")) if p.is_file()}

    def read_csv(self, path):
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))


class Pull(PullCase):
    def test_good_pull_writes_everything(self):
        code, out = self.pull()
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(str(p.relative_to(self.sheet)) for p in self.sheet.rglob("*") if p.is_file()),
                         ["private/_needs_help.csv", "private/drafts.csv", "private/orgs.csv", "private/tasks.csv",
                          "snapshot.json", "tasks.csv"])
        for tab, name in (("Tasks", "tasks.csv"), ("Drafts", "drafts.csv"), ("Orgs", "orgs.csv")):
            self.assertEqual((self.private / name).read_bytes(), fake_download()("ID", tab).encode())
        self.assertEqual(json.loads((self.sheet / "snapshot.json").read_text()), {"pulled_on": TODAY})
        staged = staging.load("alpha", 2027, self.root)
        self.assertEqual([r["program_id"] for r in staged], ["alpha-camp-2027", "alpha-added-by-hand-2027"])
        self.assertFalse(staging.staging_path("beta", 2027, self.root).exists())
        facts = staging.load_org_facts(2027, self.root)
        self.assertEqual([(f["org_id"], f["registration_opens"]) for f in facts], [("alpha", "2027-03-01")])
        helped = self.read_csv(self.private / "_needs_help.csv")
        self.assertEqual([h["task_id"] for h in helped], ["t-beta"])
        self.assertIn("start_date", helped[0]["reason"])
        self.assertIn("t-beta", out)
        # find_page url applied to sources.csv, failure note removed, other notes kept
        gamma = next(r for r in sources.load(self.root / "data" / "sources.csv") if r["org_id"] == "gamma")
        self.assertEqual((gamma["listing_url"], gamma["kind"], gamma["notes"]),
                         ("https://gamma.example.org/summer.pdf", "pdf", "keep me"))

    def test_cleared_orgs_cell_clears_the_staged_fact(self):
        # F5: the reviewed Orgs row replaces the stored facts, blanks included
        staging.upsert_org_facts(2027, [{"org_id": "alpha", "registration_opens_early": "2027-02-01",
                                         "registration_notes": "drafted"}], self.root)
        self.pull()
        fact = staging.load_org_facts(2027, self.root)[0]
        self.assertEqual((fact["registration_opens"], fact["registration_opens_early"], fact["registration_notes"]),
                         ("2027-03-01", "", "Opens at noon"))

    def test_new_host_blanks_robots_and_hash_and_prints_both_addresses(self):
        # F8: the robots answer and hash belonged to the old site
        rows = sources.load(self.root / "data" / "sources.csv")
        rows[0].update(robots_ai_optout="FALSE", last_hash="abc")
        sources.save(rows, self.root / "data" / "sources.csv")
        code, out = self.pull()
        gamma = next(r for r in sources.load(self.root / "data" / "sources.csv") if r["org_id"] == "gamma")
        self.assertEqual((gamma["robots_ai_optout"], gamma["last_hash"]), ("", ""))
        self.assertIn("page for gamma: https://old.example.org -> https://gamma.example.org/summer.pdf", out)
        self.assertIn("run sources.py robots", out)

    def test_same_host_keeps_robots_answer(self):
        rows = sources.load(self.root / "data" / "sources.csv")
        rows[0].update(listing_url="https://gamma.example.org/old", robots_ai_optout="FALSE", last_hash="abc")
        sources.save(rows, self.root / "data" / "sources.csv")
        self.pull()
        gamma = next(r for r in sources.load(self.root / "data" / "sources.csv") if r["org_id"] == "gamma")
        self.assertEqual((gamma["listing_url"], gamma["robots_ai_optout"], gamma["last_hash"]),
                         ("https://gamma.example.org/summer.pdf", "FALSE", "abc"))

    def test_committed_tasks_csv_is_redacted(self):
        # F10: the only committed copy of the Sheet has no names, notes or Sheet-typed organization names
        self.pull()
        tasks = self.read_csv(self.sheet / "tasks.csv")
        self.assertEqual([t["task_id"] for t in tasks], ["t-alpha", "t-beta", "t-gamma", "t-delta"])
        self.assertEqual({t["claimed_by"] + t["claimed_on"] + t["volunteer_notes"] for t in tasks}, {""})
        self.assertEqual([t["org_name"] for t in tasks], ["Alpha", "Beta", "Gamma", "Delta"])
        self.assertNotIn("Sam", (self.sheet / "tasks.csv").read_text())
        self.assertIn("Sam", (self.private / "tasks.csv").read_text())

    def test_fresh_clone_without_the_private_copy_does_not_restage_done_tasks(self):
        self.pull()
        shutil.rmtree(self.private)
        staging.save("alpha", 2027, [], self.root)
        code, out = self.pull()
        self.assertEqual(code, 0, out)
        self.assertEqual(staging.load("alpha", 2027, self.root), [])
        self.assertNotIn("staged", out)

    def test_find_page_for_org_without_a_sources_row_adds_one(self):
        sources.save([], self.root / "data" / "sources.csv")
        self.pull()
        rows = sources.load(self.root / "data" / "sources.csv")
        self.assertEqual([(r["org_id"], r["listing_url"], r["kind"], r["status_2027"]) for r in rows],
                         [("gamma", "https://gamma.example.org/summer.pdf", "pdf", "not_posted")])

    def test_second_identical_pull_changes_nothing(self):
        self.pull()
        before = self.tree()
        code, out = self.pull()
        self.assertEqual(code, 0)
        self.assertEqual(self.tree(), before)

    def test_rejected_task_is_rechecked_and_staged_once_fixed(self):
        self.pull()
        fixed = (FIXTURES / "drafts.csv").read_text().replace("7/5/2027", "2027-07-05")
        code, out = self.pull(fake_download(Drafts=fixed))
        self.assertEqual(code, 0)
        self.assertEqual([r["program_id"] for r in staging.load("beta", 2027, self.root)], ["beta-camp-2027"])
        self.assertEqual(self.read_csv(self.private / "_needs_help.csv"), [])

    def test_rejected_task_stays_listed_on_every_pull(self):
        self.pull()
        self.pull()
        self.assertEqual([h["task_id"] for h in self.read_csv(self.private / "_needs_help.csv")], ["t-beta"])

    def test_prints_task_diff_and_draft_changes(self):
        self.pull()
        tasks = (FIXTURES / "tasks.csv").read_text().replace("t-delta,delta,Delta Sports,enter_new,30,2027-03-01,,claimed,Kim",
                                                            "t-delta,delta,Delta Sports,enter_new,30,2027-03-01,,done,Kim")
        lines = (FIXTURES / "drafts.csv").read_text().replace("Added by hand", "Changed by hand").splitlines()
        code, out = self.pull(fake_download(Tasks=tasks, Drafts="\n".join(lines[:-1]) + "\n"))
        self.assertEqual(code, 0)
        self.assertIn("t-delta", out)
        self.assertIn("claimed -> done", out)
        self.assertRegex(out, r"Drafts rows: 0 added, 1 removed, 1 edited")

    def test_unknown_status_and_kind_are_reported_and_skipped(self):
        tasks = (FIXTURES / "tasks.csv").read_text().replace(",claimed,Kim", ",donee,Kim").replace("find_page", "find-page")
        code, out = self.pull(fake_download(Tasks=tasks))
        self.assertEqual(code, 0, out)
        self.assertIn("t-delta", out)
        self.assertIn("donee", out)
        self.assertIn("find-page", out)
        self.assertEqual(sources.load(self.root / "data" / "sources.csv")[0]["listing_url"], "https://old.example.org")

    def test_extra_columns_and_reordered_columns_still_parse(self):
        rows = list(csv.reader(io.StringIO((FIXTURES / "tasks.csv").read_text())))
        rows = [list(reversed(r)) + [("note" if i == 0 else "x")] for i, r in enumerate(rows)]
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        code, out = self.pull(fake_download(Tasks=buf.getvalue()))
        self.assertEqual(code, 0, out)
        self.assertTrue(staging.staging_path("alpha", 2027, self.root).exists())

    def test_same_org_tasks_are_combined_and_replace_the_old_file(self):
        staging.save("alpha", 2027, [dict.fromkeys(staging.STAGING_COLUMNS, "")] , self.root)
        tasks = (FIXTURES / "tasks.csv").read_text() + "t-alpha2,alpha,Alpha Camp,enter_new,30,2027-03-01,,done,Jo,,\n"
        drafts = (FIXTURES / "drafts.csv").read_text()
        extra = {c: "" for c in ss.DRAFT_COLUMNS}
        extra.update(task_id="t-alpha2", review="fixed", org_id="alpha", program_name="Winter Camp",
                     start_date="2027-02-01", end_date="2027-02-05", source_url="https://alpha.example.org")
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerow([extra[c] for c in ss.DRAFT_COLUMNS])
        code, out = self.pull(fake_download(Tasks=tasks, Drafts=drafts + buf.getvalue()))
        self.assertEqual(code, 0, out)
        self.assertEqual([r["program_id"] for r in staging.load("alpha", 2027, self.root)],
                         ["alpha-camp-2027", "alpha-added-by-hand-2027", "alpha-winter-camp-2027"])


class PullSafety(PullCase):
    def test_skipped_done_task_is_staged_once_fixed(self):
        bad = (FIXTURES / "tasks.csv").read_text().replace("t-alpha,alpha,Alpha Camp,check_draft", "t-alpha,alpha,Alpha Camp,check draft")
        code, out = self.pull(fake_download(Tasks=bad))
        self.assertEqual(code, 0, out)
        self.assertFalse(staging.staging_path("alpha", 2027, self.root).exists())
        self.assertIn("t-alpha", [h["task_id"] for h in self.read_csv(self.private / "_needs_help.csv")])
        code, out = self.pull()
        self.assertEqual([r["program_id"] for r in staging.load("alpha", 2027, self.root)],
                         ["alpha-camp-2027", "alpha-added-by-hand-2027"])

    def test_duplicate_done_task_id_is_listed(self):
        dup = (FIXTURES / "tasks.csv").read_text() + "t-alpha,alpha,Alpha Camp,check_draft,10,,,done,Zed,,\n"
        self.pull(fake_download(Tasks=dup))
        self.assertIn("t-alpha", [h["task_id"] for h in self.read_csv(self.private / "_needs_help.csv")])

    def test_crash_while_staging_leaves_the_previous_snapshot(self):
        from unittest import mock
        self.pull()
        snap = {p: p.read_bytes() for p in self.sheet.rglob("*") if p.is_file()}
        fixed = (FIXTURES / "drafts.csv").read_text().replace("7/5/2027", "2027-07-05")
        with mock.patch.object(staging, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.pull(fake_download(Drafts=fixed), today="2027-02-02")
        self.assertEqual({p: p.read_bytes() for p in self.sheet.rglob("*") if p.is_file()}, snap)

    def test_hand_added_row_never_takes_an_existing_program_id(self):
        self.write_programs([("alpha-camp-2027", "alpha"), ("alpha-added-by-hand-2027", "alpha")])
        self.pull()
        self.assertEqual([r["program_id"] for r in staging.load("alpha", 2027, self.root)],
                         ["alpha-camp-2027", "alpha-added-by-hand-2027-2"])

    def test_oversized_cell_is_a_message(self):
        code, out = self.pull(fake_download(Orgs="task_id,org_id\n" + "x" * 200000 + ",a\n"))
        self.assertEqual(code, 1)
        self.assertIn("Orgs tab could not be read as CSV", out)


class PullStops(PullCase):
    def assert_stops_untouched(self, download, expect):
        self.pull()
        before = self.tree()
        code, out = self.pull(download, today="2027-02-02")
        self.assertEqual(code, 1)
        self.assertIn(expect, out)
        self.assertEqual(self.tree(), before)

    def test_missing_column_names_it_and_keeps_the_snapshot(self):
        text = (FIXTURES / "tasks.csv").read_text().replace("status", "state", 1)
        self.assert_stops_untouched(fake_download(Tasks=text), "Tasks tab is missing column(s): status")

    def test_wiped_tab(self):
        self.assert_stops_untouched(fake_download(Drafts=""), "Drafts tab is empty")

    def test_not_public_sheet(self):
        self.assert_stops_untouched(fake_download(Orgs="<!DOCTYPE html><html>Sign in</html>"), "not CSV")

    def test_download_failure_is_a_message_not_a_traceback(self):
        self.assert_stops_untouched(fake_download(Orgs=ss.SheetError("could not download the Orgs tab: HTTP 500")),
                                    "could not download the Orgs tab: HTTP 500")

    def test_first_ever_pull_with_empty_tab_writes_nothing(self):
        code, out = self.pull(fake_download(Tasks=""))
        tree = self.tree()
        self.assertEqual((code, sorted(tree)), (1, ["data/organizations.csv", "data/programs.csv", "data/sources.csv"]))
        self.assertIn("Tasks tab is empty", out)
        self.assertFalse(self.sheet.exists())


class Download(unittest.TestCase):
    def test_network_failure_becomes_sheet_error(self):
        import urllib.error, urllib.request
        from unittest import mock
        with mock.patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("no route")):
            with self.assertRaisesRegex(ss.SheetError, "could not download the Tasks tab: no route"):
                ss.download_tab("ID", "Tasks")
        err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        with mock.patch.object(urllib.request, "urlopen", side_effect=err):
            with self.assertRaisesRegex(ss.SheetError, "HTTP 403"):
                ss.download_tab("ID", "Tasks")


if __name__ == "__main__":
    unittest.main()
