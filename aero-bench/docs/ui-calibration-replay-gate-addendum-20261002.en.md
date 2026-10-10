# Sealed replay asset rate-limit gate

Updated: 2026-10-01 PDT (America/Los_Angeles).

The v8 browser diagnostic reached verified indexed replay data, loaded 525
entities and 298 recorded states, then failed while loading the native city.
At 75.973 seconds, Control returned HTTP 429 for declared public asset
`136956908bed081df8efb6a9f2a1eaf2061ebda5bcdd20e1475cfadc0e63528e`.
The response had no `Retry-After` header. The UI made that response terminal as
`asset request failed (429)` at 0/418 native presentation assets. Evidence is in
`validation/ui-calibration-20261001/browser-baseline/final/transport-diagnostic.json`
and `transport-diagnostic.log`.

The cause is the read path between `controlReplayFetch` and `AssetResolver`.
All content-addressed files use the same run-scoped token. Control admits 120
requests per token over a 60-second sliding window. The v8 public replay closes
443 files: 431 assets, 11 artifacts and the public trace. `ControlClient.publicAsset` correctly
returns the authoritative HTTP response, while `AssetResolver` correctly rejects
non-success responses. The virtual replay adapter previously had no handling for
the temporary 429 between those boundaries.

`frontend/src/control-replay-source.ts` now retries only sealed read-only
`replay/assets/<sha256>` and `replay/artifacts/<sha256>` requests that return 429.
It uses `Retry-After` delay-seconds or HTTP-date when supplied and a 60-second
default when absent or malformed. Each asset permits four retries after its
initial request. A requested delay above five minutes remains terminal, so a
server cannot hold the viewer indefinitely. The response body is cancelled
before waiting, and the caller's abort signal cancels the timer and prevents the
next request.

The adapter does not retry replay access, public trace, replay manifest,
authentication failures, other HTTP statuses or thrown network errors. It does
not mint credentials, change Control limits, select another source or substitute
local bytes.

Focused fake-clock tests cover the absent-header 60-second delay,
`Retry-After` delay-seconds, HTTP-date, abort, HTTP 401, non-asset 429 and the
four-retry cap. The focused suite passed 11/11 tests, and frontend type checking
passed.

The final browser rerun used the unchanged Control service and the frozen source
containing this adapter. Native-scene progress advanced 4 → 114 → 234 → 354 →
418. Control returned four 429 responses at each of three rate boundaries. The
viewer waited, retried the same sealed assets, never entered its failed stage,
and reported the replay and city scene ready at 2026-10-01 09:50:25 PDT. A
separate telemetry-HUD overlap affected later camera-button pointer actions; it
does not change this replay transport result.
