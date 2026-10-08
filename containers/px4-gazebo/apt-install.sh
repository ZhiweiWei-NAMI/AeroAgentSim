#!/bin/sh
# Record the public APT artifacts selected for this build before installation.
set -eu
record=$1
shift
mkdir -p /opt/aeroagentsim/build-inputs
apt-get -o Acquire::ForceHash=SHA256 --print-uris --yes --no-install-recommends \
    install "$@" > "/opt/aeroagentsim/build-inputs/apt-$record.txt"
apt-get -o Acquire::Retries=3 install --yes --no-install-recommends "$@"
dpkg-query -W -f='${Package} ${Version} ${Architecture}\n' \
    > "/opt/aeroagentsim/build-inputs/packages-$record.txt"
