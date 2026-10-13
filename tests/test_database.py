import tempfile
import unittest
from pathlib import Path

from database import Database


class DatabaseTests(unittest.TestCase):
    def test_database_seeds_and_crud(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            self.assertGreaterEqual(len(db.list_records("materials")), 1)
            record_id = db.insert_record(
                "extras", {"name": "Тест", "unit": "шт.", "price": 42, "active": 1}
            )
            records = {item["id"]: item for item in db.list_records("extras")}
            self.assertEqual(records[record_id]["price"], 42)
            db.update_record("extras", record_id, {"price": 84})
            records = {item["id"]: item for item in db.list_records("extras")}
            self.assertEqual(records[record_id]["price"], 84)
            db.delete_record("extras", record_id)
            self.assertNotIn(record_id, {item["id"] for item in db.list_records("extras")})
            db.close()


if __name__ == "__main__":
    unittest.main()
