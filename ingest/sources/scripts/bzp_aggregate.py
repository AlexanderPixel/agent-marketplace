"""BZP JSONL -> Polish platforms in use, and Polish defence/security buyers (below EU threshold included)."""
import csv, glob, json, re, sys
from collections import Counter, defaultdict
from urllib.parse import urlparse

raw, out = sys.argv[1], sys.argv[2]

# military units and defence/security bodies, matched on buyer name
MIL = re.compile(r"jednostka wojskowa|\bjw\b|wojskow|regionalna baza logistyczna|agencja uzbrojenia|agencja mienia wojskowego|"
                 r"inspektorat|sił zbrojnych|sil zbrojnych|żandarmeri|marynarki wojennej|portu wojennego|brygad|pułk|batalion|"
                 r"dowództw|obrony terytorialnej|zarząd infrastruktury|oddział gospodarczy|baza lotnictwa|skrzydł|"
                 r"wojsk |straż graniczn|komenda główna policji|służba ochrony państwa|agencja bezpieczeństwa|"
                 r"rządowa agencja rezerw|polska grupa zbrojeniowa|centrum szkolenia|akademia sztuki wojennej|"
                 r"wojskowa akademia techniczna|lotnicza akademia wojskowa|akademia marynarki", re.I)

def host(u):
    u = u.strip().rstrip(".,;")
    try:
        h = urlparse(u if "//" in u else "https://" + u).hostname or ""
    except ValueError:
        return ""
    return h.lower().removeprefix("www.")

def platform_domain(h):
    # tenant subdomains (x.ezamawiajacy.pl, x.logintrade.net, x.eb2b.com.pl) collapse to the platform
    parts = h.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in {"com.pl", "gov.pl", "mil.pl", "edu.pl", "org.pl", "net.pl"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])

notices = []
for p in sorted(glob.glob(f"{raw}/bzp_*.jsonl")):
    notices += [json.loads(l) for l in open(p)]

plat_n = Counter(); plat_buyers = defaultdict(set); plat_example = {}
for n in notices:
    if not n["platform_url"]:
        continue
    d = platform_domain(host(n["platform_url"]))
    if "." not in d:
        continue
    plat_n[d] += 1; plat_buyers[d].add(n["buyer_nip"]); plat_example.setdefault(d, n["platform_url"])

with open(f"{out}/bzp-platforms.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["domain", "kind", "notices_6m", "share", "distinct_buyers", "example_url"])
    total = sum(plat_n.values())
    for d, k in plat_n.most_common():
        nb = len(plat_buyers[d])
        w.writerow([d, "multi_buyer_platform" if nb >= 3 else "buyer_own_site", k, f"{k / total:.4f}", nb, plat_example[d]])

buy = defaultdict(lambda: {"n": 0, "cpv35": 0, "names": Counter(), "city": "", "platforms": Counter(), "last": "", "titles": []})
for n in notices:
    is_mil = bool(MIL.search(n["buyer"] or ""))
    is_cpv = (n["cpv"] or "").startswith("35")
    if not (is_mil or is_cpv):
        continue
    b = buy[n["buyer_nip"]]
    b["n"] += 1; b["cpv35"] += is_cpv; b["names"][n["buyer"].strip()] += 1; b["city"] = n["city"]
    b["last"] = max(b["last"], n["date"]); b["mil"] = b.get("mil", False) or is_mil
    if n["platform_url"]:
        b["platforms"][platform_domain(host(n["platform_url"]))] += 1
    if len(b["titles"]) < 2:
        b["titles"].append((n["title"] or "")[:90])

with open(f"{out}/bzp-defence-buyers.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["buyer", "nip", "city", "matched_by", "notices_6m", "cpv35_notices", "last_notice", "platforms_used", "example_titles"])
    for nip, b in sorted(buy.items(), key=lambda kv: -kv[1]["n"]):
        w.writerow([b["names"].most_common(1)[0][0], nip, b["city"], "name" if b.get("mil") else "cpv35", b["n"], b["cpv35"],
                    b["last"], " ".join(d for d, _ in b["platforms"].most_common(3)), " | ".join(b["titles"])])

print("bzp notices:", len(notices), "with platform:", sum(plat_n.values()))
print("platform domains:", len(plat_n), "multi-buyer:", sum(len(plat_buyers[d]) >= 3 for d in plat_n))
print("defence/security buyers:", len(buy), "by name:", sum(1 for b in buy.values() if b.get("mil")))
