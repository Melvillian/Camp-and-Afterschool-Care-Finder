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

if __name__ == "__main__":
    unittest.main()
