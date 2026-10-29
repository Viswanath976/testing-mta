"""KumoMTA Automation — REST API for the web frontend.

Wraps the existing core (generators, DNS gen/publish, DB store) so the web UI at
localhost:3000 drives the same logic the desktop app did. CORS is open to the
dev frontend origins.
"""
from __future__ import annotations

import os
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..core import dnsgen
from ..core import dnspublish as dp
from ..core.generators import generate_all
from ..core.models import Customer
from ..db import store
from . import backup as backup_mod

app = FastAPI(title="KumoMTA Automation API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("FRONTEND_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"], allow_headers=["*"], allow_credentials=True,
)


@app.on_event("startup")
def _startup() -> None:
    store.setup()


def _find(customer: str, server: str):
    c = store.load_customer(customer)
    if not c:
        raise HTTPException(404, f"customer '{customer}' not found")
    srv = c.get_server(server)
    if not srv:
        raise HTTPException(404, f"server '{server}' not found in '{customer}'")
    return srv


# --- health ---------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"ok": True, "db": os.environ.get("DATABASE_URL", "sqlite (default)").split("@")[-1]}


# --- customers ------------------------------------------------------------- #
@app.get("/customers")
def list_customers():
    names = store.list_customer_names()
    out = []
    for n in names:
        c = store.load_customer(n)
        out.append({
            "name": n,
            "servers": [{"name": s.name, "environment": s.environment.value,
                         "host": s.ssh.host, "ips": len(s.ips),
                         "domains": len(s.unique_domains())} for s in c.servers],
            "total_ips": sum(len(s.ips) for s in c.servers),
        })
    return {"customers": out}


@app.get("/customers/{name}")
def get_customer(name: str):
    c = store.load_customer(name)
    if not c:
        raise HTTPException(404)
    return c.model_dump(mode="json")


@app.post("/customers")
def save_customer(customer: Customer):
    store.save_customer(customer)
    return {"ok": True, "name": customer.name, "servers": len(customer.servers)}


@app.delete("/customers/{name}")
def delete_customer(name: str):
    return {"deleted": store.delete_customer(name)}


# --- config preview + DNS records ------------------------------------------ #
@app.get("/customers/{customer}/servers/{server}/preview")
def preview(customer: str, server: str):
    return generate_all(_find(customer, server))


@app.get("/customers/{customer}/servers/{server}/dns")
def dns_records(customer: str, server: str):
    records, _ = dnsgen.generate_records(_find(customer, server))
    return {"records": [r.model_dump() for r in records]}


# --- cPanel accounts + DNS publish ----------------------------------------- #
class CpanelAccountIn(BaseModel):
    host: str
    user: str
    domains: list[str]
    token: str | None = None


@app.get("/cpanel-accounts")
def list_cpanel():
    return {"accounts": [a.model_dump() for a in store.list_cpanel_accounts()]}


@app.post("/cpanel-accounts")
def save_cpanel(acc: CpanelAccountIn):
    store.save_cpanel_account(acc.host, acc.user, acc.domains, acc.token)
    return {"ok": True}


@app.post("/dns/publish-sheet")
async def publish_sheet(file: UploadFile = File(...), cpanel_host: str = Form(...),
                        cpanel_user: str = Form(...), confirm: bool = Form(False),
                        include_dkim: bool = Form(False), insecure: bool = Form(False)):
    token = store.cpanel_token(cpanel_host, cpanel_user)
    if not token:
        raise HTTPException(400, "no stored token for that cPanel account")
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        path = tmp.name
    client = dp.CpanelClient(dp.CpanelCreds(cpanel_host, cpanel_user, token), insecure=insecure)
    records = dp.parse_records_xlsx(path)
    if not include_dkim:
        records = [r for r in records if "_domainkey" not in r.name]
    domains = {dp.domain_of(r.name) for r in dp._publishable(records)}
    routes = {d: client for d in domains}
    res = dp.publish_many(routes, records, dry_run=not confirm, log=lambda *_: None)
    summary = {d: {k: r.get(k) for k in ("added", "todo", "already", "conflicts")}
               for d, r in res["results"].items()}
    return {"dry_run": not confirm, "results": summary, "unrouted": res["unrouted"]}


# --- backup / restore ------------------------------------------------------ #
class RestoreIn(BaseModel):
    backup_path: str


@app.post("/backup")
def backup(stamp: str):
    out = os.environ.get("BACKUP_DIR", "./backups")
    return {"path": backup_mod.backup_db(out, stamp)}


@app.post("/restore")
def restore(body: RestoreIn):
    backup_mod.restore_db(body.backup_path)
    return {"ok": True}
