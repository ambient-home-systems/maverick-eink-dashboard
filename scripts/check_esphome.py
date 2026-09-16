#!/usr/bin/env python3
"""Validate the generated ESPHome configuration against ESPHome itself.

For every panel in the catalogue that names an ``esphome_model``
(``src/maverick/devices/panels.yaml``), this builds a one-display ``Config``,
runs ``generate_esphome_config`` over it (``src/maverick/esphome/generator.py``),
writes the result beside a ``secrets.yaml`` with placeholder values, and runs
``esphome config`` on it. ``esphome config`` validates the whole document
against every component's schema — the model name, the pin keys, the
``online_image`` options — without a toolchain and without a device, which is
exactly the check a user used to do at their own desk, after the fact.

It is not a flash, and not a compile: nothing here proves the panel draws.
What it proves is that the file Maverick hands out is one ESPHome accepts.

Two variants are exercised per panel: the plain one, and one with
``server.api_token`` set and ``esphome.deep_sleep`` on, which are the two
branches of the generator that add blocks the plain document lacks.

Usage::

    pip install esphome
    python scripts/check_esphome.py            # every panel with a model
    python scripts/check_esphome.py waveshare-7in5-mono

Exit status is 1 when any configuration fails validation, with ESPHome's own
report printed for each failure.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from maverick.config import Config  # noqa: E402
from maverick.devices import all_panels  # noqa: E402
from maverick.esphome import generate_esphome_config  # noqa: E402

#: Placeholder secrets. Every name the generator references has to resolve
#: for `esphome config` to get as far as the schema.
SECRETS = """\
wifi_ssid: "check"
wifi_password: "check-password"
api_key: "b7bJ3p0jc5q8b8v5kYc9Y6c1x3Zc1YbJ3p0jc5q8b8s="
maverick_authorization: "Bearer check-token"
"""


def _config(panel_id: str, *, token: bool, deep_sleep: bool) -> Config:
    return Config.model_validate(
        {
            "server": {
                "base_url": "http://maverick.local:5000",
                "api_token": "check-token" if token else "",
            },
            "displays": [
                {
                    "id": "check",
                    "name": "Check panel",
                    "panel": panel_id,
                    "schedule": {"every": "5m"},
                    "transport": {"type": "http_pull"},
                    "esphome": {"deep_sleep": deep_sleep},
                }
            ],
        }
    )


def _validate(esphome: str, directory: Path, name: str, document: str) -> str | None:
    """Run `esphome config`; return its output on failure, None on success."""
    path = directory / f"{name}.yaml"
    path.write_text(document, encoding="utf-8")
    result = subprocess.run(
        [esphome, "config", path.name],
        cwd=directory,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return None
    return (result.stdout + result.stderr).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "panels", nargs="*", help="panel ids to check (default: every one with a model)"
    )
    parser.add_argument(
        "--esphome", default=shutil.which("esphome"), help="path to the esphome CLI"
    )
    args = parser.parse_args(argv)

    if not args.esphome:
        print(
            "check_esphome: the `esphome` CLI is not on PATH; pip install esphome",
            file=sys.stderr,
        )
        return 2

    panels = [p for p in all_panels() if p.esphome_model]
    if args.panels:
        wanted = set(args.panels)
        panels = [p for p in panels if p.id in wanted]
        missing = wanted - {p.id for p in panels}
        if missing:
            names = ", ".join(sorted(missing))
            print(f"check_esphome: no catalogued panel with a model: {names}")
            return 2

    failures = 0
    with tempfile.TemporaryDirectory(prefix="maverick-esphome-") as tmp:
        directory = Path(tmp)
        (directory / "secrets.yaml").write_text(SECRETS, encoding="utf-8")
        for panel in panels:
            variants = (("plain", False, False), ("token-sleep", True, True))
            for variant, token, deep_sleep in variants:
                config = _config(panel.id, token=token, deep_sleep=deep_sleep)
                document = generate_esphome_config(config.display("check").resolved(), config)
                report = _validate(args.esphome, directory, f"{panel.id}-{variant}", document)
                platform = panel.esphome_platform or "waveshare_epaper"
                label = f"{panel.id} [{variant}] ({platform}: {panel.esphome_model})"
                if report is None:
                    print(f"ok    {label}")
                else:
                    failures += 1
                    print(f"FAIL  {label}\n{report}\n")
    print(f"{len(panels)} panel(s), {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
