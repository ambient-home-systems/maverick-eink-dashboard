"""Shared fixtures.

The suite never touches a browser, a broker or a real Home Assistant: every
test here drives the public surface of one component with the rest stubbed, so
`pytest -q` runs in a bare checkout.
"""

from __future__ import annotations

import pytest

from maverick.config import Config


@pytest.fixture
def config(tmp_path) -> Config:
    """A two-display config with the data directory pointed at ``tmp_path``."""
    return Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "server": {"base_url": "http://maverick.local:5000"},
            "displays": [
                {
                    "id": "kitchen",
                    "name": "Kitchen",
                    "panel": "trmnl-7in5",
                    "transport": {"type": "http_pull", "mac": "AA:BB:CC:DD:EE:FF"},
                },
                {
                    "id": "hallway",
                    "name": "Hallway",
                    "panel": "trmnl-7in5",
                    "transport": {"type": "http_pull"},
                },
            ],
        }
    )
