import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import validate_data as v


class OrgRegistrationChecks(unittest.TestCase):
    def test_good_and_blank_rows_pass(self):
        self.assertEqual(v.check_org_registration({"registration_opens": "2027-02-04", "registration_opens_early": "2027-01-28"}, "x"), [])
        self.assertEqual(v.check_org_registration({}, "x"), [])

    def test_bad_date_format(self):
        self.assertEqual(len(v.check_org_registration({"registration_opens": "2/4/2027"}, "x")), 1)

    def test_early_after_opens(self):
        self.assertEqual(len(v.check_org_registration({"registration_opens": "2027-02-04", "registration_opens_early": "2027-02-11"}, "x")), 1)


if __name__ == "__main__":
    unittest.main()
