"""Command line interface.

``maverick serve`` is the one most people run — it starts the HTTP server, the
scheduler and the Home Assistant listeners together. The rest exist because
setting up an e-ink dashboard is iterative, and being able to render one frame
to a PNG and look at it beats deploying and walking to the panel.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .config import Config, ConfigError, load_config

DEFAULT_CONFIG_PATHS = [
    Path("config.yaml"),
    Path("/config/maverick.yaml"),
    Path("/data/options.json"),
    Path.home() / ".config" / "maverick" / "config.yaml",
]


def _find_config(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise ConfigError(f"config file not found: {path}")
        return path
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return candidate
    raise ConfigError(
        "No config file found. Looked in: "
        + ", ".join(str(p) for p in DEFAULT_CONFIG_PATHS)
        + ". Run `maverick init > config.yaml` to create one."
    )


def _load(args: argparse.Namespace) -> Config:
    path = _find_config(getattr(args, "config", None))
    if path.name == "options.json":
        # Home Assistant add-on: options.json is the add-on's own schema, which
        # the add-on's run script has already translated. Reaching here means
        # that translation did not happen.
        raise ConfigError(
            "Found /data/options.json but no maverick.yaml. The add-on should "
            "have generated one; check the add-on log."
        )
    config = load_config(path)
    if getattr(args, "log_level", None):
        config.log_level = args.log_level
    return config


# --------------------------------------------------------------- commands --

def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .app import Application
    from .logging_setup import setup_logging
    from .server import create_app

    config = _load(args)
    setup_logging(config.log_level)

    application = Application(config)
    api = create_app(application)
    uvicorn.run(
        api,
        host=args.host or config.server.host,
        port=args.port or config.server.port,
        log_config=None,
        access_log=False,
    )
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    from .app import Application
    from .logging_setup import setup_logging

    config = _load(args)
    setup_logging(args.log_level or config.log_level)

    async def run() -> int:
        application = Application(config)
        # No scheduler: this is a one-shot render.
        await application.start(schedule=False)
        try:
            targets = [args.display] if args.display else [d.id for d in config.enabled_displays]
            failures = 0
            for display_id in targets:
                outcome = await application.engine.render(
                    display_id,
                    trigger="cli",
                    force=args.force,
                    deliver=not args.no_deliver,
                )
                print(outcome.describe())
                if outcome.frame is not None:
                    for issue in outcome.frame.lint.issues:
                        print(f"    {issue.severity.value:8s} {issue.code}: {issue.message}")
                        if issue.hint and args.verbose:
                            print(f"             {issue.hint}")
                    if args.out:
                        target = Path(args.out)
                        # Treat --out as a directory when it plainly is one: more
                        # than one display to write, an existing directory, a
                        # trailing separator, or no file extension for Pillow to
                        # infer a format from.
                        if (
                            len(targets) > 1
                            or target.is_dir()
                            or str(args.out).endswith(("/", os.sep))
                            or not target.suffix
                        ):
                            target.mkdir(parents=True, exist_ok=True)
                            target = target / f"{display_id}.png"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        outcome.frame.preview.save(target)
                        print(f"    wrote {target}")
                if not outcome.ok and not outcome.skipped:
                    failures += 1
            return 1 if failures else 0
        finally:
            await application.stop()

    return asyncio.run(run())


def cmd_check(args: argparse.Namespace) -> int:
    """Validate config and connectivity without rendering anything."""
    from .ha import HomeAssistantClient
    from .logging_setup import setup_logging
    from .transports import get_transport

    config = _load(args)
    setup_logging(config.log_level)
    problems = 0

    # Which file the displays came from matters as soon as there are two that
    # could have supplied them (`resolve_displays` in `src/maverick/store.py`).
    source = f" from {config.displays_source}" if config.displays_source else ""
    print(f"config: {len(config.displays)} display(s){source}")
    for display in config.displays:
        resolved = display.resolved()
        flag = " " if display.enabled else "-"
        # A display with pages has no single `dashboard` worth printing: the
        # attribute still holds its default, which would read as the thing the
        # panel shows (`DisplayConfig.pages`, `src/maverick/config.py`).
        if display.pages:
            pages = ", ".join(f"{p.name} ({p.dashboard})" for p in display.pages)
            what = f"      pages: {pages}" + (" [rotating]" if display.rotate else "")
        else:
            what = f"      dashboard: {display.dashboard}"
        print(
            f"  {flag} {display.id:16s} {resolved.profile.name}\n"
            f"      {resolved.width}x{resolved.height} {resolved.color_scheme.value} "
            f"{resolved.dpi}dpi rot{resolved.rotation} -> {display.transport.type}\n"
            f"{what}"
        )
        try:
            get_transport(display.transport.type, {})
        except KeyError as exc:
            print(f"      ERROR: {exc}")
            problems += 1

    async def check_ha() -> None:
        nonlocal problems
        if not config.home_assistant.has_credentials:
            print("home assistant: no token configured — rendering will fail")
            problems += 1
            return
        client = HomeAssistantClient(config.home_assistant)
        try:
            info = await client.check()
            kind = client.tokens.kind if client.tokens else "?"
            print(
                f"home assistant: ok ({config.home_assistant.url}, "
                f"{info.get('version','?')}, via {kind})"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"home assistant: FAILED — {exc}")
            problems += 1
        finally:
            await client.close()

    asyncio.run(check_ha())

    if config.mqtt.enabled:
        print(f"mqtt: configured for {config.mqtt.host}:{config.mqtt.port}")
    else:
        print("mqtt: disabled (displays will not appear as Home Assistant entities)")

    print("OK" if not problems else f"{problems} problem(s) found")
    return 1 if problems else 0


def cmd_panels(args: argparse.Namespace) -> int:
    from .devices import panels_by_vendor

    if args.json:
        from .devices import all_panels

        print(json.dumps([p.__dict__ | {"color_scheme": p.color_scheme.value}
                          for p in all_panels()], indent=2, default=str))
        return 0
    for vendor, panels in panels_by_vendor().items():
        print(f"\n{vendor}")
        for panel in panels:
            partial = "partial" if panel.supports_partial else "full-only"
            print(
                f"  {panel.id:32s} {panel.width:>5}x{panel.height:<5} "
                f"{panel.color_scheme.value:9s} {panel.dpi:>4}dpi  {partial}"
            )
            if args.verbose and panel.notes:
                print(f"    {panel.notes}")
    return 0


def cmd_transports(args: argparse.Namespace) -> int:
    from .transports import available_transports

    for name, cls in sorted(available_transports().items()):
        mode = "push" if cls.pushes else "pull"
        print(f"  {name:14s} [{mode}] {cls.description}")
    return 0


def cmd_esphome(args: argparse.Namespace) -> int:
    from .esphome import generate_esphome_config

    config = _load(args)
    display = config.display(args.display).resolved()
    text = generate_esphome_config(display, config)
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Discover OpenDisplay BLE tags in range."""
    from .logging_setup import setup_logging

    setup_logging(args.log_level or "info")

    async def run() -> int:
        try:
            from .transports.opendisplay import scan
        except ImportError:
            print("py-opendisplay is not installed. Install the 'opendisplay' extra.")
            return 1
        try:
            found = await scan(timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"BLE scan failed: {exc}")
            print(
                "A local Bluetooth adapter is required for scanning. If you run "
                "Maverick as an add-on without Bluetooth, use the OpenDisplay "
                "integration in Home Assistant and transport mode: ha instead."
            )
            return 1
        if not found:
            print("No OpenDisplay tags found. Check they are powered and in range.")
            return 1
        print(f"Found {len(found)} tag(s):")
        for name, mac in sorted(found.items()):
            print(f"  {mac}  {name}")
        print("\nAdd one to your config:")
        first_name, first_mac = sorted(found.items())[0]
        print(
            f"  - id: my-tag\n"
            f"    panel: opendisplay-solum-2in6-bwr   # pick your model\n"
            f"    dashboard: /lovelace-eink/tag\n"
            f"    transport:\n      type: opendisplay\n      mode: ble\n"
            f"      mac: \"{first_mac}\""
        )
        return 0

    return asyncio.run(run())


def cmd_init(args: argparse.Namespace) -> int:
    """Print a starter config."""
    from importlib import resources

    try:
        text = resources.files("maverick").joinpath("config.example.yaml").read_text()
    except (FileNotFoundError, ModuleNotFoundError):
        fallback = Path(__file__).resolve().parent.parent.parent / "config.example.yaml"
        text = fallback.read_text() if fallback.exists() else _MINIMAL_CONFIG
    print(text)
    return 0


_MINIMAL_CONFIG = """\
home_assistant:
  url: http://homeassistant.local:8123
  token: ${HA_TOKEN}

server:
  base_url: http://maverick.local:5000

displays:
  - id: kitchen
    panel: waveshare-7in5-mono
    dashboard: /lovelace-eink/kitchen
    schedule:
      every: 5m
    transport:
      type: http_pull
"""


# ------------------------------------------------------------------ parser --

def build_parser() -> argparse.ArgumentParser:
    from .app import VERSION

    parser = argparse.ArgumentParser(
        prog="maverick",
        description="Render Home Assistant dashboards to e-ink displays.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {VERSION}"
    )
    parser.add_argument("-c", "--config", help="path to config.yaml")
    parser.add_argument(
        "--log-level", choices=["debug", "info", "warning", "error"], default=None
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the server, scheduler and triggers")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.set_defaults(func=cmd_serve)

    render = sub.add_parser("render", help="render now (all displays, or one)")
    render.add_argument("display", nargs="?", help="display id; omit for all")
    render.add_argument("-o", "--out", help="also save the frame as a PNG here")
    render.add_argument("--force", action="store_true", help="ignore unchanged/lint gates")
    render.add_argument("--no-deliver", action="store_true", help="render but do not send")
    render.set_defaults(func=cmd_render)

    check = sub.add_parser("check", help="validate config and connectivity")
    check.set_defaults(func=cmd_check)

    panels = sub.add_parser("panels", help="list supported panels")
    panels.add_argument("--json", action="store_true")
    panels.set_defaults(func=cmd_panels)

    transports = sub.add_parser("transports", help="list available transports")
    transports.set_defaults(func=cmd_transports)

    esphome = sub.add_parser("esphome", help="generate an ESPHome config for a display")
    esphome.add_argument("display")
    esphome.add_argument("-o", "--out")
    esphome.set_defaults(func=cmd_esphome)

    scan = sub.add_parser("scan", help="discover OpenDisplay BLE tags in range")
    scan.add_argument("--timeout", type=float, default=10.0)
    scan.set_defaults(func=cmd_scan)

    init = sub.add_parser("init", help="print a starter config")
    init.set_defaults(func=cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if os.environ.get("MAVERICK_DEBUG"):
        return args.func(args)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("(set MAVERICK_DEBUG=1 for a traceback)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
