"""Phase A — safety & correctness features:
  * empty-string relay back-door removed from listener_domains
  * Received-header strip (hide sending infra) toggle
  * policy-file integrity check (sources = queues = auth = listener)
  * non-destructive MERGE reconstruction + union
"""
import json

from kumo_automation.core.generators import generate_all
from kumo_automation.core.merge import (
    merge_entries,
    parse_smtp_auth,
    parse_sources,
    reconstruct_entries,
)
from kumo_automation.core.models import (
    Environment,
    IPEntry,
    PasswordStrategy,
    ServerProfile,
    SSHTarget,
)
from kumo_automation.core.validation import (
    integrity_errors,
    is_consistent,
    policy_counts,
)


def _profile(**kw) -> ServerProfile:
    base = dict(
        name="Test",
        environment=Environment.testing,
        ssh=SSHTarget(host="1.2.3.4"),
        password_strategy=PasswordStrategy.shared,
        shared_password="secret",
        domains=["example.com"],
        ips=[
            IPEntry(ip="203.0.113.10", hostname="a.example.com", username="a", domain="example.com"),
            IPEntry(ip="203.0.113.11", hostname="b.example.com", username="b", domain="example.com"),
        ],
    )
    base.update(kw)
    return ServerProfile(**base)


# --- back-door -------------------------------------------------------------- #
def test_no_empty_string_backdoor_in_listener():
    ld = generate_all(_profile())["listener_domains.toml"]
    assert '"a"' in ld and '"b"' in ld          # real logins present
    # no standalone empty-string entry (the open-relay back-door)
    assert '\n  "",' not in ld
    assert not any(line.strip() == '"",' for line in ld.splitlines())


# --- Received strip --------------------------------------------------------- #
def test_hide_received_default_on():
    init = generate_all(_profile())["init.lua"]
    assert "remove_all_named_headers('Received')" in init


def test_hide_received_can_be_disabled():
    init = generate_all(_profile(hide_received=False))["init.lua"]
    assert "remove_all_named_headers('Received')" not in init


# --- integrity check -------------------------------------------------------- #
def test_generated_config_is_consistent():
    files = generate_all(_profile())
    counts = policy_counts(files)
    assert counts["sources"] == counts["queues"] == counts["auth"] == counts["listener"] == 2
    assert is_consistent(files)
    assert integrity_errors(files) == []


def test_integrity_catches_count_drift():
    # simulate the real bug: queues short by one tenant
    files = generate_all(_profile())
    files["queues.toml"] = files["queues.toml"].replace(
        '[tenant."tenant-203.0.113.11"]', '# removed', 1
    )
    errs = integrity_errors(files)
    assert errs and "counts do not match" in errs[0].lower()


def test_integrity_catches_backdoor():
    files = generate_all(_profile())
    files["listener_domains.toml"] = files["listener_domains.toml"].replace(
        'relay_from_authz = [', 'relay_from_authz = [\n  "",', 1
    )
    assert any("back-door" in e for e in integrity_errors(files))


# --- merge ------------------------------------------------------------------ #
_LIVE_SOURCES = '''
[source."ip-203.0.113.10"]
source_address = "203.0.113.10"
ehlo_domain = "a.example.com"

[pool."pool-203.0.113.10"."ip-203.0.113.10"]

[source."ip-203.0.113.99"]
source_address = "203.0.113.99"
ehlo_domain = "old.example.com"

[pool."pool-203.0.113.99"."ip-203.0.113.99"]
'''
_LIVE_AUTH = json.dumps({
    "users": {"a": "203.0.113.10", "old": "203.0.113.99"},
    "passwords": {"a": "secret", "old": "oldpw"},
    "domains": {"a": "example.com", "old": "example.com"},
})


def test_parse_and_reconstruct_live():
    assert parse_sources(_LIVE_SOURCES) == {
        "203.0.113.10": "a.example.com", "203.0.113.99": "old.example.com"}
    users, pw, dom = parse_smtp_auth(_LIVE_AUTH)
    assert users["old"] == "203.0.113.99" and pw["old"] == "oldpw"
    entries = reconstruct_entries(_LIVE_SOURCES, _LIVE_AUTH)
    old = next(e for e in entries if e.ip == "203.0.113.99")
    assert old.username == "old" and old.password == "oldpw" and old.hostname == "old.example.com"


def test_merge_keeps_existing_adds_new():
    existing = reconstruct_entries(_LIVE_SOURCES, _LIVE_AUTH)   # .10 and .99
    new = [
        IPEntry(ip="203.0.113.10", hostname="a.example.com", username="a", domain="example.com"),
        IPEntry(ip="203.0.113.11", hostname="b.example.com", username="b", domain="example.com"),
    ]
    combined, added, kept = merge_entries(existing, new)
    ips = {e.ip for e in combined}
    assert ips == {"203.0.113.10", "203.0.113.11", "203.0.113.99"}  # nothing dropped
    assert added == 1 and kept == 2                                 # only .11 is new
    # existing .99 kept exactly (never overwritten)
    assert next(e for e in combined if e.ip == "203.0.113.99").password == "oldpw"
