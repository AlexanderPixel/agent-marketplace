"""Pull Polish BZP contract notices day by day; keep buyer + CPV + the platform URL running the tender."""
import json, re, sys, time, datetime as dt, urllib.request, concurrent.futures as cf

API = "https://ezamowienia.gov.pl/mo-board/api/v1/notice"
PLATFORM = re.compile(r"Adres strony internetowej prowadzonego postępowania</h3>\s*(?:<[^>]+>\s*)*(\S+?)\s*<")


def get(params):
    url = API + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    for attempt in range(10):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except Exception as e:
            print(f"retry {attempt}: {e}", file=sys.stderr, flush=True)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("gave up " + url)


def day(d, out_dir):
    params = {"NoticeType": "ContractNotice", "PageSize": 500,
              "PublicationDateFrom": f"{d:%Y-%m-%d}T00:00:00", "PublicationDateTo": f"{d + dt.timedelta(days=1):%Y-%m-%d}T00:00:00"}
    n = 0
    with open(f"{out_dir}/bzp_{d:%Y%m%d}.jsonl", "w") as f:
        while True:
            page = get(params)
            for x in page:
                m = PLATFORM.search(x.get("htmlBody") or "")
                f.write(json.dumps({
                    "bzp": x["bzpNumber"], "date": x["publicationDate"][:10], "buyer": x["organizationName"],
                    "buyer_nip": x["organizationNationalId"], "city": x["organizationCity"], "province": x["organizationProvince"],
                    "cpv": x["cpvCode"], "title": x["orderObject"], "order_type": x["orderType"],
                    "platform_url": m.group(1) if m else None}, ensure_ascii=False) + "\n")
                n += 1
            if len(page) < params["PageSize"]:
                break
            params["SearchAfter"] = page[-1]["objectId"]
    print(f"done {d}: {n}", flush=True)
    return n


if __name__ == "__main__":
    start, end, out_dir = dt.date.fromisoformat(sys.argv[1]), dt.date.fromisoformat(sys.argv[2]), sys.argv[3]
    days = [start + dt.timedelta(days=i) for i in range((end - start).days)]

    def safe(d):
        try:
            return day(d, out_dir)
        except Exception as e:
            print(f"FAILED {d}: {e}", flush=True)
            return 0
    with cf.ThreadPoolExecutor(3) as ex:
        print("TOTAL bzp:", sum(ex.map(safe, days)), flush=True)
