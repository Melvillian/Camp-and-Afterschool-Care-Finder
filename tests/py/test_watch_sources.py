import contextlib, io, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import sources
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

    def test_merged_with_blank_hash_just_records_it(self):
        row, msg = w.assess(ROW(status_2027="merged"), R(b"<p>2027</p>"), 2027, "2027-02-01")
        self.assertEqual((row["status_2027"], msg), ("merged", None))
        self.assertEqual(len(row["last_hash"]), 16)

    def test_failures_change_nothing_but_notes(self):
        for res in (R(b"", ok=False, status=404, error="HTTP 404"),
                    R(b"<p>2027</p>", final_url="https://parked.example/")):
            row, msg = w.assess(ROW(last_hash="abc"), res, 2027, "2026-12-15")
            self.assertEqual((row["status_2027"], row["last_hash"], row["last_checked"]), ("not_posted", "abc", ""))
            self.assertTrue(msg.startswith("NEEDS A PERSON:"))
            self.assertIn("2026-12-15", row["notes"])

    def test_www_and_case_are_not_a_different_host(self):
        row, msg = w.assess(ROW(), R(b"<p>2027</p>", final_url="https://WWW.X.org/camps/"), 2027, "2026-12-15")
        self.assertEqual((row["status_2027"], msg), ("posted", "POSTED: o1"))

    def test_failure_note_replaces_earlier_one_and_keeps_others(self):
        row = ROW(notes="call in Jan; fetch failed 2026-11-01: HTTP 404")
        row, _ = w.assess(row, R(b"", ok=False, error="timed out"), 2027, "2026-12-15")
        self.assertEqual(row["notes"], "call in Jan; fetch failed 2026-12-15: timed out")

    def test_success_keeps_failure_note(self):
        row, _ = w.assess(ROW(notes="fetch failed 2026-11-01: HTTP 404"), R(b"<p>x</p>"), 2027, "2026-12-15")
        self.assertEqual(row["notes"], "fetch failed 2026-11-01: HTTP 404")

    def test_assess_does_not_mutate_its_input(self):
        original = ROW()
        w.assess(original, R(b"<p>2027</p>"), 2027, "2026-12-15")
        self.assertEqual(original, ROW())

    def test_pdf_uses_bytes_and_url(self):
        row, msg = w.assess(ROW(kind="pdf", listing_url="https://x.org/camp_guide_2027.pdf"),
                            R(b"%PDF-1.7 ...", content_type="application/pdf", final_url="https://x.org/camp_guide_2027.pdf"), 2027, "2026-12-15")
        self.assertEqual(row["status_2027"], "posted")
        row, _ = w.assess(ROW(kind="pdf", listing_url="https://x.org/guide.pdf"),
                          R(b"%PDF-1.7 Summer 2027", content_type="application/pdf", final_url="https://x.org/guide.pdf"), 2027, "2026-12-15")
        self.assertEqual(row["status_2027"], "posted")
        self.assertEqual(row["last_hash"], w.content_hash(b"%PDF-1.7 Summer 2027"))


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sources.csv"
        self.sleeps, self.fetched, self.saves = [], [], []

    def write(self, rows):
        sources.save([{**dict.fromkeys(sources.COLUMNS, ""), "kind": "html", "status_2027": "not_posted", **r} for r in rows], self.path)

    def fetch(self, url):
        self.fetched.append(url)
        if "boom" in url:
            raise RuntimeError("kaboom")
        if "gone" in url:
            return R(b"", ok=False, status=404, error="HTTP 404", final_url=url)
        return R(b"<p>Summer 2027</p>", final_url=url)

    def run_cli(self, *args):
        out = io.StringIO()
        real_save = sources.save
        def spy(rows, path=self.path):
            self.saves.append(len(rows))
            real_save(rows, path)
        w.sources.save = spy
        try:
            with contextlib.redirect_stdout(out):
                code = w.main(["watch_sources.py", *args], fetch=self.fetch, sleep=self.sleeps.append,
                              today="2026-12-15", path=self.path)
        finally:
            w.sources.save = real_save
        return code, out.getvalue()

    def test_skips_blank_url_and_other_kinds_and_counts_them(self):
        self.write([{"org_id": "a", "listing_url": ""}, {"org_id": "b", "listing_url": "https://b.org/", "kind": "rec1"},
                    {"org_id": "c", "listing_url": "https://c.org/"}])
        code, out = self.run_cli()
        self.assertEqual((code, self.fetched), (0, ["https://c.org/"]))
        self.assertIn("POSTED: c", out)
        self.assertIn("2 skipped", out)
        self.assertEqual(sources.load(self.path)[2]["status_2027"], "posted")

    def test_recently_checked_skipped_unless_force(self):
        self.write([{"org_id": "a", "listing_url": "https://a.org/", "last_checked": "2026-12-10"},
                    {"org_id": "b", "listing_url": "https://b.org/", "last_checked": "2026-12-01"}])
        self.run_cli()
        self.assertEqual(self.fetched, ["https://b.org/"])
        self.fetched.clear()
        self.run_cli("--force")
        self.assertEqual(self.fetched, ["https://a.org/", "https://b.org/"])

    def test_dry_run_writes_nothing(self):
        self.write([{"org_id": "a", "listing_url": "https://a.org/"}])
        before = self.path.read_bytes()
        _, out = self.run_cli("--dry-run")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.saves, [])
        self.assertIn("POSTED: a", out)

    def test_org_and_limit(self):
        self.write([{"org_id": f"o{i}", "listing_url": f"https://o{i}.org/"} for i in range(5)])
        self.run_cli("--org", "o3")
        self.assertEqual(self.fetched, ["https://o3.org/"])
        self.fetched.clear()
        self.run_cli("--limit", "2")
        self.assertEqual(self.fetched, ["https://o0.org/", "https://o1.org/"])

    def test_waits_between_requests_only(self):
        self.write([{"org_id": f"o{i}", "listing_url": f"https://o{i}.org/"} for i in range(3)])
        self.run_cli()
        self.assertEqual(self.sleeps, [2, 2])

    def test_saves_every_ten_rows_and_at_the_end(self):
        self.write([{"org_id": f"o{i}", "listing_url": f"https://o{i}.org/"} for i in range(23)])
        self.run_cli()
        self.assertEqual(self.saves, [23, 23, 23])

    def test_failures_do_not_stop_the_run_or_lose_rows(self):
        self.write([{"org_id": "a", "listing_url": "https://a.org/boom"}, {"org_id": "b", "listing_url": "https://b.org/gone"},
                    {"org_id": "c", "listing_url": "https://c.org/"}])
        code, out = self.run_cli()
        rows = sources.load(self.path)
        self.assertEqual(len(rows), 3)
        self.assertEqual(out.count("NEEDS A PERSON"), 2)
        self.assertIn("kaboom", rows[0]["notes"])
        self.assertIn("HTTP 404", rows[1]["notes"])
        self.assertEqual([r["status_2027"] for r in rows], ["not_posted", "not_posted", "posted"])
        self.assertEqual(rows[0]["last_checked"], "")


if __name__ == "__main__":
    unittest.main()
