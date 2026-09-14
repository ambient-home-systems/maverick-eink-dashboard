Suits OpenDisplay BLE e-paper tags — electronic shelf labels and similar
battery tags reflashed with OpenDisplay firmware. Two delivery paths share
this one transport: `mode: ha` hands the frame to Home Assistant's own
`opendisplay.upload_image` action, which can reach a tag through any
Bluetooth proxy Home Assistant knows about (including ESPHome Bluetooth
proxies) and works from a container with no Bluetooth hardware at all;
`mode: ble` talks to the tag directly from this host and needs a real
adapter in range of it.

This is a push transport in both modes: "delivered" means Home Assistant's
service call returned, or the BLE upload finished, before `deliver()`
returns — not that the tag has actually redrawn, which for a BLE panel
takes a further few seconds the caller does not wait for.

Failures this transport can return, and what to do about each:

| Message | Cause | Fix |
| --- | --- | --- |
| `opendisplay mode 'ha' needs a Home Assistant connection; set home_assistant.url and home_assistant.token, or use mode: ble.` | `mode: ha` (or `auto` resolving to `ha`) with no working Home Assistant client. | Set `home_assistant.url` and `home_assistant.token`, or switch to `mode: ble`. |
| `cannot write to <media_dir> (<exc>). The add-on needs the 'media:rw' mapping, or set transport.media_dir to a shared path.` | The process cannot create or write `transport.media_dir`. | Grant the add-on a `media:rw` mapping, or point `media_dir` at a directory this process can write. |
| `media_dir <media_dir> is not inside media_root <media_root>; Home Assistant can only read images from its media folder.` | `media_dir` is not a subdirectory of `media_root`. | Set `media_dir` to a path under `media_root` (default `/media`), or set `media_root` to match where `media_dir` actually is. |
| `opendisplay.upload_image failed: <exc>. Check the OpenDisplay integration is set up and device_id is correct — it is the device registry id, not the entity id or the MAC.` | The Home Assistant service call itself failed. | Confirm the OpenDisplay integration is installed and the device is online; re-check `transport.device_id` against the device registry, not an entity id or MAC. |
| `py-opendisplay is not installed. Install it with pip install 'maverick-eink-dashboard[opendisplay]', or use mode: ha to deliver through Home Assistant's Bluetooth instead.` | `mode: ble` without the optional dependency installed. | Install the `opendisplay` extra, or switch to `mode: ha`. |
| `BLE upload to <mac or device_name> failed: <exc>` | The BLE connection or upload itself failed. | Check the tag is powered and in range of this host's adapter; check `transport.encryption_key` if the tag requires one; `maverick scan` confirms whether the tag is visible at all. |
| `transport.device_id is not set` (from `probe`) | `mode: ha` (or `auto` resolving to `ha`) with no `device_id` configured. | Set `transport.device_id` to the device registry id shown in Home Assistant. |
| `py-opendisplay is not installed` (from `probe`) | `mode: ble` probed without the optional dependency. | Install the `opendisplay` extra. |
| `tag <mac> not found. In range: <listing>` (from `probe`) | A BLE scan completed but did not see the configured `mac`. | Check the tag is powered and within range; compare against the listing in the message, or re-run `maverick scan`. |
