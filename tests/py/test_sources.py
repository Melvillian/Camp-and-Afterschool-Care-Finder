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
        self.assertEqual(s.guess_kind("https://www.summerbtvparks.com/"), "rec1")
        self.assertEqual(s.guess_kind(""), "manual")
        self.assertEqual(s.guess_kind("https://x.org/camps"), "html")

    def test_names_ai_crawler(self):
        self.assertTrue(s.names_ai_crawler("User-agent: GPTBot\nDisallow: /\n"))
        self.assertTrue(s.names_ai_crawler("user-agent:  claudebot\nDisallow: /"))
        self.assertTrue(s.names_ai_crawler("User-agent: GPTBot # note\nDisallow: /"))
        self.assertFalse(s.names_ai_crawler("# User-agent: GPTBot\nUser-agent: *"))
        self.assertFalse(s.names_ai_crawler("User-agent: *\nCrawl-delay: 10\n"))
        self.assertFalse(s.names_ai_crawler(""))
        for name in ("Claude-Web", "Claude-User", "ChatGPT-User", "OAI-SearchBot", "Bytespider", "Amazonbot",
                     "Applebot-Extended", "meta-externalagent"):
            self.assertTrue(s.names_ai_crawler(f"User-agent: {name}\nDisallow: /\n"), name)

    def test_blanket_disallow_counts_as_opted_out(self):
        self.assertTrue(s.opts_out("User-agent: *\nDisallow: /\n"))
        self.assertTrue(s.opts_out("User-agent: Googlebot\nUser-agent: *\nDisallow: / # all\n"))
        self.assertTrue(s.opts_out("User-agent: GPTBot\nAllow: /\n"))
        self.assertFalse(s.opts_out("User-agent: *\nDisallow:\n"))
        self.assertFalse(s.opts_out("User-agent: *\nDisallow: /private\n"))
        self.assertFalse(s.opts_out("User-agent: Googlebot\nDisallow: /\n\nUser-agent: *\nDisallow: /tmp\n"))
        self.assertFalse(s.opts_out("User-agent: *\nDisallow: /tmp\nUser-agent: Googlebot\nDisallow: /\n"))


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


class Robots(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.path = Path(tempfile.mkdtemp()) / "s.csv"

    def rows(self, spec):
        base = dict.fromkeys(s.COLUMNS, "")
        return [{**base, "org_id": f"o{i}", "kind": "html", "status_2027": "not_posted",
                 "listing_url": f"https://{h}/", "robots_ai_optout": v} for i, (h, v) in enumerate(spec)]

    def test_missing_robots_is_false_and_other_failures_are_blank(self):
        import urllib.error
        s.save(self.rows([("gone.org", ""), ("down.org", ""), ("bots.org", ""), ("also.org", "")]), self.path)

        def fake_fetch(host):
            if host == "gone.org":
                return ""
            if host == "down.org":
                raise OSError("timeout")
            return "User-agent: GPTBot\nDisallow: /"
        s.robots(self.path, fetch=fake_fetch, sleep=lambda _: None)
        got = {r["listing_url"]: r["robots_ai_optout"] for r in s.load(self.path)}
        self.assertEqual(got, {"https://gone.org/": "FALSE", "https://down.org/": "",
                               "https://bots.org/": "TRUE", "https://also.org/": "TRUE"})

    def test_http_404_and_410_mean_no_robots_file_but_500_is_a_failure(self):
        import urllib.error
        from unittest import mock
        for code, expect in ((404, ""), (410, ""), (500, None)):
            err = urllib.error.HTTPError("https://x/robots.txt", code, "x", {}, None)
            with mock.patch.object(s.urllib.request, "urlopen", side_effect=err):
                if expect is None:
                    with self.assertRaises(urllib.error.HTTPError):
                        s.fetch_robots("x")
                else:
                    self.assertEqual(s.fetch_robots("x"), expect)

    def test_rerun_skips_rows_already_answered(self):
        s.save(self.rows([("a.org", "FALSE"), ("b.org", "")]), self.path)
        asked = []
        s.robots(self.path, fetch=lambda h: asked.append(h) or "", sleep=lambda _: None)
        self.assertEqual(asked, ["b.org"])
        self.assertEqual([r["robots_ai_optout"] for r in s.load(self.path)], ["FALSE", "FALSE"])

    def test_recheck_asks_every_host_again(self):
        s.save(self.rows([("a.org", "FALSE"), ("b.org", ""), ("c.org", "TRUE"), ("a.org", "FALSE")]), self.path)
        asked = []

        def fetch(host):
            asked.append(host)
            if host == "c.org":
                raise OSError("timeout")
            return "User-agent: *\nDisallow: /" if host == "a.org" else ""
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()):
            s.robots(self.path, fetch=fetch, sleep=lambda _: None, recheck=True)
        self.assertEqual(asked, ["a.org", "b.org", "c.org"])
        self.assertEqual([r["robots_ai_optout"] for r in s.load(self.path)], ["TRUE", "FALSE", "", "TRUE"])


if __name__ == "__main__":
    unittest.main()
