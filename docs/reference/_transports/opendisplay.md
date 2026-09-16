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

In the Home Assistant app `mode: ble` is refused outright: the app asks for
no Bluetooth access (`app/config.yaml`), so no adapter is ever visible to it,
and `auto` there always resolves to `ha`. The setup UI leaves the mode out in
the app, and its tag picker fills `device_id` from Home Assistant's device
registry (`GET /api/ha/opendisplay/devices`) so the registry id is never
copied by hand. `probe()` — run by `maverick check` and the UI's *Test
delivery* — checks that id against the registry in `ha` mode and scans for
the tag in `ble` mode.

Failures this transport can return, and what to do about each:

| Message | Cause | Fix |
| --- | --- | --- |
| `opendisplay mode 'ha' needs a Home Assistant connection; set home_assistant.url and home_assistant.token, or use mode: ble.` | `mode: ha` (or `auto` resolving to `ha`) with no working Home Assistant client. | Set `home_assistant.url` and `home_assistant.token`, or switch to `mode: ble`. |
| `cannot write to <media_dir> (<exc>). The add-on needs the 'media:rw' mapping, or set transport.media_dir to a shared path.` | The process cannot create or write `transport.media_dir`. | Grant the add-on a `media:rw` mapping, or point `media_dir` at a directory this process can write. |
| `media_dir <media_dir> is not inside media_root <media_root>; Home Assistant can only read images from its media folder.` | `media_dir` is not a subdirectory of `media_root`. | Set `media_dir` to a path under `media_root` (default `/media`), or set `media_root` to match where `media_dir` actually is. |
| `opendisplay.upload_image failed: <exc>. Check the OpenDisplay integration is set up and device_id is correct — it is the device registry id, not the entity id or the MAC.` | The Home Assistant service call itself failed. | Confirm the OpenDisplay integration is installed and the device is online; re-check `transport.device_id` against the device registry, not an entity id or MAC. |
| `py-opendisplay is not installed. Install it with pip install 'maverick-eink-dashboard[opendisplay]', or use mode: ha to deliver through Home Assistant's Bluetooth instead.` | `mode: ble` without the optional dependency installed. | Install the `opendisplay` extra, or switch to `mode: ha`. |
| `BLE upload to <mac or device_name> failed: <exc>` | The BLE connection or upload itself failed. | Check the tag is powered and in range of this host's adapter; check `transport.encryption_key` if the tag requires one; `maverick scan` confirms whether the tag is visible at all. |
| `opendisplay mode 'ble' is not available in the Home Assistant app: it has no Bluetooth of its own. Use mode: ha, which delivers through Home Assistant's Bluetooth and its ESPHome proxies.` | `mode: ble` inside the app, where no adapter is visible. | Use `mode: ha`, or leave `mode` unset. |
| `transport.device_id is not set` (from `probe`) | `mode: ha` (or `auto` resolving to `ha`) with no `device_id` configured. | Pick the tag in the setup UI, or set `transport.device_id` to the device registry id shown in Home Assistant. |
| `device_id '<id>' is not a device of the OpenDisplay integration in Home Assistant; pick the tag from the list in the setup UI, or copy the id from the device page URL.` (from `probe`) | The id is not one the OpenDisplay integration owns — usually an entity id or a MAC pasted in its place. | Pick the tag from the picker, which writes the registry id. |
| `neither transport.mac nor transport.device_name is set; scan for the tag from the setup UI or with `maverick scan`` (from `probe`) | `mode: ble` with nothing to look for. | Scan from the setup UI on a host with an adapter, or run `maverick scan`, and set `mac`. |
| `py-opendisplay is not installed` (from `probe`) | `mode: ble` probed without the optional dependency. | Install the `opendisplay` extra. |
| `tag <mac or device_name> not found. In range: <listing>` (from `probe`) | A BLE scan completed but did not see the configured `mac` or `device_name`. | Check the tag is powered and within range; compare against the listing in the message, or re-run `maverick scan`. |
