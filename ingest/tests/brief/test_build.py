"""The brief builds from the example tender, every section runs, and every item is cited to a real source."""

from tenderhub.brief import SECTIONS, build


def test_brief_builds_with_citations(ctx):
    brief = build(ctx)
    assert brief["errors"] == {}
    assert brief["summary"]["documents"] == 30 and brief["summary"]["latest_version"] == 3
    files = {d.filename for d in ctx.docs} | {p["url"] for p in ctx.pages}
    for name in SECTIONS:
        for item in brief[name]:
            assert item.get("cite"), f"{name}: item without citation: {item}"
            assert item["cite"]["file"] in files, f"{name}: cites unknown file {item['cite']['file']!r}"
