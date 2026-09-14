The escape hatch: any device or service with an HTTP endpoint of its own,
including other dashboard servers and vendor cloud APIs that Maverick has
no dedicated transport for.

This is a push transport: "delivered" means the HTTP request returned a
non-error status before `deliver()` returns — the far end accepted the
frame, not that a device drew it.

Failures this transport can return, and what to do about each:

| Message | Cause | Fix |
| --- | --- | --- |
| `<method> <url> returned <status>: <body>` | The endpoint responded with a 4xx or 5xx status. | Read the returned body for the endpoint's own error; check `transport.url`, `transport.method` and any required `transport.headers`. |
| `<method> <url> failed: <exc>` | The request itself failed: DNS, connection refused, TLS, or timeout. | Check `transport.url` is reachable from this host, and raise `transport.timeout` if the endpoint is simply slow. |
