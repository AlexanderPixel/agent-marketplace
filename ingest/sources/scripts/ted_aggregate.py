"""Turn mined TED JSONL into: every e-procurement platform in use, and every defence buyer."""
import csv, glob, json, re, sys
from collections import Counter, defaultdict
from urllib.parse import urlparse

raw, out = sys.argv[1], sys.argv[2]

# second-level public suffixes seen in EU procurement URLs; enough to get the registrable domain right
SUFFIX2 = {"gov.pl", "com.pl", "org.pl", "net.pl", "edu.pl", "mil.pl", "waw.pl", "gv.at", "or.at", "co.at", "gob.es",
           "gouv.fr", "asso.fr", "gov.it", "gov.uk", "co.uk", "org.uk", "ac.uk", "nhs.uk", "gov.ie", "gov.pt", "gov.gr",
           "gov.cy", "com.cy", "gov.mt", "com.mt", "gov.lv", "gov.lt", "gov.hu", "gov.ro", "gov.si", "gov.hr",
           "gov.sk", "gov.cz", "gov.bg", "gov.ua", "gov.rs", "gov.mk", "gov.al", "gov.me", "admin.ch", "europa.eu",
           "riik.ee", "gov.ee", "gov.lu", "etat.lu", "public.lu", "gov.be", "fgov.be", "gov.dk", "gov.fi", "gov.se",
           "gov.no", "gov.is", "mod.uk", "mil.be", "nato.int", "mod.gov.ua"}

def domain(url):
    try:
        h = urlparse(url if "//" in url else "http://" + url).hostname or ""
    except ValueError:
        return ""
    h = h.lower().removeprefix("www.")
    parts = h.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in SUFFIX2:
        return ".".join(parts[-3:])
    if len(parts) >= 4 and ".".join(parts[-3:]) in SUFFIX2:
        return ".".join(parts[-4:])
    return ".".join(parts[-2:])

def buyer(n):
    names = n.get("buyer-name") or {}
    for lang in names:
        if names[lang]:
            return names[lang][0].strip()
    return ""

def rows(pattern):
    for p in sorted(glob.glob(f"{raw}/{pattern}")):
        with open(p) as f:
            for line in f:
                yield json.loads(line)

# ---- platforms: every domain hosting tender documents / submissions
dom_notices = Counter(); dom_buyers = defaultdict(set); dom_country = defaultdict(Counter); dom_example = {}
country_total = Counter()
for n in rows("comp_*.jsonl"):
    c = (n.get("buyer-country") or ["?"])[0]
    country_total[c] += 1
    urls = (n.get("document-url-lot") or []) + (n.get("submission-url-lot") or [])
    for d in {domain(u) for u in urls if u}:
        if not d or "." not in d:
            continue
        dom_notices[d] += 1; dom_buyers[d].add(buyer(n)); dom_country[d][c] += 1
        dom_example.setdefault(d, next(u for u in urls if domain(u) == d))

with open(f"{out}/ted-platforms.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["domain", "kind", "main_country", "countries", "notices_12m", "distinct_buyers", "share_of_main_country", "example_url"])
    for d, k in dom_notices.most_common():
        nb = len(dom_buyers[d]); mc, mn = dom_country[d].most_common(1)[0]
        kind = "multi_buyer_platform" if nb >= 3 else "buyer_own_site"
        w.writerow([d, kind, mc, " ".join(c for c, _ in dom_country[d].most_common()), k, nb,
                    f"{mn / country_total[mc]:.3f}", dom_example[d]])

# ---- defence buyers: anyone publishing under Directive 2009/81 or buying CPV 35*
b_notices = Counter(); b_dir = Counter(); b_country = {}; b_site = {}; b_platforms = defaultdict(Counter); b_last = {}
for n in rows("def_*.jsonl"):
    b = buyer(n)
    if not b:
        continue
    c = (n.get("buyer-country") or ["?"])[0]
    key = (c, re.sub(r"\W+", " ", b.lower()).strip())
    b_notices[key] += 1; b_country[key] = (c, b)
    if "32009L0081" in (n.get("legal-basis") or []):
        b_dir[key] += 1
    if n.get("buyer-internet-address"):
        b_site.setdefault(key, n["buyer-internet-address"][0])
    for d in {domain(u) for u in (n.get("document-url-lot") or []) + (n.get("submission-url-lot") or []) if u}:
        b_platforms[key][d] += 1
    b_last[key] = max(b_last.get(key, ""), n.get("publication-date", "")[:10])

with open(f"{out}/ted-defence-buyers.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["country", "buyer", "notices_since_2023", "defence_directive_notices", "last_notice", "website", "platforms_used"])
    for key, k in sorted(b_notices.items(), key=lambda kv: (kv[0][0], -kv[1])):
        c, b = b_country[key]
        w.writerow([c, b, k, b_dir[key], b_last.get(key, ""), b_site.get(key, ""),
                    " ".join(d for d, _ in b_platforms[key].most_common(3))])

print("competition notices:", sum(country_total.values()))
print("domains:", len(dom_notices), "multi-buyer platforms:", sum(len(dom_buyers[d]) >= 3 for d in dom_notices))
print("defence buyers:", len(b_notices), "countries:", len({c for c, _ in b_notices}))
