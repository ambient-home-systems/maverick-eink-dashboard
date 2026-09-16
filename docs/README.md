# Maverick documentation

Every page in this repository, grouped by what you came here to do. Pages under
[`reference/`](reference/) are generated from the code by
`scripts/gen_docs.py` and are never edited by hand; everything else is written
by a person.

## Start here

| Page | What it is |
| --- | --- |
| [README](../README.md) | What Maverick is, what it does not do yet, install, configuration, transports, the HTTP API, running it under systemd. |
| [Quick start](../README.md#quick-start) | Seven steps from `maverick init` to a rendered PNG you can look at before any panel is involved. |
| [`config.example.yaml`](../config.example.yaml) | The commented worked example: two displays, one pulling on a timer with quiet hours, one BLE tag rendering on entity change. |
| [Home Assistant app](../app/DOCS.md) | Installing from the app store on Home Assistant OS or Supervised: the options, what the app maps and exposes, the starter config it writes, and what to check when it does not start. |

## Reference — generated

| Page | What it is |
| --- | --- |
| [Configuration](reference/configuration.md) | Every config key, its type, default and what the code does with it, from the pydantic models. |
| [Panel catalogue](reference/panels.md) | Every panel id and what it sets: resolution, colour scheme, dpi, refresh behaviour, default transport. |
| [Transports](reference/transports.md) | Every transport, its options, and the failure messages it can produce. |
| [CLI](reference/cli.md) | Every command and flag, with the environment variables the CLI reads. |
| [HTTP API](reference/http-api.md) | Every route in prose — parameters, responses, status codes, and the full pull protocol a battery panel needs. |
| [MQTT](reference/mqtt.md) | The topic tree, the payloads, the command words and the Home Assistant discovery entities. |
| [`openapi.json`](reference/openapi.json) | FastAPI's own schema for the routes, generated against an empty config. |

## Guides

| Page | What it is |
| --- | --- |
| [Home Assistant](guides/home-assistant.md) | The setup UI — adding, editing and previewing a display — making the token, getting the URL rule right, MQTT discovery, and rendering when your data changes rather than on a timer. |
| [Troubleshooting](troubleshooting.md) | The failures people actually hit, each keyed to the message Maverick prints. |

## Recipes

One page per way of getting a frame onto a panel. None has been run on
hardware; each carries a banner and says what was verified.

| Page | Device | Transport |
| --- | --- | --- |
| [Recipes index](recipes/README.md) | Which recipe is mine, and the push/pull question that decides it. | — |
| [ESPHome and Waveshare](recipes/esphome-waveshare.md) | An ESP32 wired to a Waveshare e-paper module | `http_pull` |
| [OpenDisplay tags](recipes/opendisplay-tags.md) | Shelf labels reflashed with OpenDisplay firmware | `opendisplay` |
| [Kindle and Kobo](recipes/kindle-kobo.md) | A jailbroken e-reader polling for PNGs | `http_pull`, `file` |
| [TRMNL](recipes/trmnl.md) | A TRMNL 7.5" panel in bring-your-own-server mode | `http_pull` |
| [Pi and Pimoroni Inky](recipes/inky-mqtt.md) | A Raspberry Pi holding an MQTT subscription | `mqtt` |
| [Webhook and file](recipes/webhook-and-file.md) | Anything else: a directory, or an HTTP endpoint of your own | `file`, `webhook` |
| [`clients/frame_poll.sh`](recipes/clients/frame_poll.sh) | A POSIX-shell poller for an e-reader: `curl`, an ETag on disk, `fbink`. | — |
| [`clients/inky_client.py`](recipes/clients/inky_client.py) | An MQTT client for a Pimoroni Inky: `paho-mqtt`, Pillow, `inky`. | — |

## Design

Maverick renders a dashboard you already have, and the one you already have was
built for a phone. Making a dashboard for the panel is its own problem, and
these are the two ways into it: the guide explains the mechanism, and
`maverick dashboard <id>` hands you a layout sized for your panel to start
from.

| Page | What it is |
| --- | --- |
| [Building a dashboard for e-ink](guides/home-assistant.md#building-a-dashboard-for-e-ink) | The short version: the generator, and the three rules that decide everything else. Start here. |
| [The e-ink design guide](design-guide.md) | How to build a dashboard that survives quantisation: contrast, type size in millimetres, what dithering does to a card, what to remove. |

## Project

| Page | What it is |
| --- | --- |
| [Architecture](architecture.md) | How the service works today, component by component, and what is planned rather than built. |
| [Roadmap](roadmap.md) | The add-on and integration, pages and a control surface, the dashboard strategy and live preview. |
| [Contributing](../CONTRIBUTING.md) | Setup, the checks, the documentation workflow, adding a panel or a transport, the untested-banner rule, the pull request checklist. |
| [`CLAUDE.md`](../CLAUDE.md) | The short version for AI-assisted changes: module map, invariants, documentation rules, the commands that verify a change. |
| [Documentation plan](documentation-plan.md) | The review that produced this documentation set, and the phases it was written in. |
| [Implementation plan](implementation-plan.md) | The review of the product layer, and the phased prompts, with a model and effort level each, that build it: runtime display configuration, the setup UI, pages, the dev loop. |
| [Changelog](../CHANGELOG.md) | What exists, by module, in Keep a Changelog format. |
