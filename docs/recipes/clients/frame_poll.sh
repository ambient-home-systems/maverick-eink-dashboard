#!/bin/sh
# Poll a Maverick frame and draw it, for a jailbroken Kindle or Kobo.
#
#   BASE=http://maverick.local:5000 DISPLAY_ID=kitchen TOKEN=s3cret ./frame_poll.sh
#
# The ETag is kept on disk, so a frame that has not changed costs one request
# and no e-ink refresh. See docs/recipes/kindle-kobo.md; untested on a device.

BASE=${BASE:-http://maverick.local:5000}
DISPLAY_ID=${DISPLAY_ID:-kitchen}
TOKEN=${TOKEN:-}
DIR=${DIR:-/mnt/us/maverick}
FRAME="$DIR/frame.png"
HDR="$DIR/frame.hdr"
ETAG="$DIR/etag"

mkdir -p "$DIR"
[ -f "$ETAG" ] || : > "$ETAG"

while true; do
    code=$(curl -s -o "$FRAME.new" -D "$HDR" -w '%{http_code}' \
        ${TOKEN:+-H "Access-Token: $TOKEN"} \
        -H "If-None-Match: $(cat "$ETAG")" \
        "$BASE/api/displays/$DISPLAY_ID/frame")

    case "$code" in
        200)
            mv "$FRAME.new" "$FRAME"
            awk 'tolower($1)=="etag:"{printf "%s", $2}' "$HDR" | tr -d '\r' > "$ETAG"
            fbink -q -c -g file="$FRAME" -f
            ;;
        304) ;;                                  # already on screen: do nothing
        401) echo "token rejected"; exit 1 ;;
        404) ;;                                  # nothing rendered yet; try later
        *)   echo "HTTP $code" ;;
    esac

    nap=$(awk 'tolower($1)=="x-maverick-next-refresh:"{printf "%d", $2}' "$HDR")
    sleep "${nap:-900}"
done
