"""Seed script: demo academic master data + role users for local development.

Idempotent: safe to run repeatedly (``python -m backend.scripts.import_seed``).

In production this file is replaced by the read-only legacy adapter sync job
(ADR-009); nothing here writes to the university database.
"""
from __future__ import annotations

import sys

from sqlalchemy.orm import Session

DEMO_SUBJECT = {
    "name": "Physics-I",
    "code": "PHY101",
    "program": "B.Tech Mechanical",
    "semester": "Semester 1",
    "topics": [
        # (topic name, teaching hours, weight, importance 1..5, [subtopics])
        ("Units and Measurements", 6, 1.0, 3,
         ["SI Base Units", "Dimensional Analysis", "Errors and Precision"]),
        ("Vectors", 8, 1.0, 4,
         ["Vector Addition", "Dot Product", "Cross Product"]),
        ("Motion in a Straight Line", 10, 1.5, 4,
         ["Kinematic Equations", "Relative Velocity"]),
        ("Newton's Laws of Motion", 12, 2.0, 5,
         ["First Law", "Second Law", "Third Law", "Friction"]),
        ("Work, Energy and Power", 10, 1.5, 4,
         ["Work-Energy Theorem", "Conservation of Energy", "Power"]),
        ("Circular Motion", 8, 1.0, 3,
         ["Centripetal Acceleration", "Banked Roads"]),
        ("Gravitation", 8, 1.0, 3,
         ["Kepler's Laws", "Escape Velocity"]),
    ],
}

ROLE_USERS = [
    ("admin", "Administrator"),
    ("generator", "QuestionGenerator"),
    ("reviewer", "AcademicReviewer"),
    ("expert", "SubjectExpert"),
    ("exam", "ExamController"),
    ("viewer", "ReadOnly"),
]


def seed_academic(db: Session) -> dict:
    """Create demo subject/topics/users if absent. Returns counts."""
    from backend.core.security import hash_password
    from backend.models.entities import Subject, SubTopic, Topic, User

    created = {"subjects": 0, "topics": 0, "subtopics": 0, "users": 0}

    subj = db.query(Subject).filter_by(name=DEMO_SUBJECT["name"]).first()
    if subj is None:
        subj = Subject(name=DEMO_SUBJECT["name"], code=DEMO_SUBJECT["code"],
                       program=DEMO_SUBJECT["program"], semester=DEMO_SUBJECT["semester"])
        db.add(subj)
        db.flush()
        created["subjects"] += 1

    for tname, hours, weight, importance, subs in DEMO_SUBJECT["topics"]:
        topic = db.query(Topic).filter_by(subject_id=subj.id, name=tname).first()
        if topic is None:
            topic = Topic(subject_id=subj.id, name=tname, teaching_hours=hours,
                          weight=weight, importance=importance)
            db.add(topic)
            db.flush()
            created["topics"] += 1
        for sname in subs:
            if not db.query(SubTopic).filter_by(topic_id=topic.id, name=sname).first():
                db.add(SubTopic(topic_id=topic.id, name=sname))
                created["subtopics"] += 1

    default_pw = hash_password("pw-123456")
    for username, role in ROLE_USERS:
        if not db.query(User).filter_by(username=username).first():
            db.add(User(username=username, password_hash=default_pw, role=role))
            created["users"] += 1

    db.commit()
    return created


def main() -> int:
    from backend.database.session import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        counts = seed_academic(db)
        print("seeded:", counts)
        print("demo logins (password 'pw-123456'):", ", ".join(u for u, _ in ROLE_USERS))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
