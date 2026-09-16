`maverick` is one binary with a subcommand per task. Most of them exist so
that tuning a dashboard for e-ink does not require a physical panel: render a
frame to a PNG, look at it, adjust the dashboard or the config, and repeat.

## The recommended iteration loop

```text
maverick render kitchen --no-deliver -o out/
# open out/kitchen.png and look at it
# tune the dashboard or displays[].theme/image in config.yaml
maverick check
maverick serve
```

`render --no-deliver -o` renders one display straight to a PNG without
touching a transport, so a bad theme or a broken selector never reaches a
real panel. Once the frame looks right, `check` confirms the config loads,
the panel and transport resolve, and Home Assistant and MQTT (if configured)
are reachable — and asks each enabled display's transport whether a delivery
would arrive (`Transport.probe`: for an OpenDisplay tag in `ha` mode, that
Home Assistant's device registry knows the `device_id`; in `ble` mode, that a
scan hears the tag; `--no-probe` skips it) — all without rendering anything.
`serve` is what actually runs the service: the HTTP server, the scheduler
and the Home Assistant listeners together.
