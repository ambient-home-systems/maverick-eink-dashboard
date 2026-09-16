"""Gate for the whole `browser` suite.

Every test under here drives a real Chromium, unlike the rest of the suite
(`tests/conftest.py`), so two things happen only in this directory: every
collected item is tagged with the `browser` marker (`pyproject.toml` excludes
it from a plain `pytest -q`), and the directory is skipped — not failed — when
no Chromium can be launched. "Can be launched" is decided by actually asking
`BrowserPool` to start one, the same object and the same rule a real render
uses (`src/maverick/render/browser.py` lines 44 to 62): an explicit
`MAVERICK_CHROMIUM_PATH`, or a Chromium Playwright downloaded itself.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

HERE = str(Path(__file__).parent)


def _chromium_unavailable_reason() -> str | None:
    from maverick.render.browser import BrowserPool

    async def _probe() -> None:
        pool = BrowserPool(max_concurrent=1)
        try:
            await pool.start()
        finally:
            await pool.stop()

    try:
        asyncio.run(_probe())
    except RuntimeError as exc:
        return str(exc)
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if str(item.fspath).startswith(HERE):
            item.add_marker(pytest.mark.browser)


@pytest.fixture(scope="session", autouse=True)
def _require_chromium() -> None:
    reason = _chromium_unavailable_reason()
    if reason is not None:
        pytest.skip(f"tests/browser/ needs a Chromium: {reason}")
