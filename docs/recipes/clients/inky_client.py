#!/usr/bin/env python3
"""Draw Maverick frames on a Pimoroni Inky attached to a Raspberry Pi.

Subscribes to the two topics the `mqtt` transport publishes for one display —
`<base>/meta` (JSON) and `<base>/frame` (the frame bytes, PNG by default) —
and redraws the panel when the frame changes.

    pip install paho-mqtt pillow inky
    python inky_client.py --host mqtt.local --display kitchen

Untested on an Inky: see docs/recipes/inky-mqtt.md for what was and was not
verified.
"""

import argparse
import io
import json

import paho.mqtt.client as mqtt
from inky.auto import auto
from PIL import Image

parser = argparse.ArgumentParser(description="Draw Maverick frames on a Pimoroni Inky.")
parser.add_argument("--host", required=True, help="MQTT broker host")
parser.add_argument("--port", type=int, default=1883)
parser.add_argument("--username")
parser.add_argument("--password")
parser.add_argument("--display", required=True, help="Maverick display id")
parser.add_argument("--base-topic", default="maverick", help="mqtt.base_topic")
args = parser.parse_args()

base = f"{args.base_topic}/display/{args.display}"
panel = auto()  # detects the Inky model over I2C
state = {"meta": None, "drawn": None}


def draw(payload: bytes, meta: dict) -> None:
    image = Image.open(io.BytesIO(payload)).convert("RGB")
    if image.size != (panel.width, panel.height):
        image = image.resize((panel.width, panel.height))
    panel.set_image(image)  # the library maps it onto the panel's own inks
    panel.show()  # always a full update on an Inky; there is no partial mode
    state["drawn"] = meta["checksum"]
    print(f"drew {meta['checksum']} ({meta['width']}x{meta['height']}, {meta['format']})")


def on_meta(_client, _userdata, message) -> None:
    state["meta"] = json.loads(message.payload)


def on_frame(_client, _userdata, message) -> None:
    meta = state["meta"]
    if meta is None or meta["bytes"] != len(message.payload):
        return  # the retained pair has not caught up yet; wait for the next one
    if meta["checksum"] == state["drawn"] and not meta["full_refresh"]:
        return  # already on the panel, and nothing asked for a ghost-clearing redraw
    draw(message.payload, meta)


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if args.username:
    client.username_pw_set(args.username, args.password)
client.message_callback_add(f"{base}/meta", on_meta)
client.message_callback_add(f"{base}/frame", on_frame)
client.on_connect = lambda c, *_: c.subscribe([(f"{base}/meta", 1), (f"{base}/frame", 1)])
client.connect(args.host, args.port, 60)
client.loop_forever()
