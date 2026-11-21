import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_data_js as b

ORG = {"org_id": "o1", "org_name": "Org One"}
def prog(pid, ptype="camp"):
    return {"program_id": pid, "org_id": "o1", "program_name": pid, "program_type": ptype}

class StableUid(unittest.TestCase):
    def test_uid_is_program_id(self):
        self.assertEqual(b.build_program_obj(prog("echo-animal-superpowers-2026"), ORG, 7)["uid"],
                         "echo-animal-superpowers-2026")

    def test_uid_does_not_depend_on_row_position(self):
        first = b.build_program_obj(prog("a-camp"), ORG, 1)
        later = b.build_program_obj(prog("a-camp"), ORG, 99)
        self.assertEqual(first["uid"], later["uid"])
        self.assertEqual((first["id"], later["id"]), (1, 99))

class OrgRegistration(unittest.TestCase):
    def test_org_object_carries_registration_fields(self):
        o = b.build_org_obj({"org_id": "o1", "org_name": "Org", "registration_opens": "2027-02-04",
                             "registration_opens_early": "2027-01-28", "registration_notes": "Residents first"})
        self.assertEqual((o["registrationOpens"], o["registrationOpensEarly"], o["registrationNotes"]),
                         ("2027-02-04", "2027-01-28", "Residents first"))

    def test_blank_fields_are_empty_strings(self):
        o = b.build_org_obj({"org_id": "o1", "org_name": "Org"})
        self.assertEqual((o["registrationOpensEarly"], o["registrationNotes"]), ("", ""))


if __name__ == "__main__":
    unittest.main()
