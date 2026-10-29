"""Migrate the desktop app's YAML projects (+ keyring secrets) into the database.

Run once when moving to the web app:
    python -m kumo_automation.db.migrate
It reads every customer YAML under ~/.kumo-automation/customers, hydrates any
keyring passwords, and writes them into the DB (DATABASE_URL). Idempotent — a
re-run upserts each customer.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..services.project_store import ProjectStore
from . import store as db_store


def migrate_from_yaml(customers_dir: str | Path | None = None) -> int:
    customers_dir = Path(customers_dir or (Path.home() / ".kumo-automation" / "customers"))
    db_store.setup()
    ps = ProjectStore(customers_dir)
    names = ps.list_customers()
    if not names:
        print(f"No customer projects found in {customers_dir}")
        return 0
    total = 0
    for name in names:
        cust = ps.load(name)
        db_store.save_customer(cust)
        ips = sum(len(s.ips) for s in cust.servers)
        total += ips
        print(f"  migrated {name}: {len(cust.servers)} server(s), {ips} IPs")
    print(f"Done. {len(names)} customer(s), {total} IPs -> database.")
    return len(names)


if __name__ == "__main__":
    sys.exit(0 if migrate_from_yaml(*sys.argv[1:2]) >= 0 else 1)
