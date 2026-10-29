"""Core is UI-agnostic, so it's fully testable headless."""
from kumo_automation.core.generators import generate_all
from kumo_automation.core.models import (
    Environment,
    IPEntry,
    PasswordStrategy,
    ServerProfile,
    ShapingConfig,
    ShapingPreset,
    SSHTarget,
)


def _profile(strategy=PasswordStrategy.shared) -> ServerProfile:
    return ServerProfile(
        name="Test",
        environment=Environment.testing,
        ssh=SSHTarget(host="1.2.3.4"),
        password_strategy=strategy,
        shared_password="secret",
        domains=["example.com"],
        ips=[
            IPEntry(ip="203.0.113.10", hostname="a.example.com", username="a", domain="example.com"),
            IPEntry(ip="203.0.113.11", hostname="b.example.com", username="b",
                    password="own", domain="example.com"),
        ],
    )


def test_generates_expected_files():
    # shaping is enabled by default (shaping_custom.toml); listener_domains.toml is
    # always emitted (relay auth); dkim_data.toml because DKIM is on by default.
    files = generate_all(_profile())
    assert set(files) == {"init.lua", "sources.toml", "queues.toml", "ip_map.toml",
                          "smtp-auth.json", "shaping_custom.toml",
                          "listener_domains.toml", "dkim_data.toml"}


def test_shaping_on_by_default():
    files = generate_all(_profile())
    shaping = files["shaping_custom.toml"]
    # per-ISP blocks + a default fallback are present
    assert '["gmail.com"]' in shaping
    assert '["yahoo.com"]' in shaping
    assert "connection_limit" in shaping
    assert '["default"]' in shaping
    # init.lua actually loads it
    assert "shaping:setup" in files["init.lua"]
    assert "require 'policy-extras.shaping'" in files["init.lua"]


def test_shaping_can_be_disabled():
    p = _profile()
    p.shaping = ShapingConfig(enabled=False)
    files = generate_all(p)
    assert "shaping_custom.toml" not in files          # no file emitted
    assert "shaping:setup" not in files["init.lua"]    # init.lua doesn't load it


def test_init_loads_both_shaping_files():
    # item 4: init.lua must load community base + our generated overrides
    init = generate_all(_profile())["init.lua"]
    assert "/opt/kumomta/share/policy-extras/shaping.toml" in init
    assert "/opt/kumomta/etc/policy/shaping_custom.toml" in init


def test_config_monitor_globs_cover_data_files():
    # queue.lua calls kumo.validation_failed() unless its data file is covered by
    # the config-monitor globs, so init.lua must set them over the policy dir.
    init = generate_all(_profile())["init.lua"]
    assert "kumo.set_config_monitor_globs" in init
    assert "/opt/kumomta/etc/policy/*.toml" in init
    assert "/opt/kumomta/etc/policy/*.json" in init


def test_no_standalone_egress_handler_when_shaping_on():
    # with shaping ON, policy-extras/shaping owns get_egress_path_config; a second
    # standalone handler conflicts and fails kumod --validate.
    on = generate_all(_profile())["init.lua"]
    assert "kumo.on('get_egress_path_config'" not in on
    # Ipv4Only still applied — via the shaping "default" block instead
    assert "ip_lookup_strategy = \"Ipv4Only\"" in generate_all(_profile())["shaping_custom.toml"]

    # with shaping OFF, the standalone handler IS present (nothing else sets it)
    p = _profile()
    p.shaping.enabled = False
    off = generate_all(p)["init.lua"]
    assert "kumo.on('get_egress_path_config'" in off


def test_shaping_setup_runs_before_queue_setup():
    # KumoMTA requires queue.lua to register get_queue_config LAST; shaping.lua
    # also registers one, so shaping:setup must come *before* queue_module:setup
    # or kumod --validate fails ("queue.lua is not the last module to register").
    init = generate_all(_profile())["init.lua"]
    # match the actual calls (with brace), not any mention in comments
    assert init.index("shaping:setup {") < init.index("queue_module:setup {")


def test_returnpath_rewrite_present_and_conditional():
    # item 1: bounces@<from-domain>, but only when no envelope sender was given
    init = generate_all(_profile())["init.lua"]
    assert "msg:set_sender('bounces@'" in init
    # guarded by the "sender domain empty" check -> client-supplied one is kept
    assert "_s.domain == ''" in init
    # final fallback is a real configured domain, not a hardcoded one
    assert "or 'example.com'" in init


def test_dkim_on_by_default_off_when_disabled():
    on_files = generate_all(_profile())
    on = on_files["init.lua"]
    # DKIM on: helper wired in init.lua + data file emitted
    assert "require 'policy-extras.dkim_sign'" in on and "dkim_signer(msg)" in on
    assert "dkim_data.toml" in on_files

    p = _profile()
    p.dns.dkim_enabled = False
    off_files = generate_all(p)
    off = off_files["init.lua"]
    assert "policy-extras.dkim_sign" not in off   # helper not required
    assert "dkim_signer(msg)" not in off          # no signing call
    assert "dkim_data.toml" not in off_files      # no data file emitted
    # return-path + tenant routing still present without DKIM
    assert "msg:set_sender('bounces@'" in off
    assert "tenant-" in off


def test_shaping_preset_scales_limits():
    def gmail_conn_limit(preset):
        p = _profile()
        p.shaping = ShapingConfig(enabled=True, preset=preset)
        toml = generate_all(p)["shaping_custom.toml"]
        # find the connection_limit line inside the gmail block
        block = toml.split('["gmail.com"]', 1)[1]
        line = next(ln for ln in block.splitlines() if "connection_limit" in ln)
        return int(line.split("=")[1])

    assert (gmail_conn_limit(ShapingPreset.conservative)
            < gmail_conn_limit(ShapingPreset.balanced)
            < gmail_conn_limit(ShapingPreset.aggressive))


def test_per_ip_routing_present():
    files = generate_all(_profile())
    assert 'source_address = "203.0.113.10"' in files["sources.toml"]
    assert 'tenant."tenant-203.0.113.10"' in files["queues.toml"]
    assert 'allowed_from = "example.com"' in files["ip_map.toml"]


def test_shared_vs_per_ip_password():
    import json

    shared = json.loads(generate_all(_profile(PasswordStrategy.shared))["smtp-auth.json"])
    assert shared["passwords"]["a"] == "secret"
    assert shared["passwords"]["b"] == "secret"          # shared overrides per-ip value

    per_ip = json.loads(generate_all(_profile(PasswordStrategy.per_ip))["smtp-auth.json"])
    assert per_ip["passwords"]["b"] == "own"

    # username -> ip mapping drives per-IP egress
    assert shared["users"]["a"] == "203.0.113.10"
