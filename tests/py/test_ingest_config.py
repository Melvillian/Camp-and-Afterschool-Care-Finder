import datetime as dt, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ingest_config as ic

MSG = ('REMINDER: the volunteer Google Sheet is still editable by anyone with the link. '
       'Restrict editing, set up the service-account key, then set "public_edit" to false '
       'in data/ingest_config.json.')
CFG = {"public_edit": True, "secure_by": "2026-12-20"}

class Reminder(unittest.TestCase):
    def test_quiet_before_the_date(self):
        self.assertIsNone(ic.reminder_due(CFG, dt.date(2026, 12, 19)))
    def test_due_on_and_after_the_date(self):
        self.assertEqual(ic.reminder_due(CFG, dt.date(2026, 12, 20)), MSG)
        self.assertEqual(ic.reminder_due(CFG, dt.date(2027, 3, 1)), MSG)
    def test_quiet_once_secured(self):
        self.assertIsNone(ic.reminder_due({**CFG, "public_edit": False}, dt.date(2027, 3, 1)))
    def test_config_file_has_the_expected_keys(self):
        cfg = ic.load()
        self.assertEqual(cfg["season"], 2027)
        self.assertEqual(cfg["sheet_id"], "11fUBvE0_Vwm0ktqbYo-yE3rxMrpmpFQUYXMhSLoahHc")
        self.assertEqual(cfg["secure_by"], "2026-12-20")

class SheetIsSecured(unittest.TestCase):
    def test_the_volunteer_sheet_is_secured_by_the_deadline(self):
        """Fails loudly from secure_by onward until public_edit is false."""
        due = ic.reminder_due(ic.load(), dt.date.today())
        self.assertIsNone(due, "\n\n" + "=" * 78 + "\n" + str(due) + "\n" + "=" * 78 + "\n")

if __name__ == "__main__":
    unittest.main()
