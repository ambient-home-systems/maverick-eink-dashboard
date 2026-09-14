Maverick is configured by one YAML file. This page lists every key in it, and is
generated from the models in `src/maverick/config.py`, so it describes the software
you have rather than the software somebody meant to write.

`maverick init > config.yaml` prints a starter file, and
[`config.example.yaml`](../../config.example.yaml) is a worked two-display example.
`maverick check` validates a file without rendering anything.

## Where the file is found

`maverick -c /path/to/config.yaml <command>` (or `--config`) names a file
explicitly, and a path that does not exist is an error rather than a fallback.
With no flag, the first of these that exists wins:

1. `config.yaml`, relative to the working directory
2. `/config/maverick.yaml` — the Home Assistant add-on's `/config` share
3. `/data/options.json` — the add-on's own options
4. `~/.config/maverick/config.yaml`

If nothing is found, Maverick lists those paths and suggests `maverick init`.

The third entry is a diagnostic rather than a usable format: `/data/options.json`
is the add-on's schema, which the add-on's run script is supposed to translate
into `/config/maverick.yaml` before starting the service. Reaching it means that
translation did not happen, and Maverick says so instead of trying to read it.

## Environment substitution

`${VAR}` and `${VAR:-default}` are expanded in every value in the file, including
values nested inside lists and mappings, before anything is validated. Keys
themselves are left alone. This is how secrets stay out of a file that usually
lives in a shared `/config` folder:

```yaml
home_assistant:
  token: ${HA_TOKEN}
mqtt:
  password: ${MQTT_PASSWORD:-}
```

`${VAR}` requires the variable to be set. If it is not, loading fails with:

```text
Environment variable HA_TOKEN is referenced in the config but not set (use ${HA_TOKEN:-default} to make it optional).
```

`${VAR:-default}` uses the text after `:-` when the variable is unset, and an
empty default (`${MQTT_PASSWORD:-}`) is the way to say "optional, usually empty".
Note that this is substitution, not shell evaluation: a variable set to an empty
string is still set, so `${VAR}` yields an empty string rather than failing, and
`${VAR:-fallback}` yields the empty string too.

## Unknown keys are an error

Every model rejects keys it does not know, so a misspelling fails at load with the
offending key named, instead of being silently ignored until you wonder why
`quiet_hours` never took effect. The one exception is `displays[].transport`,
whose extra keys are the chosen transport's own options; they are listed under
[Transport options](#transport-options).

## Durations

Wherever a key takes a duration it accepts `"30s"`, `"5m"`, `"1h"`, `"2d"`,
`"250ms"`, or a bare number meaning seconds. Values are converted to seconds when
the file loads, so an invalid duration fails at load rather than at the first
render.

## Panel profiles and per-display overrides

A display names a panel from the catalog (`maverick panels`), and the catalog
entry supplies the resolution, color scheme, dpi, native rotation, frame format
and ghosting behavior. That is the point of it: `panel: waveshare-7in5-mono` is
meant to be the last thing you have to know about the hardware.

Each of those values can still be overridden per display, and `DisplayConfig.resolved()`
merges the two in one place:

| Resolved value | Comes from | Falls back to |
| --- | --- | --- |
| `width`, `height` | `displays[].width`, `displays[].height` | the panel profile |
| `color_scheme` | `displays[].color_scheme` | the panel profile |
| `dpi` | `displays[].dpi` | the panel profile |
| `rotation` | `displays[].rotation` | the panel's native rotation |
| `frame_format` | `displays[].frame_format` | the panel's default format, then a default for the configured transport |
| `full_refresh_every` | `displays[].schedule.full_refresh_every` | the panel profile's recommendation |

One consequence is worth knowing: in every row but `rotation`, a numeric override
of `0` counts as "not set", so `full_refresh_every: 0` asks for the panel's
recommendation rather than for no full refreshes at all. `rotation` is the
exception that tells unset from zero, so `rotation: 0` really does mean "no
rotation, whatever the profile says".

Everything else — theme, image pipeline, lint thresholds, render, schedule,
transport, packing and ESPHome generation — is per display with no catalog
involvement, and defaults to the values in the tables below.
