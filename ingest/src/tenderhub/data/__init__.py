"""Static data shipped with the package.

tenants/<platform>.json: buyer tenants seen on multi-tenant platforms (subdomains or profile slugs),
mined from a year of TED and six months of BZP notices, most active first.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any


def load_tenants(platform: str) -> list[dict[str, Any]]:
    """[{"tenant": "uck-gdansk.ezamawiajacy.pl", "notices": 42, "example_url": ..., "buyer": ...}, ...]"""
    path = resources.files(__package__) / "tenants" / f"{platform}.json"
    return json.loads(path.read_text()) if path.is_file() else []
