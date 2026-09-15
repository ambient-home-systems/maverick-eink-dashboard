# The `file` and `webhook` transports

*Last reviewed against commit `d103e74`.*

> **Not run on hardware by this project** — but these two are the least
> hardware-dependent paths there are, and most of what is below was exercised
> directly. See [What was verified](#what-was-verified).

These are the two escape hatches. Neither knows anything about panels: one
writes the frame to a directory, the other POSTs it somewhere. Everything else
is your problem, which is the point.

1. [`file`](#file)
2. [`webhook`](#webhook)
3. [What was verified](#what-was-verified)
4. [What to check first when it does not work](#what-to-check-first-when-it-does-not-work)

## `file`

For anything that reads a file: a script on a Raspberry Pi, a Samba share, a
directory another web server serves, an rsync to a jailbroken e-reader
([recipe](kindle-kobo.md#the-file-transport-for-rsync-instead-of-polling)), or
just looking at the output while you tune a theme.

```yaml
displays:
  - id: kitchen
    panel: waveshare-4in2-mono
    transport:
      type: file
      path: /srv/maverick/frames
      filename: kitchen.png
      write_preview: true
```

| Option | Default | What it does |
| --- | --- | --- |
| `path` | `./out` | Directory to write into, created if it does not exist — including parents. |
| `filename` | `<display id>.<ext>` | The frame file's name. The default extension comes from the frame format: `.png`, `.bmp`, or `.bin` for a raw layout (`packed`, `planes`, `indexed`). |
| `write_preview` | `false` | Also write `<display id>-preview.png`, a viewable render of the quantised frame. |

Three things are worth knowing.

**The write is atomic.** The payload goes to `<target><suffix>.tmp` and is then
renamed over the target:

```python
temporary = target.with_suffix(target.suffix + ".tmp")
temporary.write_bytes(frame.payload)
temporary.replace(target)
```

`Path.replace` is an atomic rename within a filesystem, so a reader polling the
directory sees either the old frame or the new one and never a half-written
file. It also means you can point something dumb — a `while true` loop with
`fbink` — at that path without a lock.

**`write_preview` ignores `filename`.** The preview is always
`<display id>-preview.png`, so with `filename: kitchen.png` for a display called
`kitchen` you get `kitchen.png` and `kitchen-preview.png`, but with
`filename: dashboard.png` you get `dashboard.png` and still
`kitchen-preview.png`. The preview is a debugging artefact, not a second
delivery.

**"Delivered" means written.** This is a push transport: `deliver()` returns
once the bytes are on disk. Whether anything downstream picked them up is
outside what the transport can know, so a successful delivery here says less
than a successful delivery over MQTT does.

## `webhook`

For any device or service with an HTTP endpoint of its own — another dashboard
server, a vendor cloud API, a small script of your own.

```yaml
displays:
  - id: kitchen
    panel: waveshare-7in5-mono
    transport:
      type: webhook
      url: https://panel.example.com/frames
      method: POST
      timeout: 30
      headers:
        Authorization: Bearer abc123
```

| Option | Default | What it does |
| --- | --- | --- |
| `url` | *required* | Where the frame is sent. Missing or empty raises `transport 'webhook' requires the 'url' option to be set`. |
| `method` | `POST` | Upper-cased before use, so `put` works. |
| `headers` | `{}` | Extra request headers, merged over the three below. |
| `timeout` | `30` | Seconds, applied to the whole request. |

The frame is the raw request body — no multipart, no base64, no JSON envelope.
Three headers describe it, each set with `setdefault`, so anything you put in
`headers` wins:

| Header | Value |
| --- | --- |
| `Content-Type` | `image/png`, `image/bmp`, or `application/octet-stream` for a raw layout. |
| `X-Maverick-Display` | The display id. |
| `X-Maverick-Checksum` | The frame's sixteen-hex-character checksum — the same identity the pull endpoint puts in its `ETag`. |

That checksum is what makes a receiver able to behave like a proper e-ink
client: store the last one, compare, and skip the refresh when it matches. It
is the only piece of the conditional-GET machinery that survives being pushed
rather than pulled.

A response of 400 or above is a failure, and the first 200 characters of the
body come back in the message — so if your endpoint explains itself, you will
see the explanation in Maverick's log.

## What was verified

Checked on this repository, with a live `maverick serve`:

* A display with `transport: file`, `path`, and `write_preview: true` wrote both
  `<id>.png` and `<id>-preview.png` into the configured directory, and the frame
  file was a PNG of the display's geometry.
* Every option name, default and message above is read from
  `transports/pull.py` and appears in
  [the transport reference](../reference/transports.md#file).

Not checked: the `webhook` transport against a real endpoint — no request was
sent — and the `.tmp` rename was read rather than raced against a reader.

## What to check first when it does not work

Both transports are push transports, so a failure is returned by `deliver()`
and appears in the log as `[<id>] FAILED: <detail>`, in the MQTT state topic's
`error` key, and in the `delivery` field of a render response.

### `file`

| Message | What to do |
| --- | --- |
| `could not write /srv/maverick/frames: [Errno 13] Permission denied` | The user running Maverick cannot create or write the directory. Check ownership; in a container, check the mount is read-write. |
| `could not write …: [Errno 28] No space left on device` | Exactly what it says. |
| `could not write …: [Errno 20] Not a directory` | `path` names an existing file, or a parent of it does. |
| Nothing is written and there is no error | The render was skipped, not failed — an unchanged frame, quiet hours, or a lint block. The log line says `skipped:` and the reason. |
| The file exists but is stale | `schedule.skip_unchanged` means an identical frame is not re-delivered. `POST /api/displays/<id>/render?force=true` proves the path end to end. |

The `.tmp` file is only left behind if the process dies mid-write; it is
harmless and the next delivery overwrites it.

### `webhook`

| Message | What to do |
| --- | --- |
| `transport 'webhook' requires the 'url' option to be set` *(raised, not returned)* | Add `url`. This one is an exception rather than a delivery failure, so it surfaces as a traceback under `[<id>] scheduled render failed`. |
| `POST https://… returned 401: <body>` | The endpoint rejected the request. Add whatever it wants to `headers`. |
| `POST https://… returned 413: <body>` | The frame is larger than the endpoint accepts. A greyscale or colour frame is not small; consider a lower-depth `color_scheme`, or a different receiver. |
| `POST https://… failed: <exc>` | The request never completed: DNS, connection refused, TLS, or a timeout. Try the same URL with `curl` from the same host. If it is simply slow, raise `timeout`. |
| The endpoint gets the frame but cannot tell what it is | Read `X-Maverick-Display` and `Content-Type`. If you set a `Content-Type` in `headers`, yours wins — which may be exactly the problem. |
