"""Built-in generators: KumoMTA policy files + SMTP user credentials."""
from __future__ import annotations

import json

from ..models import ServerProfile
from ..templating import render
from .base import Generator, register

# Faster log flush in testing so logs appear quickly; steadier in production.
_SEGMENT_DURATION = {"testing": "5s", "production": "10s"}

# Per-destination-ISP shaping presets. Each block = a group of ISP domains that
# share the same connection/rate/delivery limits. "conservative" matches our
# proven new-IP warmup limits; balanced/aggressive scale them up for warm IPs.
# Grouped by ISP family: Yahoo (strictest), Gmail, Outlook, Apple.
_SHAPING_PRESETS: dict[str, list[dict]] = {
    "conservative": [
        {"domains": ["yahoo.com", "aol.com", "ymail.com"],
         "connection_limit": 3, "max_connection_rate": "5/min",
         "max_message_rate": "30/min", "max_deliveries_per_connection": 50},
        {"domains": ["gmail.com", "googlemail.com"],
         "connection_limit": 5, "max_connection_rate": "10/min",
         "max_message_rate": "50/min", "max_deliveries_per_connection": 100},
        {"domains": ["outlook.com", "hotmail.com", "live.com", "msn.com"],
         "connection_limit": 4, "max_connection_rate": "8/min",
         "max_message_rate": "40/min", "max_deliveries_per_connection": 50},
        {"domains": ["icloud.com", "me.com"],
         "connection_limit": 5, "max_connection_rate": "10/min",
         "max_message_rate": "50/min", "max_deliveries_per_connection": 50},
    ],
    "balanced": [
        {"domains": ["yahoo.com", "aol.com", "ymail.com"],
         "connection_limit": 6, "max_connection_rate": "10/min",
         "max_message_rate": "60/min", "max_deliveries_per_connection": 100},
        {"domains": ["gmail.com", "googlemail.com"],
         "connection_limit": 10, "max_connection_rate": "20/min",
         "max_message_rate": "100/min", "max_deliveries_per_connection": 200},
        {"domains": ["outlook.com", "hotmail.com", "live.com", "msn.com"],
         "connection_limit": 8, "max_connection_rate": "16/min",
         "max_message_rate": "80/min", "max_deliveries_per_connection": 100},
        {"domains": ["icloud.com", "me.com"],
         "connection_limit": 10, "max_connection_rate": "20/min",
         "max_message_rate": "100/min", "max_deliveries_per_connection": 100},
    ],
    "aggressive": [
        {"domains": ["yahoo.com", "aol.com", "ymail.com"],
         "connection_limit": 12, "max_connection_rate": "20/min",
         "max_message_rate": "120/min", "max_deliveries_per_connection": 200},
        {"domains": ["gmail.com", "googlemail.com"],
         "connection_limit": 20, "max_connection_rate": "40/min",
         "max_message_rate": "200/min", "max_deliveries_per_connection": 400},
        {"domains": ["outlook.com", "hotmail.com", "live.com", "msn.com"],
         "connection_limit": 16, "max_connection_rate": "32/min",
         "max_message_rate": "160/min", "max_deliveries_per_connection": 200},
        {"domains": ["icloud.com", "me.com"],
         "connection_limit": 20, "max_connection_rate": "40/min",
         "max_message_rate": "200/min", "max_deliveries_per_connection": 200},
    ],
}


@register
class KumoMTAConfigGenerator(Generator):
    name = "kumomta-config"

    def generate(self, profile: ServerProfile) -> dict[str, str]:
        ctx = {
            "environment": profile.environment.value,
            "ips": profile.ips,
            "smtp_port": profile.smtp_port,
            "submission_ports": profile.effective_ports(),
            "max_segment_duration": _SEGMENT_DURATION.get(profile.environment.value, "10s"),
            "monitoring_hub": (profile.monitoring.prometheus_hub or "")
            if profile.monitoring.enabled else "",
            "dkim_selector": profile.dns.dkim_selector,
            "dkim_enabled": profile.dns.dkim_enabled,
            "dkim_domains": profile.unique_domains(),
            "shaping_enabled": profile.shaping.enabled,
            "hide_received": getattr(profile, "hide_received", True),
            # final fallback bounce domain when neither the envelope sender nor
            # the From header carries a domain (rare); first configured domain.
            "default_bounce_domain": (profile.unique_domains() or [""])[0],
            "auth_usernames": profile.auth_usernames(),
        }
        files = {
            "init.lua": render("init.lua.j2", **ctx),
            "sources.toml": render("sources.toml.j2", **ctx),
            "queues.toml": render("queues.toml.j2", **ctx),
            "ip_map.toml": render("ip_map.toml.j2", **ctx),
            # relay authorization for authenticated submission (always required)
            "listener_domains.toml": render("listener_domains.toml.j2", **ctx),
        }
        # DKIM signing data — only when enabled (bare test boxes skip it and send
        # unsigned rather than failing on a missing key).
        if profile.dns.dkim_enabled:
            files["dkim_data.toml"] = render("dkim_data.toml.j2", **ctx)
        return files


@register
class ShapingGenerator(Generator):
    """Per-destination-ISP traffic shaping. Only emits a file when shaping is
    enabled on the profile; init.lua loads it via shaping:setup when enabled."""
    name = "shaping"

    def applies_to(self, profile: ServerProfile) -> bool:
        return profile.shaping.enabled

    def generate(self, profile: ServerProfile) -> dict[str, str]:
        preset = profile.shaping.preset.value
        blocks = _SHAPING_PRESETS[preset]
        return {
            "shaping_custom.toml": render(
                "shaping.toml.j2", shaping_preset=preset, shaping_blocks=blocks
            )
        }


@register
class SmtpUsersGenerator(Generator):
    name = "smtp-users"

    def generate(self, profile: ServerProfile) -> dict[str, str]:
        # A shared login maps to all IPs; the listener rotates through that list.
        # Per-IP logins retain the one-username-to-one-IP mapping.
        users: dict[str, str | list[str]] = {}
        passwords: dict[str, str] = {}
        domains: dict[str, str] = {}
        for ip in profile.ips:
            username = profile.effective_username(ip)
            if not username:
                raise ValueError("A shared SMTP username is required for shared credentials.")
            if profile.credential_strategy.value == "per_ip" and username in users:
                raise ValueError(
                    "Duplicate SMTP username(s) would collide in smtp-auth.json: "
                    + username + ". Every IP needs a unique username.")
            if isinstance(users.get(username), list):
                users[username].append(ip.ip)
            elif username in users:
                users[username] = [users[username], ip.ip]
            else:
                users[username] = ip.ip
            passwords[username] = profile.effective_password(ip)
            domains.setdefault(username, ip.domain)
        payload = {"users": users, "passwords": passwords, "domains": domains}
        return {"smtp-auth.json": json.dumps(payload, indent=2)}
