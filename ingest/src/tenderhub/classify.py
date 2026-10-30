"""Flags defence & security notices so they can be filtered regardless of source."""

from __future__ import annotations

import re

from .models import Notice

DEFENCE_DIRECTIVE = "32009L0081"

# CPV prefixes: 35 = security, fire-fighting, police and defence equipment;
# plus military-specific codes elsewhere in the tree
DEFENCE_CPV_PREFIXES = ("35", "73400000", "50413000", "34711", "34730000", "34731000", "38820000", "63727")

# buyer-name patterns for defence/security bodies across EU languages
BUYER_PATTERNS = re.compile(
    r"\b(jednostka wojskowa|wojskow\w*|regionalna baza logistyczna|agencja uzbrojenia|agencja mienia wojskowego|"
    r"inspektorat wsparcia|sił zbrojnych|żandarmeri\w*|marynark\w* wojenn\w*|"
    r"bundeswehr|baainbw|bundesamt für ausrüstung|"
    r"ministère des armées|direction générale de l'armement|\bdga\b|"
    r"ministerio de defensa|armamento y material|ministero della difesa|segretariato generale della difesa|"
    r"defensie|ministry of defen[cs]e|ministerstwo obrony|ministerstvo obrany|"
    r"forsvar\w*|puolustusvoim\w*|fmv\b|nspa\b|ncia\b|occar|european defence agency|"
    r"armed forces|army|navy|air force|military|defen[cs]e)\b",
    re.IGNORECASE,
)


def classify_defence(n: Notice) -> tuple[bool, list[str]]:
    reasons = []
    if n.legal_basis == DEFENCE_DIRECTIVE:
        reasons.append("legal_basis:2009/81")
    cpv = [c for c in n.cpv_codes if c.startswith(DEFENCE_CPV_PREFIXES)]
    if cpv:
        reasons.append("cpv:" + ",".join(sorted(set(cpv))[:5]))
    if n.buyer_name and (m := BUYER_PATTERNS.search(n.buyer_name)):
        reasons.append("buyer:" + m.group(0).lower())
    return bool(reasons), reasons
