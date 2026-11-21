import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import merge_staging as m, sources, staging as st
from test_staging import make_root, run

P = lambda pid, **kw: {**dict.fromkeys(st.PROGRAM_COLUMNS, ""), "program_id": pid, "org_id": "o1", "program_name": pid, **kw}
S = lambda pid, **kw: {**P(pid, **kw), **dict.fromkeys(st.EXTRA, ""), **{k: v for k, v in kw.items() if k in st.EXTRA}}


class MergePrograms(unittest.TestCase):
    def test_add_update_retire(self):
        existing = [P("old-2026", confidence="confirmed"), P("b-2027", cost_raw="$100", confidence="confirmed"), P("c-2027", confidence="confirmed")]
        staged = [S("a-2027", review="ok", source_url="https://x"), S("b-2027", review="fixed", cost_raw="$120"),
                  S("c-2027", review="delete"), S("d-2027", review="delete")]
        out, counts = m.merge_programs(existing, staged, "2027-01-10", 2027)
        self.assertEqual([r["program_id"] for r in out], ["old-2026", "b-2027", "c-2027", "a-2027"])
        by = {r["program_id"]: r for r in out}
        self.assertEqual((by["b-2027"]["cost_raw"], by["b-2027"]["verified_date"], by["b-2027"]["confidence"]), ("$120", "2027-01-10", "confirmed"))
        self.assertEqual(by["c-2027"]["confidence"], "inactive")
        self.assertEqual(by["old-2026"], existing[0])
        self.assertEqual(set(by["a-2027"]), set(st.PROGRAM_COLUMNS))
        self.assertEqual(counts, {"added": 1, "updated": 1, "retired": 1})

    def test_program_year_is_the_season(self):
        out, _ = m.merge_programs([], [S("a-2027", review="ok", program_year="1999")], "2027-01-10", 2027)
        self.assertEqual(out[0]["program_year"], "2027")

    def test_merging_twice_changes_nothing_more(self):
        staged = [S("a-2027", review="ok")]
        once, _ = m.merge_programs([], staged, "2027-01-10", 2027)
        twice, counts = m.merge_programs(once, staged, "2027-01-10", 2027)
        self.assertEqual(once, twice)
        self.assertEqual(counts["added"], 0)

    def test_renamed_program_keeps_its_id(self):
        existing = [P("o1-camp-a-2027", program_name="Camp A", confidence="confirmed")]
        out, _ = m.merge_programs(existing, [S("o1-camp-a-2027", review="fixed", program_name="Camp A Renamed")], "2027-01-10", 2027)
        self.assertEqual([(r["program_id"], r["program_name"]) for r in out], [("o1-camp-a-2027", "Camp A Renamed")])


class MergeOrgs(unittest.TestCase):
    def test_non_blank_values_overwrite(self):
        existing = [{"org_id": "o1", "website": "https://old", "registration_opens": "", "verified_date": ""}]
        out, n = m.merge_orgs(existing, [{"org_id": "o1", "website": "", "registration_opens": "2027-02-04"}], "2027-01-10")
        self.assertEqual((out[0]["website"], out[0]["registration_opens"], out[0]["verified_date"], n),
                         ("https://old", "2027-02-04", "2027-01-10", 1))

    def test_only_existing_columns_never_org_id_and_unknown_org_not_added(self):
        existing = [{"org_id": "o1", "website": "", "verified_date": ""}]
        out, n = m.merge_orgs(existing, [{"org_id": "o1", "website": "https://w", "extra": "x"},
                                         {"org_id": "zzz", "website": "https://z"}], "2027-01-10")
        self.assertEqual(out, [{"org_id": "o1", "website": "https://w", "verified_date": "2027-01-10"}])
        self.assertEqual(n, 1)


def ok_validator(root):
    return True, "ok"


def bad_validator(root):
    return False, "ERROR: programs row 2 (o1-camp-a-2027): required field 'program_name' is blank"


class CliCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = make_root(Path(self._tmp.name))
        self.built = []
        sources.save([{"org_id": "o1", "kind": "html", "status_2027": "extracted"}, {"org_id": "o2", "kind": "html", "status_2027": "extracted"}],
                     self.root / "data" / "sources.csv")

    def stage(self, review="ok", org="o1", **kw):
        row = {**dict.fromkeys(st.STAGING_COLUMNS, ""), "program_id": f"{org}-camp-a-2027", "org_id": org, "program_name": "Camp A",
               "program_type": "camp", "program_year": "2027", "session_type": "day", "schedule_type": "weekly",
               "confidence": "likely", "start_date": "2027-07-05", "end_date": "2027-07-09",
               "source_url": "https://x.org", "review": review, **kw}
        st.save(org, 2027, [row], root=self.root)

    def merge(self, *argv, validate=ok_validator):
        return run(m.main, list(argv), self.root, validate=validate, build=lambda root: self.built.append(1) or (True, ""), today="2027-01-10")

    def status(self, org):
        return {r["org_id"]: r["status_2027"] for r in sources.load(self.root / "data" / "sources.csv")}[org]

    def read(self, name):
        return (self.root / "data" / name).read_bytes()


class Cli(CliCase):
    def test_refuses_blank_review_and_writes_nothing(self):
        self.stage(review="")
        before = self.read("programs.csv")
        code, out = self.merge("--org", "o1")
        self.assertEqual(code, 1)
        self.assertEqual(self.read("programs.csv"), before)
        self.assertEqual((self.status("o1"), self.built), ("extracted", []))

    def test_merges_keeps_crlf_and_marks_merged(self):
        self.stage()
        st.upsert_org_facts(2027, [{"org_id": "o1", "registration_opens": "2027-02-04", "bogus": "x"}], root=self.root)
        code, _ = self.merge("--org", "o1")
        self.assertEqual(code, 0)
        raw = self.read("programs.csv")
        self.assertTrue(raw.startswith(",".join(st.PROGRAM_COLUMNS).encode() + b"\r\n"))
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))
        self.assertIn(b"o1-camp-a-2027", raw)
        self.assertIn(b"2027-02-04", self.read("organizations.csv"))
        self.assertEqual((self.status("o1"), self.built), ("merged", [1]))

    def test_all_ready_skips_merged_and_unreviewed(self):
        self.stage(); self.stage(review="", org="o2")
        self.assertEqual(self.merge("--all-ready")[0], 0)
        self.assertEqual((self.status("o1"), self.status("o2")), ("merged", "extracted"))
        after_first = self.read("programs.csv")
        code, out = self.merge("--all-ready")
        self.assertEqual(code, 0)
        self.assertEqual(self.read("programs.csv"), after_first)  # verified_date not re-stamped
        self.assertEqual(len(self.built), 1)

    def test_validator_failure_restores_files(self):
        self.stage()
        st.upsert_org_facts(2027, [{"org_id": "o1", "registration_opens": "2027-02-04"}], root=self.root)
        before = (self.read("programs.csv"), self.read("organizations.csv"), self.read("sources.csv"))
        code, out = self.merge("--org", "o1", validate=bad_validator)
        self.assertEqual(code, 1)
        self.assertEqual((self.read("programs.csv"), self.read("organizations.csv"), self.read("sources.csv")), before)
        self.assertEqual(self.built, [])
        self.assertIn("o1", out)

    def test_id_belonging_to_another_org_is_refused(self):
        # programs.csv already holds o1-camp-a-2027 under o1; o2 stages the same id
        self.stage()
        self.merge("--org", "o1")
        self.stage(org="o2", program_id="o1-camp-a-2027")
        before = self.read("programs.csv")
        code, out = self.merge("--org", "o2")
        self.assertEqual(code, 1)
        self.assertEqual(self.read("programs.csv"), before)

    def test_existing_row_owned_by_another_org_is_refused(self):
        import csv
        row = {c: "" for c in st.PROGRAM_COLUMNS} | {"program_id": "o2-camp-a-2027", "org_id": "o1", "program_name": "x"}
        with open(self.root / "data" / "programs.csv", "a", newline="") as f:
            csv.writer(f, lineterminator="\r\n").writerow([row[c] for c in st.PROGRAM_COLUMNS])
        self.stage(org="o2")
        before = self.read("programs.csv")
        code, out = self.merge("--org", "o2")
        self.assertEqual(code, 1)
        self.assertIn("already belongs", out)
        self.assertEqual(self.read("programs.csv"), before)

    def test_same_id_in_two_staging_files_refuses_the_run(self):
        # o1-x's id also starts with "o1-", so a prefix check alone passes both files
        self.stage(org="o1", program_id="o1-x-camp-2027")
        self.stage(org="o1-x", program_id="o1-x-camp-2027")
        before = (self.read("programs.csv"), self.read("sources.csv"))
        for argv in (("--org", "o1", "--org", "o1-x"), ("--all-ready",)):
            code, out = self.merge(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("o1-x-camp-2027", out)
            self.assertEqual((self.read("programs.csv"), self.read("sources.csv")), before)
        self.assertEqual(self.status("o1"), "extracted")

    def test_renamed_row_keeps_id_through_merge(self):
        self.stage()
        self.merge("--org", "o1")
        self.stage(program_name="Camp A Renamed", review="fixed")
        self.assertEqual(self.merge("--org", "o1")[0], 0)
        raw = self.read("programs.csv").decode()
        self.assertEqual(raw.count("o1-camp-a-2027"), 1)
        self.assertIn("Camp A Renamed", raw)


class MergedOnce(CliCase):
    """F1: a merged staging file is not merged again until a pull replaces it."""

    def test_merged_file_is_marked_and_not_ready_again(self):
        self.stage()
        self.assertEqual(self.merge("--org", "o1")[0], 0)
        self.assertEqual([r["review"] for r in st.load("o1", 2027, self.root)], ["merged"])
        sources.save([{"org_id": "o1", "kind": "html", "status_2027": "changed"}], self.root / "data" / "sources.csv")
        after = self.read("programs.csv")
        code, out = self.merge("--all-ready")
        self.assertEqual((code, self.read("programs.csv")), (0, after))
        self.assertIn("nothing ready to merge", out)
        code, out = self.merge("--org", "o1")
        self.assertEqual(code, 1)
        self.assertIn("already merged", out)

    def test_file_with_no_sources_row_is_merged_once(self):
        sources.save([], self.root / "data" / "sources.csv")
        self.stage()
        self.assertEqual(self.merge("--all-ready")[0], 0)
        self.assertIn("nothing ready to merge", self.merge("--all-ready")[1])
        self.assertEqual(len(self.built), 1)

    def test_fresh_rows_from_a_pull_are_ready_again(self):
        self.stage()
        self.merge("--org", "o1")
        sources.save([{"org_id": "o1", "kind": "html", "status_2027": "changed"}], self.root / "data" / "sources.csv")
        self.stage(review="fixed", cost_raw="$300")
        self.assertEqual(self.merge("--all-ready")[0], 0)
        self.assertIn(b"$300", self.read("programs.csv"))


class SeasonOnly(CliCase):
    """F3, F4: merge never touches another season's row."""

    def add_live(self, pid, year):
        import csv
        row = {c: "" for c in st.PROGRAM_COLUMNS} | {"program_id": pid, "org_id": "o1", "program_name": "Old",
                                                     "program_year": year, "confidence": "confirmed"}
        with open(self.root / "data" / "programs.csv", "a", newline="") as f:
            csv.writer(f, lineterminator="\r\n").writerow([row[c] for c in st.PROGRAM_COLUMNS])

    def test_row_of_another_season_is_refused(self):
        self.add_live("o1-camp-a-2026", "2026")
        self.stage(program_id="o1-camp-a-2026", review="delete")
        before = self.read("programs.csv")
        code, out = self.merge("--org", "o1")
        self.assertEqual(code, 1)
        self.assertIn("is a 2026 row", out)
        self.assertEqual(self.read("programs.csv"), before)

    def test_staged_wrong_year_is_refused(self):
        self.stage(program_year="2026")
        code, out = self.merge("--org", "o1")
        self.assertEqual(code, 1)
        self.assertIn("program_year", out)


class OrgFactsMerge(CliCase):
    """F6, F21: organization facts are checked, printed old -> new, and applied once."""

    def org(self, org="o1"):
        import csv, io
        return next(r for r in csv.DictReader(io.StringIO(self.read("organizations.csv").decode())) if r["org_id"] == org)

    def test_changes_are_printed_and_the_facts_row_is_removed(self):
        self.stage()
        st.upsert_org_facts(2027, [{"org_id": "o1", "registration_opens": "2027-02-04", "website": "https://new.example/camps"}], self.root)
        code, out = self.merge("--org", "o1")
        self.assertEqual(code, 0, out)
        self.assertIn("o1 registration_opens: '' -> '2027-02-04'", out)
        self.assertIn("o1 website: 'https://old' -> 'https://new.example/camps'", out)
        self.assertIn("WARNING o1 website moves from old to new.example", out)
        self.assertEqual(st.load_org_facts(2027, root=self.root), [])
        # a later merge of fresh rows does not re-apply the old facts over a correction
        self.stage(review="fixed")
        self.assertEqual(self.merge("--org", "o1")[0], 0)
        self.assertEqual(self.org()["website"], "https://new.example/camps")

    def test_bad_facts_refuse_the_merge(self):
        self.stage()
        for bad in ({"website": "javascript:alert(1)"}, {"registration_opens": "2027-02-30"}):
            st.replace_org_facts(2027, [{"org_id": "o1", **bad}], self.root)
            before = self.read("organizations.csv")
            code, out = self.merge("--org", "o1")
            self.assertEqual(code, 1, bad)
            self.assertEqual(self.read("organizations.csv"), before)


class OrgFactsOnly(CliCase):
    """F7: --org-facts applies a registration date before any sessions are staged."""

    def facts(self, **kw):
        st.upsert_org_facts(2027, [{"org_id": "o1", **kw}], self.root)

    def test_applies_without_a_staging_file(self):
        self.facts(registration_opens="2027-02-04", registration_notes="Opens at noon")
        programs, srcs = self.read("programs.csv"), self.read("sources.csv")
        code, out = self.merge("--org-facts", "o1")
        self.assertEqual(code, 0, out)
        self.assertIn("o1 registration_opens: '' -> '2027-02-04'", out)
        self.assertIn(b"2027-02-04", self.read("organizations.csv"))
        self.assertEqual((self.read("programs.csv"), self.read("sources.csv"), self.built), (programs, srcs, [1]))
        self.assertEqual(st.load_org_facts(2027, root=self.root), [])

    def test_validation_failure_restores(self):
        self.facts(registration_opens="2027-02-04")
        before = self.read("organizations.csv")
        code, out = self.merge("--org-facts", "o1", validate=bad_validator)
        self.assertEqual((code, self.read("organizations.csv"), self.built), (1, before, []))
        self.assertEqual(len(st.load_org_facts(2027, root=self.root)), 1)

    def test_refusals(self):
        self.facts(website="javascript:alert(1)")
        for argv in (("--org-facts", "o1"), ("--org-facts", "o2"), ("--org-facts", "nope")):
            before = self.read("organizations.csv")
            code, out = self.merge(*argv)
            self.assertEqual((code, self.read("organizations.csv")), (1, before), argv)

    def test_one_mode_at_a_time(self):
        with self.assertRaises(SystemExit):
            self.merge("--org-facts", "o1", "--all-ready")
