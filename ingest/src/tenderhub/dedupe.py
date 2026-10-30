"""Cross-source duplicate detection: the same tender published on several sources becomes one cluster.

Two notices land in the same cluster when they share any match key (transitively):
- a globally unique identifier from `refs`: TED publication number, eForms procedure/notice UUID,
  OCDS ocid, BZP number;
- a fingerprint for sources without such ids: buyer country + deadline date + normalized title.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TED = re.compile(r"^0*(\d{1,8})-(\d{4})$")
_BZP = re.compile(r"^(\d{4})/BZP\s*0*(\d+)", re.IGNORECASE)
MIN_FINGERPRINT_TITLE = 20  # shorter titles ("Dostawa paliwa") are too generic to match on


def normalize_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", t.replace("ł", "l"))


def match_keys(source: str, source_id: str, refs: dict[str, Any], country: str | None, title: str | None,
               deadline_iso: str | None) -> set[str]:
    keys: set[str] = set()
    if source == "ted":
        refs = {**refs, "ted": source_id}
    for name, value in refs.items():
        for v in str(value or "").split(","):
            v = v.strip()
            if not v:
                continue
            if name in ("ted", "ted_all") and (m := _TED.match(v)):
                keys.add(f"ted:{m.group(1)}-{m.group(2)}")
            elif name in ("procedure", "notice") and _UUID.match(v.lower()):
                keys.add(f"{name}:{v.lower()}")
            elif name == "ocid":
                keys.add(f"ocid:{v.lower()}")
            elif name == "bzp" and (m := _BZP.match(v)):
                keys.add(f"bzp:{m.group(1)}/{int(m.group(2))}")
    norm = normalize_title(title or "")
    if country and deadline_iso and len(norm) >= MIN_FINGERPRINT_TITLE:
        keys.add(f"fp:{country}|{deadline_iso[:10]}|{norm[:100]}")
    return keys
