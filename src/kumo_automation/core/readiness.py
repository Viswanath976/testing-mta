"""Production-readiness score from a server's workflow progress.

Pure + testable. Categories map to the lifecycle steps; the score is the
percentage of categories complete.
"""
from __future__ import annotations

from .models import ServerProfile

# category -> the progress key(s) that satisfy it
CATEGORIES = [
    ("Prerequisites", ["setup"]),
    ("Configuration", ["previewed"]),
    ("Validation", ["validated"]),
    ("DNS", ["dns"]),
    ("Dry Run", ["dry_run"]),
    ("Health", ["health"]),
    ("SMTP", ["smtp"]),
    ("Monitoring", ["monitoring"]),
]


def readiness(profile: ServerProfile) -> dict:
    """Return {score:int, ready:bool, items:[(category, done_bool)]}."""
    items = []
    done = 0
    for name, keys in CATEGORIES:
        ok = all(profile.progress.get(k) for k in keys)
        items.append((name, ok))
        done += 1 if ok else 0
    score = round(done * 100 / len(CATEGORIES)) if CATEGORIES else 0
    return {"score": score, "ready": score == 100, "items": items}
