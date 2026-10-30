"""Brief extractors are tested against the captured example tender VM191 (BAIUDBw catheters, e-Vergabe 900460)."""

from pathlib import Path

import pytest

from tenderhub.brief import TenderContext, load_fixture_context

FIXTURE = Path(__file__).parent.parent / "fixtures" / "vm191"


@pytest.fixture(scope="session")
def ctx() -> TenderContext:
    return load_fixture_context(FIXTURE)
