#!/bin/sh
set -eu
backend=px4-gazebo
image=${1:-aeroagentsim/px4-gazebo:standalone}
case "$image" in
    aeroagentsim/px4-gazebo:standalone*) ;;
    *) echo "Build tag must be aeroagentsim/px4-gazebo:standalone*" >&2; exit 2 ;;
esac
context=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec docker build --progress=plain --label aeroagentsim.job=p9 \
    --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY \
    -t "$image" "$context"
