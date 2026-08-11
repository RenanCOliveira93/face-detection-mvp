import tempfile
import unittest
from pathlib import Path

from database import FaceDatabase
from sync_service import flush_outbox


class SyncServiceTests(unittest.TestCase):
    def test_failure_remains_pending_and_reconnect_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = FaceDatabase(str(Path(tmp) / "sync.db"))
            school = db.create_school("A", "a")
            db.add_face("student", "Aluno", "", school_id=school["id"])
            db.create_device(school["id"], "gate", "Gate", "secret")
            db.record_device_presence(school["id"], "gate", "one", "student")

            def unavailable(*args):
                raise ConnectionError("central offline")

            self.assertEqual(flush_outbox(db, unavailable), {"sent": 0, "failed": 1})
            self.assertEqual(len(db.pending_outbox()), 1)
            central = {}

            def sink(key, aggregate, payload):
                central.setdefault(key, (aggregate, payload))

            self.assertEqual(flush_outbox(db, sink), {"sent": 1, "failed": 0})
            self.assertEqual(flush_outbox(db, sink), {"sent": 0, "failed": 0})
            self.assertEqual(len(central), 1)


if __name__ == "__main__":
    unittest.main()
