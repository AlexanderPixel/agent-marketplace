"""Builds the compact demo-data/ folder the API loads on start, from the raw pull in the project files.

Usage: python3 tools/prepare_demo_data.py <raw demo-data dir> <repo demo-data dir>
Raw data: TED Search API eForms fields, e-Zamówienia BZP notices, eCertis. See demo-data/SOURCES.md.
"""
import glob
import html
import json
import os
import re
import shutil
import sys

raw, out = sys.argv[1], sys.argv[2]
os.makedirs(f"{out}/ted/notices", exist_ok=True)
os.makedirs(f"{out}/bzp", exist_ok=True)

for path in [
    "ted/baainbw-leaf-springs/723644-2026-eforms-fields.json",
    "ted/pl-defence/604127-2026-eforms-fields.json",
    "ted/pl-defence/609937-2026-eforms-fields.json",
]:
    shutil.copy(f"{raw}/{path}", f"{out}/ted/notices/{os.path.basename(path).replace('-eforms-fields', '')}")
shutil.copy(f"{raw}/ted/pl-defence/pl-defence-contract-notices-sep-oct-2026.json", f"{out}/ted/pl-defence-notices.json")
if os.path.isdir(f"{raw}/ted/prime-awards"):
    shutil.copy(f"{raw}/ted/prime-awards/rheinmetall-knds-pgz-awards-2026.json", f"{out}/ted/prime-awards.json")
if os.path.isdir(f"{raw}/ecertis"):
    shutil.copytree(f"{raw}/ecertis", f"{out}/ecertis", dirs_exist_ok=True)


def text(body: str) -> str:
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", body)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", body)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    lines = [re.sub(r"\s+", " ", line).strip() for line in body.split("\n")]
    return "\n".join(line for line in lines if line)[:20000]


notices = {}
for path in glob.glob(f"{raw}/ezamowienia/contract-notices-*.json"):
    for n in json.load(open(path)):
        n.pop("htmlBody", None)
        notices[n["noticeNumber"]] = n
for path in glob.glob(f"{raw}/ezamowienia/notices/*.json"):
    n = json.load(open(path))
    body_path = path[:-5] + ".html"
    if os.path.exists(body_path):
        n["text"] = text(open(body_path, encoding="utf-8").read())
    n.pop("htmlBody", None)
    notices[n["noticeNumber"]] = n
json.dump(list(notices.values()), open(f"{out}/bzp/notices.json", "w"), ensure_ascii=False)
print(f"{len(notices)} BZP notices")
