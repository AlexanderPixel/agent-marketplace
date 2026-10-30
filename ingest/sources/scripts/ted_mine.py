"""Pull TED notices (selected fields only) in parallel date-sliced iteration chains -> JSONL."""
import json, sys, time, datetime as dt, urllib.request, concurrent.futures as cf

API = "https://api.ted.europa.eu/v3/notices/search"
FIELDS = ["publication-number", "publication-date", "buyer-name", "buyer-country", "buyer-internet-address",
          "document-url-lot", "submission-url-lot", "legal-basis", "classification-cpv", "notice-type"]


def post(body):
    for attempt in range(12):
        try:
            req = urllib.request.Request(API, json.dumps(body).encode(), {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except Exception as e:
            wait = 15 * (attempt + 1)
            print(f"retry {attempt} in {wait}s: {e}", file=sys.stderr, flush=True)
            time.sleep(wait)
    raise RuntimeError("gave up")


def chain(query, out_path):
    body = {"query": query, "fields": FIELDS, "limit": 250, "paginationMode": "ITERATION"}
    n = 0
    with open(out_path, "w") as f:
        while True:
            d = post(body)
            for x in d.get("notices", []):
                x.pop("links", None)
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
                n += 1
            tok = d.get("iterationNextToken")
            if not tok or not d.get("notices"):
                break
            body["iterationNextToken"] = tok
    print(f"done {out_path}: {n}", flush=True)
    return n


def slices(start, end, days):
    s = start
    while s < end:
        e = min(s + dt.timedelta(days=days), end)
        yield s, e
        s = e


if __name__ == "__main__":
    job, base_q, start, end, days, out_dir = sys.argv[1:7]
    start, end = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    tasks = []
    for s, e in slices(start, end, int(days)):
        q = f"({base_q}) AND publication-date>={s:%Y%m%d} AND publication-date<{e:%Y%m%d}"
        tasks.append((q, f"{out_dir}/{job}_{s:%Y%m%d}.jsonl"))
    import os
    skip = set(os.environ.get("SKIP", "").split())
    tasks = [t for t in tasks if os.path.basename(t[1]) not in skip]

    def safe(t):
        try:
            return chain(*t)
        except Exception as e:
            print(f"FAILED {t[1]}: {e}", flush=True)
            return 0
    with cf.ThreadPoolExecutor(int(os.environ.get("THREADS", "6"))) as ex:
        total = sum(ex.map(safe, tasks))
    print(f"TOTAL {job}: {total}", flush=True)
