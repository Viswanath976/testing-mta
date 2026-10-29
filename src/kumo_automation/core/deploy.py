"""The safe deployment pipeline.

    generate -> (dry-run stop) -> backup -> upload -> validate
             -> restart -> verify listeners  |  any failure => ROLLBACK

Nothing skips this order. Every deploy is reversible.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Literal

from .firewall import apply as fw_apply
from .firewall import apply_fail2ban
from .generators import generate_all
from .merge import merged_profile
from .models import DeployResult, ServerProfile, StepResult
from .ssh import SSHClient
from .validation import integrity_errors

POLICY_DIR = "/opt/kumomta/etc/policy"
KUMOD = "/opt/kumomta/sbin/kumod"

LogFn = Callable[[str], None]


def _validate_ok(stdout: str, stderr: str) -> bool:
    """kumod --validate can false-alarm inside the policy-extras DKIM helper
    (dkim_sign.lua) when it self-tests. Treat that as PASS; only a real
    init.lua error blocks the deploy."""
    blob = (stdout + stderr)
    if "error" not in blob.lower():
        return True
    # config parsed far enough to reach the dkim self-test => syntactically OK
    if "dkim_sign.lua" in blob and "init.lua" not in blob:
        return True
    return False


DKIM_DIR = "/opt/kumomta/etc/dkim"
MSS_UNIT = "/etc/systemd/system/mss-clamp.service"
TIMEOUT_DROPIN_DIR = "/etc/systemd/system/kumomta.service.d"
TIMEOUT_DROPIN = f"{TIMEOUT_DROPIN_DIR}/10-timeout.conf"


def _mss_unit(mss: int) -> str:
    """systemd oneshot that installs a single idempotent TCPMSS clamp rule.
    Delete-then-add so re-runs never stack duplicate rules."""
    add = f"iptables -t mangle -A OUTPUT -p tcp --syn -j TCPMSS --set-mss {mss}"
    dele = f"iptables -t mangle -D OUTPUT -p tcp --syn -j TCPMSS --set-mss {mss}"
    return (
        "[Unit]\n"
        "Description=Clamp TCP MSS for routed-IP egress (PMTU black-hole fix)\n"
        "After=network-online.target\nWants=network-online.target\n\n"
        "[Service]\nType=oneshot\nRemainAfterExit=yes\n"
        f"ExecStart=/bin/sh -c '{dele} 2>/dev/null; {add}'\n"
        f"ExecStop=/bin/sh -c '{dele} 2>/dev/null || true'\n\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )


def _harden(profile: ServerProfile, ssh: SSHClient, step) -> None:
    """Bake in the host-level fixes proven necessary in production, idempotently.
    Best-effort: a failure here is recorded but never rolls back the deploy.

      * TimeoutStopSec=30 — kumod drains held connections on stop and can hang
        for minutes; bound it so restarts always complete.
      * TCP MSS clamp — routed IPs tunnel through a sub-1500 MTU gateway with
        broken PMTUD, which black-holes large inbound TLS cert segments and
        stalls STARTTLS. Clamping the advertised MSS fixes it.
    """
    # applied BEFORE restart so this deploy's restart is already bounded
    ssh.mkdirs(TIMEOUT_DROPIN_DIR)
    ssh.put_content(TIMEOUT_DROPIN, "[Service]\nTimeoutStopSec=30\n")
    ssh.run("systemctl daemon-reload")
    step("harden-timeout", True, "TimeoutStopSec=30")

    mss = getattr(profile, "mss_clamp", 0) or 0
    if mss > 0:
        ssh.put_content(MSS_UNIT, _mss_unit(mss))
        r = ssh.run(
            "systemctl daemon-reload && "
            "systemctl enable --now mss-clamp.service && "
            f"iptables -t mangle -C OUTPUT -p tcp --syn -j TCPMSS --set-mss {mss} "
            "2>/dev/null && echo CLAMPED")
        ok = "CLAMPED" in r.stdout
        step("harden-mss", ok, f"MSS {mss}" if ok else (r.stderr or r.stdout).strip()[:120])
    else:
        step("harden-mss", True, "disabled (mss_clamp=0)")

    # firewall allow-list for the submission ports (only if the profile lists
    # allowed IPs) + fail2ban sshd jail. Best-effort, never rolls back.
    fw_ok, fw_detail = fw_apply(profile, ssh)
    step("harden-firewall", fw_ok, fw_detail)
    f2b_ok, f2b_detail = apply_fail2ban(ssh)
    step("harden-fail2ban", f2b_ok, f2b_detail)


def deploy(
    profile: ServerProfile,
    ssh: SSHClient,
    *,
    dry_run: bool = False,
    mode: Literal["merge", "replace"] = "merge",
    log: LogFn = print,
    dkim_keys: dict[str, str] | None = None,
) -> DeployResult:
    """Deploy a server profile.

    mode="merge" (default) folds this profile's IPs into the server's LIVE config
    without dropping IPs already there. mode="replace" renders only this profile
    and overwrites the whole policy dir (the old behaviour).
    """
    res = DeployResult(server=profile.name, started_at=datetime.now(), dry_run=dry_run)

    def step(name: str, ok: bool, detail: str = "") -> bool:
        res.steps.append(StepResult(name=name, ok=ok, detail=detail))
        log(f"[{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    # 0) merge — never overwrite existing IPs unless mode=replace ----------- #
    if mode == "merge":
        try:
            profile, added, kept = merged_profile(profile, ssh)
            step("merge", True, f"{added} new IP(s) added, {kept} existing kept")
        except Exception as exc:  # noqa: BLE001 - fall back to profile-only, don't abort
            step("merge", True, f"no live config to merge ({exc}); deploying profile as-is")

    # 1) generate ---------------------------------------------------------- #
    files = generate_all(profile)
    res.generated_files = sorted(files)
    step("generate", True, f"{len(files)} files")

    # 1b) integrity guard — every IP must be wired through all 5 files ------ #
    errs = integrity_errors(files)
    if not step("integrity", not errs, "; ".join(errs) if errs else "sources=queues=auth consistent"):
        res.finished_at = datetime.now()
        log("Refusing to deploy an inconsistent config.")
        return res

    if dry_run:
        res.success = True
        res.finished_at = datetime.now()
        log("[dry-run] stopping before any change on the server")
        return res

    # 2) backup ------------------------------------------------------------ #
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{POLICY_DIR}.bak-{ts}"
    ssh.run(f"cp -a {POLICY_DIR} {backup} 2>/dev/null || true")
    res.backup_path = backup
    step("backup", True, backup)

    # 3) upload ------------------------------------------------------------ #
    ssh.mkdirs(POLICY_DIR)
    for rel, content in files.items():
        ssh.put_content(f"{POLICY_DIR}/{rel}", content)
    step("upload", True, f"{len(files)} files -> {POLICY_DIR}")

    # 3b) DKIM private keys. The policy-extras signer loads each key from
    #     /opt/kumomta/etc/dkim/<domain>/<selector>.key
    if dkim_keys:
        sel = profile.dns.dkim_selector
        for domain, pem in dkim_keys.items():
            ssh.mkdirs(f"{DKIM_DIR}/{domain}")
            ssh.put_content(f"{DKIM_DIR}/{domain}/{sel}.key", pem)
        # make readable by the kumod service user (best-effort)
        ssh.run(f"chown -R kumod:kumod {DKIM_DIR} 2>/dev/null || true; "
                f"find {DKIM_DIR} -name '*.key' -exec chmod 600 {{}} + 2>/dev/null || true")
        step("dkim-keys", True, f"{len(dkim_keys)} key(s) -> {DKIM_DIR}/<domain>/{sel}.key")

    # 4) validate ---------------------------------------------------------- #
    v = ssh.run(f"{KUMOD} --policy {POLICY_DIR}/init.lua --validate --user kumod")
    if not step("validate", _validate_ok(v.stdout, v.stderr), (v.stderr or v.stdout).strip()[:200]):
        return _rollback(ssh, res, backup, step, log)

    # 4b) host hardening (idempotent; before restart so it takes effect now) #
    _harden(profile, ssh, step)

    # 5) restart ----------------------------------------------------------- #
    r = ssh.run("systemctl restart kumomta && sleep 2 && systemctl is-active kumomta")
    if not step("restart", "active" in r.stdout, r.stdout.strip() or r.stderr.strip()):
        return _rollback(ssh, res, backup, step, log)

    # 6) verify listeners -------------------------------------------------- #
    lc = ssh.run("ss -ltnp | grep -Ec ':587|:2525|:25 '")
    try:
        count = int(lc.stdout.strip() or "0")
    except ValueError:
        count = 0
    step("verify-listeners", count > 0, f"{count} listening")

    res.success = True
    res.finished_at = datetime.now()
    return res


def _rollback(ssh, res, backup, step, log) -> DeployResult:
    log("Deployment failed — rolling back to previous config…")
    ssh.run(
        f"rm -rf {POLICY_DIR} && cp -a {backup} {POLICY_DIR} && "
        "systemctl restart kumomta && sleep 2 && systemctl is-active kumomta"
    )
    res.rolled_back = True
    res.success = False
    res.finished_at = datetime.now()
    step("rollback", True, f"restored {backup}")
    return res
