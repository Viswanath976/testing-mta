"""Shared Jinja2 environment. Templates live in ./templates and are the *only*
place config file layout is defined — change them (not code) to alter output.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_TEMPLATE_DIR = Path(__file__).parent / "templates"

env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=StrictUndefined,   # fail loudly on missing variables
    autoescape=False,            # we generate config/Lua, not HTML (report escapes explicitly)
)


def render(template_name: str, **context) -> str:
    return env.get_template(template_name).render(**context)
