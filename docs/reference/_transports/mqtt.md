Suits anything mains-powered that can hold a subscription: a Raspberry Pi
driving a Pimoroni Inky, an ESP32 running ESPHome with an MQTT subscription,
or a custom client written against the topic layout below. Not suitable for
a battery device that spends most of its life asleep — that is what
[`http_pull`](#http_pull) is for.

Because this is a push transport, "delivered" means the broker accepted the
publish at QoS 1 before `deliver()` returns — `MqttPublisher.publish` waits
on `wait_for_publish`, so a successful result means the broker has the
frame, not that a subscribed client has drawn it. Frames are published
retained, so a client that reconnects (after a reboot, say) gets the current
frame immediately rather than a blank panel until the next scheduled render.

Failures this transport can return, and what to do about each:

| Message | Cause | Fix |
| --- | --- | --- |
| `MQTT is not enabled. Set mqtt.enabled: true and configure the broker.` | `mqtt.enabled` is false, or the display's `transport.type` is `mqtt` without an `MqttPublisher` running. | Set `mqtt.enabled: true` and the `mqtt.host`/`mqtt.port` keys. |
| `MQTT publish to <topic> failed: <exc>` | The broker connection dropped or rejected the publish after startup. | Check the broker is still running and reachable; check `mqtt.username`/`mqtt.password` and `mqtt.tls` if the broker requires them. |
| `MQTT is not enabled` (from `probe`) | Same as above, surfaced by `maverick check`. | Same fix. |
| `broker not connected` (from `probe`) | The publisher exists but the connection has not completed (or has dropped). | Check network reachability to `mqtt.host:mqtt.port` and the broker's own logs; a slow or flapping broker can also cause `MqttPublisher.start()` to raise a timeout of its own before `deliver()` is ever reached. |
