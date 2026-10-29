"""Headless CLI driving the exact same core as the UI.

Examples:
    kumo-cli preview --project examples/sample_customer.yaml --server "Test Server 01"
    kumo-cli deploy  --project examples/sample_customer.yaml --server "Test Server 01" --dry-run
    kumo-cli dns     --project examples/sample_customer.yaml --server "Test Server 01"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .core import deploy as deploy_mod
from .core import dns as dns_mod
from .core import healthchecks as hc_mod
from .core.generators import generate_all
from .core.models import Customer, ServerProfile
from .core.report import build_report_html, save_report
from .core.ssh import SSHClient


def _load_server(project: Path, server: str) -> ServerProfile:
    data = yaml.safe_load(project.read_text(encoding="utf-8"))
    customer = Customer(**data)
    srv = customer.get_server(server)
    if not srv:
        raise SystemExit(f"Server '{server}' not found. Available: "
                         f"{[s.name for s in customer.servers]}")
    return srv


def _cmd_preview(args) -> int:
    srv = _load_server(Path(args.project), args.server)
    files = generate_all(srv)
    for name, content in files.items():
        print(f"\n===== {name} =====\n{content}")
    return 0


def _cmd_dns(args) -> int:
    srv = _load_server(Path(args.project), args.server)
    for c in dns_mod.validate_profile(srv):
        print(f"[{c.status:8}] {c.record_type:5} {c.name} -> {c.found or '(none)'}")
    return 0


def _cmd_dnsgen(args) -> int:
    from .core import dnsgen

    srv = _load_server(Path(args.project), args.server)
    records, dkim_keys = dnsgen.generate_records(srv)
    print(f"{'ACTION':7} {'TYPE':4} {'NAME':42} VALUE")
    for r in records:
        print(f"{r.action:7} {r.type:4} {r.name:42} {r.value[:70]}")
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "dns-records.csv").write_text(dnsgen.records_to_csv(records), encoding="utf-8")
        for domain, pem in dkim_keys.items():
            (out / f"dkim-{domain}.key").write_text(pem, encoding="utf-8")
        print(f"\nWrote dns-records.csv + {len(dkim_keys)} DKIM key(s) to {out.resolve()}")
    return 0


def _cmd_plan(args) -> int:
    from .core import ipplan

    if args.csv:
        ranges = ipplan.parse_input(Path(args.csv).read_text(encoding="utf-8"),
                                    default_domain=args.domain or "", preset=args.pattern)
    elif args.cidr:
        hp, up = ipplan.NAMING_PRESETS.get(args.pattern, ipplan.NAMING_PRESETS["s{n}"])
        ranges = [ipplan.IPRange(cidr=args.cidr, domain=args.domain or "",
                                 hostname_pattern=hp, username_pattern=up)]
    else:
        raise SystemExit("provide --csv <file> or --cidr <cidr>")

    for msg in ipplan.validate_plan(ranges):
        print("!  " + msg)

    pv = ipplan.preview(ranges)
    print(f"\nTotal usable IPs: {pv['total_ips']}")
    print(f"Per domain:       {pv['per_domain']}")
    print("Sample (first 8):")
    for e in pv["sample"]:
        print(f"   {e.ip:18} {e.hostname:32} {e.username:16} {e.domain}")
    if pv["duplicate_hostnames"]:
        print("DUPLICATE hostnames:", pv["duplicate_hostnames"])
    if pv["duplicate_usernames"]:
        print("DUPLICATE usernames:", pv["duplicate_usernames"])
    return 0


def _cmd_monitor(args) -> int:
    from .core import monitoring
    from .core.models import MonitoringHub, SSHTarget

    srv = _load_server(Path(args.project), args.server)

    print("===== prometheus file_sd =====")
    print(monitoring.prometheus_file_sd_json(srv))
    print("\n===== promtail (ship to Loki) =====")
    print(monitoring.promtail_config(srv, args.loki_url))

    if not (args.register or args.verify):
        return 0

    if not args.hub_host:
        raise SystemExit("--register/--verify need --hub-host (the monitoring box)")
    hub = MonitoringHub(
        ssh=SSHTarget(host=args.hub_host, username=args.hub_user,
                      use_password=bool(args.hub_password)),
        loki_push_url=args.loki_url,
        reload_method=args.reload,
    )
    ssh = SSHClient(hub.ssh.host, hub.ssh.port, hub.ssh.username,
                    password=args.hub_password or None)
    ssh.connect()
    try:
        if args.register:
            c = monitoring.register_server(srv, hub, ssh)
            print(f"\n[{'OK ' if c.ok else 'FAIL'}] {c.name} — {c.detail}")
        if args.verify:
            c = monitoring.verify_target(srv, hub, ssh)
            print(f"[{'OK ' if c.ok else 'FAIL'}] {c.name} — {c.detail}")
    finally:
        ssh.close()
    return 0


def _cmd_cpanel_login(args) -> int:
    """Store a cPanel API token in the OS keyring (per host+user)."""
    from .core import dnspublish as dp
    dp.store_token(args.host, args.user, args.token)
    print(f"Stored cPanel token for {args.user}@{args.host} in the keyring.")
    return 0


def _dns_creds(args):
    from .core import dnspublish as dp
    if args.creds:
        return dp.creds_from_file(args.creds)
    if args.cpanel_host and args.cpanel_user:
        creds = dp.creds_from_keyring(args.cpanel_host, args.cpanel_user)
        if not creds:
            raise SystemExit("No token in keyring — run: kumo-cli cpanel-login --host ... --user ... --token ...")
        return creds
    raise SystemExit("Provide --creds <file> OR --cpanel-host + --cpanel-user (with a stored token).")


def _cmd_dns_push(args) -> int:
    """Add / delete / update / verify a domain's DNS records in cPanel."""
    from .core import dnsgen
    from .core import dnspublish as dp

    srv = _load_server(Path(args.project), args.server)
    client = dp.CpanelClient(_dns_creds(args), insecure=args.insecure)
    domain = args.domain

    if args.delete_range:
        res = dp.delete_range(client, domain, args.delete_range.split(","),
                              confirm=args.confirm, backup_path=args.backup)
        print(res)
        return 0 if not res.get("failed") else 1

    records, _ = dnsgen.generate_records(srv)
    recs = [r for r in records
            if r.name == domain or r.name.rstrip(".").endswith("." + domain)]
    if not args.include_dkim:                       # DKIM public must match the deployed key
        recs = [r for r in recs if "_domainkey" not in r.name]

    if args.verify:
        res = dp.verify(client, domain, recs)
        print(f"{domain}: published={res['published']} missing={res['missing']} "
              f"conflicts={res['conflicts']}")
        for r in res["missing_records"][:20]:
            print(f"  MISSING {r.type:5} {r.name} -> {r.value}")
        return 0

    res = dp.publish(client, domain, recs, dry_run=not args.confirm)
    print(res)
    return 0 if not res.get("failed") else 1


def _cmd_dns_push_sheet(args) -> int:
    """Publish an Excel sheet of DNS records — every domain in the sheet, routed to
    the cPanel account. Matches the app's Download-Center layout (Type/Host/Value/TTL)."""
    from .core import dnspublish as dp

    client = dp.CpanelClient(_dns_creds(args), insecure=args.insecure)
    records = dp.parse_records_xlsx(args.xlsx)
    if not args.include_dkim:
        records = [r for r in records if "_domainkey" not in r.name]
    domains = {dp.domain_of(r.name) for r in dp._publishable(records)}
    routes = {d: client for d in domains}       # one account owns all sheet domains
    print(f"Loaded {len(records)} record(s) across {len(domains)} domain(s) from {args.xlsx}")
    print(f"Mode: {'PUBLISH' if args.confirm else 'DRY-RUN (no changes)'}\n")

    res = dp.publish_many(routes, records, dry_run=not args.confirm)
    total_add = sum(r["added"] if not r.get("dry_run") else r["todo"]
                    for r in res["results"].values())
    for domain, r in res["results"].items():
        done = r["added"] if not r.get("dry_run") else r["todo"]
        print(f"  {domain:30} {'to add' if r.get('dry_run') else 'added'} {done}, "
              f"skip {r['already']}, conflict {r['conflicts']}, failed {len(r['failed'])}")
    if res["unrouted"]:
        print("\n  ! no cPanel account for:", ", ".join(res["unrouted"]))
    print(f"\n{'Would add' if not args.confirm else 'Added'} {total_add} record(s). "
          f"{'Re-run with --confirm to apply.' if not args.confirm else 'Done.'}")
    return 0


def _cmd_import(args) -> int:
    """Reconstruct a ServerProfile from a LIVE server and add it to a customer
    project (created if missing). Writes YAML directly (passwords stripped) so it
    scales to thousands of IPs without slow keyring writes."""
    from .core.importer import import_profile
    from .services.project_store import _yaml_dump

    ssh = SSHClient(args.host, args.ssh_port, args.username, password=args.password)
    ssh.connect()
    try:
        print(f"Reading live config from {args.host} …")
        profile = import_profile(ssh, args.name, args.host,
                                 ssh_port=args.ssh_port, username=args.username)
    finally:
        ssh.close()
    print(f"Imported '{profile.name}': {len(profile.ips)} IPs, "
          f"{len(profile.unique_domains())} domains, ports {profile.effective_ports()}")

    projects = Path.home() / ".kumo-automation" / "customers"
    projects.mkdir(parents=True, exist_ok=True)
    path = projects / f"{args.add_to}.yaml"
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cust = Customer(**data)
    else:
        cust = Customer(name=args.add_to)
    cust.servers = [s for s in cust.servers if s.name != profile.name] + [profile]

    out = cust.model_dump(mode="json")
    for srv in out["servers"]:                          # keep passwords out of the file
        srv["shared_password"] = None
        for ip in srv["ips"]:
            ip["password"] = None
        srv.get("ssh", {}).pop("password", None)
    path.write_text(_yaml_dump(out), encoding="utf-8")
    print(f"Saved -> {path}  (project '{args.add_to}' now has {len(cust.servers)} server(s))")
    return 0


def _cmd_deploy(args) -> int:
    srv = _load_server(Path(args.project), args.server)
    ssh = SSHClient(srv.ssh.host, srv.ssh.port, srv.ssh.username,
                    key_path=srv.ssh.key_path,
                    password=(args.password if srv.ssh.use_password else None))
    ssh.connect()
    try:
        mode = "replace" if getattr(args, "replace", False) else "merge"
        result = deploy_mod.deploy(srv, ssh, dry_run=args.dry_run, mode=mode, log=print)
        dns_checks = dns_mod.validate_profile(srv)
        health = [] if args.dry_run else hc_mod.run_health_checks(srv, ssh)
    finally:
        ssh.close()

    if args.report:
        html = build_report_html(srv, result, dns_checks, health)
        path = save_report(html, Path(args.report), srv.name)
        print(f"\nReport: {path}")
    return 0 if result.success else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kumo-cli", description="KumoMTA Automation CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("preview", "dns", "dns-gen", "deploy"):
        sp = sub.add_parser(name)
        sp.add_argument("--project", required=True)
        sp.add_argument("--server", required=True)
        if name == "dns-gen":
            sp.add_argument("--out", default=None, help="directory to write dns-records.csv + DKIM keys")
        if name == "deploy":
            sp.add_argument("--dry-run", action="store_true")
            sp.add_argument("--replace", action="store_true",
                            help="overwrite the whole config (default: merge — add IPs without dropping existing)")
            sp.add_argument("--password", default=None)
            sp.add_argument("--report", default=None, help="directory to write an HTML report")

    # `monitor` — file_sd/promtail preview, optional hub register/verify
    mo = sub.add_parser("monitor", help="preview monitoring config; optionally register with the hub")
    mo.add_argument("--project", required=True)
    mo.add_argument("--server", required=True)
    mo.add_argument("--loki-url", default="http://127.0.0.1:3100/loki/api/v1/push")
    mo.add_argument("--register", action="store_true", help="write file_sd target on the hub + reload")
    mo.add_argument("--verify", action="store_true", help="query the hub Prometheus for target up")
    mo.add_argument("--hub-host", default=None, help="monitoring box (la412)")
    mo.add_argument("--hub-user", default="root")
    mo.add_argument("--hub-password", default=None)
    mo.add_argument("--reload", default="http", choices=["http", "docker", "sighup"])

    # `cpanel-login` stores a cPanel API token in the keyring
    cl = sub.add_parser("cpanel-login", help="store a cPanel API token in the keyring")
    cl.add_argument("--host", required=True, help="cPanel host, e.g. server.host.com:2083")
    cl.add_argument("--user", required=True)
    cl.add_argument("--token", required=True)

    # `dns-push` adds / deletes / verifies a domain's DNS records in cPanel
    dp_ = sub.add_parser("dns-push", help="publish/delete/verify DNS records in cPanel")
    dp_.add_argument("--project", required=True)
    dp_.add_argument("--server", required=True)
    dp_.add_argument("--domain", required=True, help="zone to act on")
    dp_.add_argument("--verify", action="store_true", help="diff expected vs live (no changes)")
    dp_.add_argument("--delete-range", default=None, help="comma-separated CIDRs to delete")
    dp_.add_argument("--confirm", action="store_true", help="apply changes (without this: dry-run)")
    dp_.add_argument("--backup", default=None, help="JSON backup file before delete")
    dp_.add_argument("--include-dkim", action="store_true", help="also push DKIM (needs matching key)")
    dp_.add_argument("--creds", default=None, help="cpanel_creds.txt file (HOST/USER/TOKEN)")
    dp_.add_argument("--cpanel-host", default=None)
    dp_.add_argument("--cpanel-user", default=None)
    dp_.add_argument("--insecure", action="store_true", help="skip TLS verify (self-signed cPanel)")

    # `dns-push-sheet` publishes an Excel of records (every domain in the sheet)
    ds = sub.add_parser("dns-push-sheet", help="publish a DNS Excel sheet to cPanel (all domains)")
    ds.add_argument("--xlsx", required=True, help="DNS records .xlsx (Download-Center layout)")
    ds.add_argument("--confirm", action="store_true", help="apply (without this: dry-run)")
    ds.add_argument("--include-dkim", action="store_true")
    ds.add_argument("--creds", default=None, help="cpanel_creds.txt file")
    ds.add_argument("--cpanel-host", default=None)
    ds.add_argument("--cpanel-user", default=None)
    ds.add_argument("--insecure", action="store_true")

    # `import` reconstructs a ServerProfile from a live server
    im = sub.add_parser("import", help="import a live server's config into a customer project")
    im.add_argument("--host", required=True, help="server IP/hostname to read config from")
    im.add_argument("--name", required=True, help="name to give the imported server")
    im.add_argument("--add-to", required=True, help="customer project to add it to (created if missing)")
    im.add_argument("--password", default=None, help="SSH password (or use a key/agent)")
    im.add_argument("--ssh-port", type=int, default=22)
    im.add_argument("--username", default="root")

    # `plan` takes a CIDR or CSV instead of a saved project
    pl = sub.add_parser("plan", help="expand CIDR/CSV -> IPs, hostnames, usernames (preview)")
    pl.add_argument("--csv", default=None, help="CSV file of IPs or CIDRs")
    pl.add_argument("--cidr", default=None, help="single CIDR, e.g. 203.0.113.0/24")
    pl.add_argument("--domain", default=None, help="domain to assign (default for rows without one)")
    pl.add_argument("--pattern", default="s{n}", help="naming preset: s{n}|smtp{n}|mail{n}|mx{n}|word")

    args = p.parse_args(argv)
    handlers = {
        "preview": _cmd_preview,
        "dns": _cmd_dns,
        "dns-gen": _cmd_dnsgen,
        "deploy": _cmd_deploy,
        "import": _cmd_import,
        "cpanel-login": _cmd_cpanel_login,
        "dns-push": _cmd_dns_push,
        "dns-push-sheet": _cmd_dns_push_sheet,
        "plan": _cmd_plan,
        "monitor": _cmd_monitor,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
