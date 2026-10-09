#!/bin/sh
set -eu
backend=ns3
image=${1:-aeroagentsim/ns3:standalone}
case "$image" in
    aeroagentsim/ns3:standalone*) ;;
    *) echo "Build tag must be aeroagentsim/ns3:standalone*" >&2; exit 2 ;;
esac
context=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec docker build --progress=plain --label aeroagentsim.job=release \
    --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY \
    -t "$image" "$context"
