import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
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

    def test_robots_flag_accepts_blank_true_false(self):
        for v in ("", "TRUE", "FALSE"):
            self.assertEqual(s.check([self.row(robots_ai_optout=v)], {"o1"}), [])
        self.assertEqual(len(s.check([self.row(robots_ai_optout="yes")], {"o1"})), 1)

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


if __name__ == "__main__":
    unittest.main()
