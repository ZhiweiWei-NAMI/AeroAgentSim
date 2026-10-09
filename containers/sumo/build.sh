#!/bin/sh
set -eu
backend=sumo
image=${1:-aeroagentsim/sumo:standalone}
case "$image" in
    aeroagentsim/sumo:standalone*) ;;
    *) echo "Build tag must be aeroagentsim/sumo:standalone*" >&2; exit 2 ;;
esac
context=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec docker build --progress=plain --label aeroagentsim.job=release \
    --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY \
    -t "$image" "$context"
