#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
revision=e58e986546aa4af927d1e74929996ae4f311c5c3
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
curl --fail --location "https://codeload.github.com/tordanik/OSM2World/tar.gz/$revision" -o "$work/source.tar.gz"
tar -xzf "$work/source.tar.gz" -C "$work"
source="$work/OSM2World-$revision"
patch -d "$source" -p1 < "$root/tools/osm2world/web-integration.patch"
mvn -f "$source/pom.xml" -pl core-web -am package -DskipTests -B -ntp
cp "$source/core-web/target/generated/js/teavm/osm2world-core-web.mjs" "$root/frontend/public/osm2world/osm2world-core-web.mjs"
cp "$source/LICENSE.txt" "$root/frontend/public/osm2world/LICENSE.txt"
node --input-type=module - "$root" "$revision" <<'JS'
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";
const [root, revision] = process.argv.slice(2);
const path = join(root, "frontend/public/osm2world/runtime-provenance.json");
const provenance = JSON.parse(readFileSync(path, "utf8"));
const digest = relative => createHash("sha256").update(readFileSync(join(root, relative))).digest("hex");
provenance.revision = revision;
provenance.patch_sha256 = digest(provenance.patch);
provenance.runtime_sha256 = digest("frontend/public/osm2world/osm2world-core-web.mjs");
writeFileSync(path, JSON.stringify(provenance, null, 2) + "\n");
console.log(provenance.runtime_sha256);
JS
