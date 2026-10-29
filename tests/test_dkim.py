"""DKIM: stable keys, public/private match, config signing, key deployment."""
import json

from kumo_automation.core import deploy as deploy_mod
from kumo_automation.core import dkim, dnsgen
from kumo_automation.core.generators import generate_all
from kumo_automation.core.models import (
    Environment,
    IPEntry,
    ServerProfile,
    SSHTarget,
)
from kumo_automation.core.ssh import CmdResult


def _profile():
    return ServerProfile(
        name="T1", environment=Environment.production,
        ssh=SSHTarget(host="h", use_password=False),
        ips=[IPEntry(ip="1.1.1.1", hostname="s1.example.com", username="s1", domain="example.com"),
             IPEntry(ip="1.1.1.2", hostname="s2.shop.com", username="s2", domain="shop.com")],
        shared_password="pw", domains=["example.com", "shop.com"])


def test_keystore_is_stable(tmp_path):
    ks = dkim.KeyStore(tmp_path)
    k1 = ks.get_or_create("srv", "example.com")
    k2 = ks.get_or_create("srv", "example.com")
    assert k1 == k2 and "PRIVATE KEY" in k1


def test_public_derives_from_private():
    pem = dkim.generate_private_pem()
    assert dkim.public_txt_from_private(pem).startswith("v=DKIM1; k=rsa; p=")


def test_dns_uses_provided_key_so_it_matches_server():
    p = _profile()
    pem = dkim.generate_private_pem()
    keys = {"example.com": pem, "shop.com": dkim.generate_private_pem()}
    records, out = dnsgen.generate_records(p, keys=keys)
    assert out["example.com"] == pem                        # reused, not regenerated
    rec = next(r for r in records if r.name == "s1._domainkey.example.com")
    assert rec.value == dkim.public_txt_from_private(pem)   # DNS public == server private


def test_init_lua_signs_dkim():
    # DKIM is done via the policy-extras dkim_sign helper (dkim_data.toml + key on
    # disk), NOT the hand-rolled rsa_sha256_signer.
    files = generate_all(_profile())
    init = files["init.lua"]
    assert "require 'policy-extras.dkim_sign'" in init
    assert "dkim_sign:setup { '/opt/kumomta/etc/policy/dkim_data.toml' }" in init
    assert "dkim_signer(msg)" in init
    assert "rsa_sha256_signer" not in init
    # the signer's data file carries the selector + the per-domain blocks
    data = files["dkim_data.toml"]
    assert 'selector = "s1"' in data
    assert '[domain."example.com"]' in data
    assert '[domain."shop.com"]' in data


def test_smtp_auth_has_domains_map():
    auth = json.loads(generate_all(_profile())["smtp-auth.json"])
    assert auth["domains"] == {"s1": "example.com", "s2": "shop.com"}


class _FakeSSH:
    def __init__(self):
        self.puts = {}

    def run(self, cmd, timeout=None):
        return CmdResult(command=cmd, exit_code=0, stdout="active", stderr="")

    def mkdirs(self, p):
        pass

    def put_content(self, path, content):
        self.puts[path] = content

    def get_content(self, path):
        return ""


def test_deploy_uploads_dkim_keys():
    ssh = _FakeSSH()
    keys = {"example.com": "PEM-A", "shop.com": "PEM-B"}
    res = deploy_mod.deploy(_profile(), ssh, dry_run=False, log=lambda *_: None, dkim_keys=keys)
    # keys land at <domain>/<selector>.key (selector s1) so the dkim_sign helper finds them
    assert "/opt/kumomta/etc/dkim/example.com/s1.key" in ssh.puts
    assert ssh.puts["/opt/kumomta/etc/dkim/shop.com/s1.key"] == "PEM-B"
    assert any(s.name == "dkim-keys" for s in res.steps)
