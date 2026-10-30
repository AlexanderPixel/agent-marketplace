"""Country and language code conversions used by connectors."""

from __future__ import annotations

ISO3_TO_ISO2 = {
    "AUT": "AT", "BEL": "BE", "BGR": "BG", "HRV": "HR", "CYP": "CY", "CZE": "CZ", "DNK": "DK", "EST": "EE",
    "FIN": "FI", "FRA": "FR", "DEU": "DE", "GRC": "GR", "HUN": "HU", "IRL": "IE", "ITA": "IT", "LVA": "LV",
    "LTU": "LT", "LUX": "LU", "MLT": "MT", "NLD": "NL", "POL": "PL", "PRT": "PT", "ROU": "RO", "SVK": "SK",
    "SVN": "SI", "ESP": "ES", "SWE": "SE", "NOR": "NO", "ISL": "IS", "LIE": "LI", "CHE": "CH", "GBR": "GB",
    "UKR": "UA", "MDA": "MD", "GEO": "GE", "ARM": "AM", "AZE": "AZ", "TUR": "TR", "SRB": "RS", "MNE": "ME",
    "MKD": "MK", "ALB": "AL", "BIH": "BA", "XKX": "XK", "USA": "US", "CAN": "CA", "MAR": "MA", "TUN": "TN",
    "EGY": "EG", "ISR": "IL", "JOR": "JO", "LBN": "LB", "IND": "IN", "CHN": "CN", "JPN": "JP", "KOR": "KR",
    "AUS": "AU", "BRA": "BR", "ZAF": "ZA", "KEN": "KE", "NGA": "NG", "ETH": "ET", "SEN": "SN", "GHA": "GH",
}

LANG3_TO_LANG2 = {
    "bul": "bg", "ces": "cs", "dan": "da", "deu": "de", "ell": "el", "eng": "en", "est": "et", "fin": "fi",
    "fra": "fr", "gle": "ga", "hrv": "hr", "hun": "hu", "ita": "it", "lav": "lv", "lit": "lt", "mlt": "mt",
    "nld": "nl", "pol": "pl", "por": "pt", "ron": "ro", "slk": "sk", "slv": "sl", "spa": "es", "swe": "sv",
    "nor": "no", "isl": "is", "ukr": "uk",
}


def iso3_to_iso2(code: str | None) -> str | None:
    if not code:
        return None
    code = code.upper()
    if len(code) == 2:
        return code
    return ISO3_TO_ISO2.get(code)


def lang3_to_lang2(code: str | None) -> str | None:
    return LANG3_TO_LANG2.get(code.lower()) if code else None
