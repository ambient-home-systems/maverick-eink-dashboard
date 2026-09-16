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
2. `/config/maverick.yaml` — the Home Assistant app's `/config` share
3. `~/.config/maverick/config.yaml`

If nothing is found, Maverick lists those paths and suggests `maverick init`.

The app's own options (`/data/options.json`) are not one of them. They are not a
configuration file in this format and never were: they are the Configuration
tab's values, which Maverick reads separately and turns into the environment
variables the file substitutes (`load_app_options` in
`src/maverick/ha/options.py`), before looking for the file itself.

## Where the displays live

Everything on this page is read from the config file, with one exception. The
displays are kept in a file Maverick itself writes — the **display store**,
`<data_dir>/displays.yaml` unless [`displays_file`](#top-level-keys) points
elsewhere — because the setup UI has to be able to add and change a display,
and the config file belongs to whoever wrote it (`src/maverick/store.py`).

`load_config` resolves the two on every load, and it is the only place that
does, so `serve`, `render`, `check` and `esphome` all get the same answer
(`resolve_displays` in `src/maverick/store.py`):

| State | What happens |
| --- | --- |
| The store exists | It is the source of the displays. A `displays:` list in the config file is **ignored**, with one warning naming both files. |
| No store, `displays:` in the config file | They are imported into the store, once, and read from it from then on. |
| Neither | There are no displays. |

So a config file's `displays:` list is a starting point rather than a running
record: write one to get going, and delete it once the first load has imported
it — the warning tells you when that has happened, and `maverick check` prints
which file the displays it validated came from. Until the setup UI can write
the store itself, changing a display means editing the store and restarting;
what changes today is which file that is.

The store is machine-owned. It is a mapping of `version: 1` and a `displays:`
list whose entries hold only what differs from a default display, so it reads
like the list it replaces, and `${VAR}` in it is expanded on load exactly as it
is here. A save rewrites the file whole, though, with the values those
substitutions expanded to, so a hand edit survives only until the next one.
Secrets belong in the config file, which nothing rewrites.

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

`${VAR:-default}` uses the text after `:-` when the variable is unset **or set
to the empty string**, which is what `:-` means in a shell (`expand_env` in
`src/maverick/config.py`). An empty default (`${MQTT_PASSWORD:-}`) is therefore
the way to say "optional, usually empty", and `${MQTT_PORT:-1883}` still gives
you 1883 when something upstream exported `MQTT_PORT=`. That matters under the
Home Assistant app, which sets a value for every substitution in the starter
config, empty for the options you have not filled in (`load_app_options` in
`src/maverick/ha/options.py`).

`${VAR}` without a default is the only form that cares whether a variable is
set at all: unset fails the load, set-and-empty yields the empty string, since
with no default written there is nothing else it could mean.

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
