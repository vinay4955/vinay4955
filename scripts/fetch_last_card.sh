#!/usr/bin/env bash
# Fetch the last published streak card from the live Pages site, for the slot gate.
#
#   fetch_last_card.sh <url> <out-file>
#
# 200 -> card written to <out-file>.
# 404 -> nothing deployed yet: <out-file> is left empty, which slot.py reads as
#        "this slot is not done" (first publish).
# anything else (5xx, DNS, refused, timeout) -> non-zero. A transient failure
# must fail the job: treating it as "nothing published" would empty the gate's
# input and republish a slot that is already done.
set -euo pipefail

url="${1:?usage: fetch_last_card.sh <url> <out-file>}"
out="${2:?usage: fetch_last_card.sh <url> <out-file>}"

code="$(curl -sS --retry 3 --retry-all-errors --max-time 20 \
  -o "$out" -w '%{http_code}' "$url?nocache=$(date +%s)")"

case "$code" in
  200) ;;
  404) echo "::notice::no card published at $url yet - first publish" >&2; : > "$out" ;;
  *)   echo "::error::could not read the last card from $url (HTTP $code)" >&2; exit 1 ;;
esac
