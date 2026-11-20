import contextlib, io, json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
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

    def test_same_name_twice_in_one_batch_gets_distinct_ids(self):
        rows = st.prepare([{"program_name": "Same"}, {"program_name": "Same"}], "o1", 2027, set(), "2026-12-15")
        self.assertEqual([r["program_id"] for r in rows], ["o1-same-2027", "o1-same-2027-2"])


class Checks(unittest.TestCase):
    def good(self, **kw):
        return {**dict.fromkeys(st.STAGING_COLUMNS, ""), "program_id": "o1-a-2027", "org_id": "o1", "program_name": "A",
                "program_type": "camp", "program_year": "2027", "session_type": "day", "schedule_type": "weekly",
                "confidence": "likely", "start_date": "2027-07-05", "end_date": "2027-07-09",
                "source_url": "https://x.org/camps", **kw}

    def test_good_row(self):
        self.assertEqual(st.check_rows([self.good()], {"o1"}), [])

    def test_each_problem_is_one_error(self):
        for bad in (dict(start_date="7/5/2027"), dict(start_date="2026-07-05"), dict(end_date="2028-01-01"),
                    dict(source_url=""), dict(review="maybe"), dict(org_id="nope"), dict(program_name="")):
            self.assertEqual(len(st.check_rows([self.good(**bad)], {"o1"})), 1, bad)

    def test_deleted_rows_need_no_source(self):
        self.assertEqual(st.check_rows([self.good(review="delete", source_url="")], {"o1"}), [])

    def test_duplicate_ids_are_an_error(self):
        self.assertEqual(len(st.check_rows([self.good(), self.good()], {"o1"})), 1)


def make_root(tmp: Path) -> Path:
    (tmp / "data").mkdir()
    prog_header = ",".join(st.PROGRAM_COLUMNS)
    (tmp / "data" / "programs.csv").write_bytes((prog_header + "\r\n").encode())
    (tmp / "data" / "organizations.csv").write_bytes(
        b"org_id,org_name,website,registration_opens,registration_opens_early,registration_notes,"
        b"financial_aid_available,financial_aid_notes,verified_date\r\no1,One,https://old,,,,,,\r\no2,Two,,,,,,,\r\n")
    return tmp


def run(main, argv, root, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["x"] + argv, root=root, **kw)
    return code, out.getvalue() + err.getvalue()


class AddCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        self.json = self.root / "rows.json"
        self.addCleanup(self._tmp.cleanup)

    def add(self, rows, org="o1"):
        self.json.write_text(json.dumps(rows))
        return run(st.main, ["add", "--org", org, "--json", str(self.json)], self.root, today="2026-12-15")

    def good(self, **kw):
        return {"program_name": "Camp A", "start_date": "2027-07-05", "end_date": "2027-07-09",
                "source_url": "https://x.org/camps", **kw}

    def test_bad_row_exits_1_and_writes_nothing(self):
        code, _ = self.add([self.good(), self.good(program_name="Camp B", start_date="2026-07-05")])
        self.assertEqual(code, 1)
        self.assertFalse(st.staging_path("o1", 2027, root=self.root).exists())

    def test_unknown_key_and_unknown_org_are_rejected(self):
        self.assertEqual(self.add([self.good(bogus="x")])[0], 1)
        self.assertEqual(self.add([self.good()], org="nope")[0], 1)
        self.assertFalse(st.staging_path("o1", 2027, root=self.root).exists())

    def test_same_name_twice_gets_dash_2_and_keeps_both(self):
        self.assertEqual(self.add([self.good()])[0], 0)
        self.assertEqual(self.add([self.good()])[0], 0)
        ids = [r["program_id"] for r in st.load("o1", 2027, root=self.root)]
        self.assertEqual(ids, ["o1-camp-a-2027", "o1-camp-a-2027-2"])

    def test_id_taken_in_programs_csv_is_avoided(self):
        row = {c: "" for c in st.PROGRAM_COLUMNS} | {"program_id": "o1-camp-a-2027", "org_id": "o1"}
        import csv
        with open(self.root / "data" / "programs.csv", "a", newline="") as f:
            csv.writer(f, lineterminator="\r\n").writerow([row[c] for c in st.PROGRAM_COLUMNS])
        self.assertEqual(self.add([self.good()])[0], 0)
        self.assertEqual(st.load("o1", 2027, root=self.root)[0]["program_id"], "o1-camp-a-2027-2")

    def test_resubmitting_an_id_already_staged_is_rejected(self):
        self.add([self.good()])
        code, _ = self.add([self.good(program_id="o1-camp-a-2027")])
        self.assertEqual(code, 1)
        self.assertEqual(len(st.load("o1", 2027, root=self.root)), 1)


class OrgFacts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        self.json = self.root / "org.json"
        self.addCleanup(self._tmp.cleanup)

    def add_org(self, obj, org="o1"):
        self.json.write_text(json.dumps(obj))
        return run(st.main, ["add-org", "--org", org, "--json", str(self.json)], self.root)

    def test_absent_file_is_empty(self):
        self.assertEqual(st.load_org_facts(2027, root=self.root), [])

    def test_blank_never_erases_and_one_row_per_org(self):
        self.assertEqual(self.add_org({"registration_opens": "2027-02-04", "website": "https://new"})[0], 0)
        self.assertEqual(self.add_org({"registration_notes": "lottery", "registration_opens": ""})[0], 0)
        rows = st.load_org_facts(2027, root=self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["org_id"], rows[0]["registration_opens"], rows[0]["registration_notes"], rows[0]["website"]),
                         ("o1", "2027-02-04", "lottery", "https://new"))

    def test_problems_exit_1_and_write_nothing(self):
        for obj, org in (({"bogus": "x"}, "o1"), ({"registration_opens": "Feb 4"}, "o1"),
                         ({"registration_opens": "2027-02-04", "registration_opens_early": "2027-03-01"}, "o1"),
                         ({"website": "https://x"}, "nope")):
            self.assertEqual(self.add_org(obj, org)[0], 1, obj)
        self.assertEqual(st.load_org_facts(2027, root=self.root), [])
