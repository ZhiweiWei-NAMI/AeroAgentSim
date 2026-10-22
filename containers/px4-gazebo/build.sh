#!/bin/sh
set -eu
backend=px4-gazebo
image=${1:-aeroagentsim/px4-gazebo:standalone}
case "$image" in
    aeroagentsim/px4-gazebo:standalone*) ;;
    *) echo "Build tag must be aeroagentsim/px4-gazebo:standalone*" >&2; exit 2 ;;
esac
context=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
jobs=${BUILD_JOBS-16}
jobs_ok=0
case "$jobs" in
    ''|*[!0-9]*) ;;
    *[1-9]*) jobs_ok=1 ;;
esac
if [ "$jobs_ok" -ne 1 ]; then
    echo "BUILD_JOBS must be a positive integer, got '$jobs'" >&2
    exit 2
fi
exec docker build --progress=plain --label aeroagentsim.job=release \
    --build-arg BUILD_JOBS="$jobs" \
    --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY \
    -t "$image" "$context"
