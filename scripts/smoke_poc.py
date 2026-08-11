"""Deterministic no-hardware POC smoke test."""
import tempfile
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import FaceDatabase
from sync_service import flush_outbox

with tempfile.TemporaryDirectory() as tmp:
    db = FaceDatabase(str(Path(tmp) / "smoke.db"))
    a = db.create_school("Escola A", "a", "school-a", "America/Sao_Paulo")
    b = db.create_school("Escola B", "b", "school-b")
    admin = db.add_school_member(a["id"], "Admin", "admin@example.test", "school_admin", "admin-key")
    professor = db.add_school_member(a["id"], "Prof", "prof@example.test", "professor", "prof-key")
    assert db.authenticate_principal("admin-key")["role"] == "school_admin"
    classroom = db.create_classroom(a["id"], "5 A", "2026")
    db.add_face("a1", "Aluno", "", school_id=a["id"])
    db.add_face("b1", "Outro", "", school_id=b["id"])
    db.enroll_student(a["id"], classroom, "a1")
    db.add_dietary_restriction(a["id"], "a1", "Amendoim", "alta")
    db.add_teacher_note(a["id"], professor["id"], "Participou", face_id="a1")
    db.create_device(a["id"], "gate", "Portaria", "secret")
    first = db.record_device_presence(a["id"], "gate", "event-1", "a1")
    duplicate = db.record_device_presence(a["id"], "gate", "event-1", "a1")
    assert duplicate[2] and duplicate[0] == first[0]
    try:
        db.enroll_student(a["id"], classroom, "b1")
        raise AssertionError("cross-school enrollment accepted")
    except ValueError:
        pass
    dashboard = db.get_school_dashboard(a["id"])
    assert dashboard["students"] == 1 and dashboard["classrooms"][0]["student_count"] == 1
    db.add_kitchen_recipient(a["id"], "Cozinha", "mock-number")
    assert not db.prepare_kitchen_dispatch(a["id"])["duplicate"]
    assert flush_outbox(db, lambda *_: (_ for _ in ()).throw(ConnectionError("offline")))["failed"] == 1
    central = {}
    assert flush_outbox(db, lambda key, kind, payload: central.setdefault(key, payload))["sent"] == 1
    assert len(central) == 1
print("SMOKE_OK: local, isolation, Control iD, dashboard, kitchen and outbox")
