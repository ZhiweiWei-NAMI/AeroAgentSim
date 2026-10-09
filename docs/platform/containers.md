# Standalone backend container builds

All three Dockerfiles build from public, digest-pinned bases. No local registry,
AeroBench image, benchmark service or checkout is required. Service/native-provider
semantics are unchanged. The source/build inputs are vendored into each backend
context; copied AeroBench inputs retain their original bytes and provenance.

## Build and run

From the platform repository root:

```sh
containers/px4-gazebo/build.sh
containers/sumo/build.sh
containers/ns3/build.sh
```

The scripts forward existing HTTP/HTTPS proxy variables and accept only
`aeroagentsim/<backend>:standalone*` output tags. They do not disable TLS or bypass
registry verification. Configure the Docker daemon's proxy as well when the
registry needs it: build arguments affect build steps, not daemon registry pulls.
The public Ubuntu base bootstraps CA certificates through signed Ubuntu APT
metadata before subsequent HTTPS downloads. APT update failures are fatal;
network artifact downloads can retry, while stateful simulator calls never retry.

Runtime example (make `/tmp/aas-p9` first):

```sh
docker run -d --name aas-p9-px4 --label aeroagentsim.job=p9 --cpus 16 \
  --memory 16g -p 127.0.0.1:19000:9000 aeroagentsim/px4-gazebo:standalone
python \
  containers/px4-gazebo/smoke.py --port 19000 --vehicles 1 --runs 1 --step-ms 20 \
  --output /tmp/aas-p9/px4-smoke.json
docker rm -f aas-p9-px4

docker run -d --name aas-p9-sumo --label aeroagentsim.job=p9 --cpus 16 \
  --memory 16g -p 127.0.0.1:19003:9000 aeroagentsim/sumo:standalone
python \
  containers/sumo/smoke.py --port 19003 --seconds 60 --repeats 2 \
  --output /tmp/aas-p9/sumo-smoke
docker rm -f aas-p9-sumo

docker run -d --name aas-p9-ns3 --label aeroagentsim.job=p9 --cpus 16 \
  --memory 16g -p 127.0.0.1:19004:9000 aeroagentsim/ns3:standalone
python \
  containers/ns3/smoke.py --port 19004 --output /tmp/aas-p9/ns3-smoke
docker rm -f aas-p9-ns3
```

Existing Docker adapter tests opt into the standalone images through the small
conftest-only override. The fixture maps the original scenario image references,
allows only the supplied standalone tags, sets label `p9` and 16 CPU quota, and
moves the tests' measurement output into the authorized temporary directory:

```sh
AEROAGENTSIM_IMAGE_PX4=aeroagentsim/px4-gazebo:standalone \
AEROAGENTSIM_IMAGE_SUMO=aeroagentsim/sumo:standalone \
AEROAGENTSIM_IMAGE_NS3=aeroagentsim/ns3:standalone \
MYPYPATH=../aerokernel PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 \
HYPOTHESIS_STORAGE_DIRECTORY=/tmp/aas-p9/hypothesis \
  .venv/bin/python -m pytest tests/adapters -m docker -p no:cacheprovider \
  --basetemp=/tmp/aas-p9/pytest -s
```

## Public bases

The platform is tested here on Linux amd64. These exact digests were recovered
from the previous build recipes/image histories and then pulled from Docker Hub.

| Backend | Public base | Manifest SHA-256 |
|---|---|---|
| PX4/Gazebo builder and runtime | `px4io/px4-sitl-gazebo` | `6f687ce0dad52b648c6f882ebaee93a03254001a8857b761312bafb689f7803e` |
| SUMO (Python 3.12.11 / Debian bookworm) | `python` | `c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49` |
| ns-3 builder | `ubuntu:24.04` | `1e0a86e57d247923571b75e0aaf48a1449cf8c543d51fb3e07a4a7d7bfa79316` |

The ns-3 final `scratch` stage is assembled entirely from the Ubuntu builder's
runtime dependencies with the existing `bundle.py`. There is no independently
resolved runtime base. ns-3 uses the recovered optimized profile (release,
assertions/logging off, native optimizations on) with core, network, internet,
mobility, propagation, traffic-control and wifi modules. Native optimizations
retain the prior build's host-CPU assumption; this is not a portable multi-CPU
or multi-architecture image claim.

## Source and patch inputs

MAVSDK is 3.17.2 at `9e3ca17faa84aa868caea10a3bbdab7e53810ced`; all its third-party
archives are fetched before an offline CMake configure/build. The vendored patch
routes third-party builds to those local archives and includes the heartbeat and
audit CLI used by the unchanged slim launcher. No airspace plugin is referenced
by the slim service, so it is omitted. The public base retains PX4/Gazebo binaries
and their native models; camera/contact patches enforce expected source and
result hashes.

| Input | SHA-256 |
|---|---|
| `MAVSDK_SOURCE_SHA256` | `709de37d225f6374b3ebe65af8e34921759b7a8c2bbe611a581fd614126293c0` |
| `MAVLINK_SOURCE_SHA256` | `1e82b295bfd4fb9c3ce4d7f503e445c2050ea6d254dfd8addf56b7f2fa8b984d` |
| `PYMAVLINK_SOURCE_SHA256` | `e0949dd5858ecce28c52f5b30a7957f2236482417798867f5e961d7a2abd1fc8` |
| `MAVSDK_PATCH_SHA256` | `972bef4a0d23a72a1fa46c3d9fa808e4f2f73467da32692b1c5b5d681e3bfed5` |
| `MAVLINK_OFFLINE_PATCH_SHA256` | `777a0f83ab88ab9eff71a51daeddc7e88efcf58c23b1c73c5477c788ce82734a` |
| `PYMAVLINK_BUILD_LOCK_SHA256` | `4c51de8177452454bc62e795ee802c521e442b1b2a8429dc01cde45675546795` |
| `LIBEVENTS_SOURCE_SHA256` | `03a940cf47863e9c943cb1466bd26cc225ee3549c2eb8bed5978a9d0ea488191` |
| `PICOSHA2_SOURCE_SHA256` | `b99ca6df2c596b68c4587a56007aa2305b715f379729a665ebeb18ec1b99f28f` |
| `TINYXML2_SOURCE_SHA256` | `5556deb5081fb246ee92afae73efd943c889cef0cafea92b0b82422d6a18f289` |
| `LIBMAVLIKE_SOURCE_SHA256` | `7ca84d49104b615b00f9cb4af12241438d03581c6b0c2478197fa9b3831cdbd7` |
| `JSONCPP_SOURCE_SHA256` | `f93b6dd7ce796b13d02c108bc9f79812245a82e577581c4c9aabe57075c90ea2` |
| `LIBLZMA_SOURCE_SHA256` | `135c90b934aee8fbc0d467de87a05cb70d627da36abe518c357a873709e5b7d6` |
| `OPENSSL_SOURCE_SHA256` | `b6a5f44b7eb69e3fa35dbf15524405b44837a481d43d81daddde3ff21fcbb8e9` |
| `CARES_SOURCE_SHA256` | `dcd919635f01b7c8c9c2f5fb38063cd86500f7c6d4d32ecf4deff5e3497fb157` |
| `ABSL_SOURCE_SHA256` | `f17bec838a89633561fdfadbdc89efaca36a6bac04999290adb7c13559dfd504` |
| `PROTOBUF_SOURCE_SHA256` | `3d32940e975c4ad9b8ba69640e78f5527075bae33ca2890275bf26b853c0962c` |
| `RE2_SOURCE_SHA256` | `d59276d828823b73fd567d7f238d11a47fdab6e5542fbcf98e754e9319925141` |
| `GRPC_SOURCE_SHA256` | `afbc5d78d6ba6d509cc6e264de0d49dcd7304db435cbf2d630385bacf49e066c` |
| `https://www.nsnam.org/releases/ns-3.48.tar.bz2` | `5700ceecef2c9bc862502914b2237fe798d4c7ae07652247050e300e49407c4d` |
| `containers/px4-gazebo/requirements.lock` | `3ddaf8892308ee2b1491a0a4dc74b89d279be2a57118814236a123f43bd2bde2` |
| `containers/px4-gazebo/patch_camera_model.py` | `5638b6dcebbf0b091d7f9ce63fe7adb05591e6f17cf01a4e4fcf50aefb90893c` |
| `containers/px4-gazebo/inject_contact_sensors.py` | `6dfe60e70aae74fdde4c8f9be9af14d57a155f0473aaf85eb8ef985882e62437` |
| `containers/sumo/requirements.lock` | `1018386ff002e71e9085cba763e98c780982d0cedf6facb27d098a0f86cee271` |
| `containers/ns3/native/aero-ns3-provider.cc` | `73765241a0e8d55c1995a95e0fefefc3a2ca4b5dd5fc765d4b63893ab7815587` |
| `containers/ns3/native/scene.h` | `c973af9ec8c2a0edad66d971c1506d6e1e5f52aad2e0f6c1c21e4d66812baa88` |
| `containers/ns3/bundle.py` | `b96ba7aff5b106c5f83492eca51601470b9a52da5840c3f698f85393bd5ac395` |
| `containers/ns3/recovered/sources.tar.gz` | `4da0dd635bcdfc2d08bd6c5506ac1a3b9c8675b404dad390b8e179f5edccce27` |

Archive download URLs and exact versions/commits are in the PX4 Dockerfile's
`wget` commands. The immutable base supplies PX4 commit
`381149fb012762f5e38c4a7fdc1b905b28038970`, binary SHA-256
`b6d41b7e7d8be65017a1dc4cfe12502382e8595d2408c4c27d28c64a7cff66f6`, and
Gazebo 8.11.0 launcher SHA-256
`6e9cccdef9f9f266f45b76559c0974b1970e4348e82feb7a51964b4854cdd22a`.
The patched `mono_cam/model.sdf` hash is
`2d8b948585fc615cb0f34ed9d67885c15a9bac16df2d1fc59ebb03325c0c26cf`;
patched `x500_base/model.sdf` is
`7456e4b43a1894a727bdf544250205780dc2e74460d9cfa424c65edb3fb4e898`.
The patch helpers verify original SDF hashes before editing and accept an
already-correct result only after matching its full hash.

SUMO installs `eclipse-sumo`, `sumo-data`, `sumolib` and `traci` at 1.27.1. PX4
runtime installs `mavsdk==3.17.2`, `grpcio==1.83.1`, `protobuf==7.36.0` and
`typing-extensions==4.16.0`; its MAVLink generation uses the separately vendored
`pymavlink-build-requirements.lock`. Each lock lists every allowed distribution
hash. Downloads require hashes and binary wheels; installation uses those same
local verified wheels with no index. Selected wheel filenames and SHA-256 values
are recorded in each image's `build-inputs/*.sha256` and in the input ledger below.

APT libraries/compiler packages come from signed public distribution repositories.
`apt-install.sh` records the exact selected `.deb` URLs, filenames, sizes and
SHA-256 values before installation and records the installed package inventory.
Base-image packages are covered by the base digest. APT repository metadata is
live: a future rebuild can select newer OS package versions and change binary
bytes/image sizes even with the pinned simulation sources. Input ledgers make
that drift visible; they do not assert fully bit-identical rootfs rebuilds.

Provenance: PX4's build recipe, two patches, two locks and model-patch helpers,
and SUMO's lock, are copied/adapted from the read-only AeroBench
`containers/{px4-gazebo,sumo}/` trees. The original ns-3 provider recovery is
preserved in `containers/ns3/recovered/{sources.tar.gz,provenance.json,docker-history.txt}`;
this build compiles the existing platform provider, whose recovered derivation
and model changes are documented in [ns3-backend.md](ns3-backend.md).
No recovered simulator binary or complete AeroBench image is copied into a new
backend. Original images were never started, retagged, removed or pushed.

## Real validation (2026-10-08)

All final images were built from public bases and exercised on the local Docker
daemon with `--label aeroagentsim.job=p9 --cpus 16 --memory 16g`. Native service
ports were published only to loopback; passive listener observation preceded
smoke connections. Containers were removed after each check. Smoke and adapter
measurements are real, single-host observations, with other jobs on this host.

| Backend | First completed public source build (s) | Final recipe invocation (s) | Final image bytes |
|---|---:|---:|---:|
| px4-gazebo | 770.579 | 233.749 | 2,335,561,749 |
| sumo | 93.251 | 44.951 | 860,715,317 |
| ns3 | 286.957 | 286.957 | 72,841,777 |

These are measured build-command wall times, after base pulls, with cache reuse
from the preceding attempts. PX4's final invocation overlapped/reused the same
MAVSDK compile step; its later cache-only confirmation completed in 0.350 s.
They are not clean-machine cold-build timings. Runtime wheel downloads use
`--no-cache-dir`; archive/wheel caches do not inflate the final images.
Builds used an isolated rootless daemon entirely under `/tmp/aas-p9/`, inheriting
the shell proxy; its process tree was limited to CPU affinity 0–15 during native
compilation because this host lacks rootless CPU cgroups. Runtime CPU quota was
verified as `NanoCpus=16000000000` on the regular daemon.

| Backend | Final image ID |
|---|---|
| `aeroagentsim/px4-gazebo:standalone` | `sha256:2c973f0fb2bb0bc18f3f23f2b4a5d8ed2a5932eb0335e7cb7865fd6a8dd31444` |
| `aeroagentsim/sumo:standalone` | `sha256:7e3bdf8db7a7f1df726ef39c7df82e2cf887c414bb1d576bb5d02e10083f9530` |
| `aeroagentsim/ns3:standalone` | `sha256:ca03e1d2f4ca8e9a2f78ed41ef8192b374248226073f4394727d8705109de036` |

PX4 smoke used one x500, seed 42, 20 ms advances. Observed arm, takeoff to
10 m, goto `[50,0,10]`, land and final disarmed/ON_GROUND state all passed.
The reset took **13.142 s**; **44.02 s** of flight took
**13.245 s**, RTF **3.324**. PX4/Gazebo is not claimed
bitwise deterministic. MAVSDK's newly built server SHA-256 is
`9318e6a62abb6e0738f4a1f280f2d04c9eb991e51007ef63c03a54f45e7d676e`;
its source/patch/CLI and native smoke are verified rather than requiring the
prior compiler's binary hash.

SUMO smoke used 200 authored vehicles/3 persons for 60 s, 100 ms native and RPC
steps, seed 7, two fresh resets. Both passed the existing command/event checks.
Wall times were **17.240/16.181 s**,
RTF **3.480/3.708**. Full trajectories are each
**28,925,518 bytes**, byte-identical, SHA-256
`737488372cea4d6c08320e40227ff8171ae0faf29dda08e3be6ade2673f67ca3`.

ns-3 smoke accepted 2,400 datagrams per run: 430 observed deliveries and
1,970 native application timeout drops, zero pending after the drain. Both
fresh processes produced the same transcript, including all native measurements.
The 61 s native horizon took **4.353/4.360 s**,
RTF **14.012/13.991**.
Transcripts are **2,035,863 bytes**, SHA-256
`9ebfe5a4ae7c6333aabe81010e0f7a45dbb726a6a4927627bf43a79e2b91baa0`.
They are also byte-identical to the retained `dev-p4b` smoke transcript; the old
image was never started for this comparison.

The requested Docker adapter suite passed **3 tests** (146.309 s):
PX4 takeoff/goto/land with offline replay; SUMO 60 s/reroute with replay;
and the coupled PX4/SUMO/ns-3 30 s run with real mobility, packet outcomes and
replay. All images were overridden to `:standalone`. The platform `.venv` was
used for this exact requested suite (it provides PyYAML 6.0.3); build/smoke scripts
used the required aerokernel Python 3.11 environment. Kernel runtime code and
its stdlib-only dependency boundary were untouched.

Evidence is under each backend's `standalone/`: image metadata, measured builds,
smoke metrics, source fingerprints and extracted input manifests. SUMO/ns-3
retain run A as lossless gzip and both original transcript hashes/byte-equality
results; full repeated raw files remain under `/tmp/aas-p9/`. Adapter JUnit and
measurements are in `containers/px4-gazebo/standalone/`. The kernel/host code,
service code, native provider and original image tags were not modified.

Two concurrent WorkBuddy GLM sessions (`workbuddy/glm-5.3-flash`, max output
131072, no effort parameter) reviewed the PX4 runtime prerequisites and the
conftest-only image override. Their completed outputs were checked against
source; speculative concerns about extra world plugins and alternate pytest
marker expressions were not turned into unrequested service/test changes.

Failure/build history: direct pulls on the regular daemon timed out because its
proxy is unset; the isolated proxy-enabled daemon pulled all three public bases.
Initial APT HTTP requests returned 502; HTTPS initialization on bare Ubuntu
failed before CA certificates existed. The final Dockerfiles retain signed APT
CA bootstrapping, verified HTTPS and explicit update errors. No TLS bypass,
synthetic simulator outcome, hidden failed test or unavailable upstream artifact
was used. gRPC produced upstream `always_inline` compiler warnings; its build
completed successfully. An initial collect-only check with aerokernel's venv
failed for missing host PyYAML; the requested platform venv resolved it without
changing either environment. Vendored model-patch helpers retain their original
bytes, including pre-existing import-order lint findings; conftest Ruff and all
shell syntax checks passed.

## Input ledger

The following are every additional APT artifact and selected Python wheel for
the actual builds. Packages already in a public base are covered by its digest.
Stage package inventories are snapshots immediately after installation; PX4
subsequently removes the venv bootstrap package, and ns-3 copies only runtime
files. APT's signed metadata/artifact verification remains enabled.

### px4-gazebo

| Input / stage | SHA-256 |
|---|---|
| `libpython3.12-dev_3.12.3-1ubuntu0.17_amd64.deb` (build-inputs/apt-runtime) | `c123ddab7763e45199e21b4ac8545e705f20a0315b9ada4c236b52fb088c4e16` |
| `libpython3.12t64_3.12.3-1ubuntu0.17_amd64.deb` (build-inputs/apt-runtime) | `0d29f4763eb95b8a4283a4aca3ef1d9658629c618be1778def41bdc31be12b46` |
| `python3.12_3.12.3-1ubuntu0.17_amd64.deb` (build-inputs/apt-runtime) | `6745c9463432e619d7402b117ad4ac86c31dcd3999ba20daa9e395f6f9909d86` |
| `libpython3.12-stdlib_3.12.3-1ubuntu0.17_amd64.deb` (build-inputs/apt-runtime) | `45d3f530ba1f9d6e879ad46b92046fabab13fe50a82450e4f65557a3bbad1489` |
| `python3.12-minimal_3.12.3-1ubuntu0.17_amd64.deb` (build-inputs/apt-runtime) | `d452689b9660845345a4c3e05e4ad82c082d5474e04031b7aa47f1a6d5610a6e` |
| `libpython3.12-minimal_3.12.3-1ubuntu0.17_amd64.deb` (build-inputs/apt-runtime) | `d646ad7112b5adec21ba0e1af015f04ae8ca7f5efdc619622547dd6673c4c14b` |
| `ca-certificates_20260601~24.04.1_all.deb` (build-inputs/apt-runtime) | `6bac2a01979e210d9eac1d4d56747ec709ea60654744d66705dc3c36e7629e50` |
| `python3-pip-whl_24.0+dfsg-1ubuntu1.3_all.deb` (build-inputs/apt-runtime) | `4b7c50db8f261b208c1d9cde8db148c1f682cc516957b986ada5088cfcee1359` |
| `python3-setuptools-whl_68.1.2-2ubuntu1.2_all.deb` (build-inputs/apt-runtime) | `edfa94cc1f6a33af99cfaf6ebfe35dbcd9c4bdd8555b90c0d8e78479faf5c8f0` |
| `python3.12-venv_3.12.3-1ubuntu0.17_amd64.deb` (build-inputs/apt-runtime) | `b8d4bfea3df63945b6a202bd0773caf7727dde86d2df91df6e88a8ccf7c18b7e` |
| `python3-venv_3.12.3-0ubuntu2.1_amd64.deb` (build-inputs/apt-runtime) | `16d50387b7656796b79e2c059d06774dfe9fe3f42808d8d50b332ceb780f508a` |
| `dpkg_1.22.6ubuntu6.6_amd64.deb` (mavsdk-builder/apt-builder) | `ceb6aa4da59fbcb8a3b0549b4280c60b7d849b519859797c8668bdfbe378fb3b` |
| `libbz2-1.0_1.0.8-5.1ubuntu0.1_amd64.deb` (mavsdk-builder/apt-builder) | `36ee08b00b1ee5018b2fce02881b7d2d73c9b4e32990bde3388400d86c48126a` |
| `liblzma-dev_5.6.1+really5.4.5-1ubuntu0.3_amd64.deb` (mavsdk-builder/apt-builder) | `a36f21970809e5ec58b6fb1186b1a819276f92c081eb109aeebf00e82e78d068` |
| `liblzma5_5.6.1+really5.4.5-1ubuntu0.3_amd64.deb` (mavsdk-builder/apt-builder) | `d2eabd41ca77d2c2dd9d5d4ef478cccb64ffde6279c47cf4699a857d46785a52` |
| `ca-certificates_20260601~24.04.1_all.deb` (mavsdk-builder/apt-builder) | `6bac2a01979e210d9eac1d4d56747ec709ea60654744d66705dc3c36e7629e50` |
| `python3-pkg-resources_68.1.2-2ubuntu1.2_all.deb` (mavsdk-builder/apt-builder) | `13caaa69d5ba8b02e90087d4bcef714c0581e591e820804d15d12f582055147a` |
| `libjansson4_2.14-2build2_amd64.deb` (mavsdk-builder/apt-builder) | `0cf79113f5d193ce9af2be2ff4b2c3b30dd4e55a0b6c47f7d28f6c849ff3aa60` |
| `wget_1.21.4-1ubuntu4.5_amd64.deb` (mavsdk-builder/apt-builder) | `2aa5a734b21b49ff6bd2239546b3f88275f8abb55dd8a984249e6cd0c1173a92` |
| `xz-utils_5.6.1+really5.4.5-1ubuntu0.3_amd64.deb` (mavsdk-builder/apt-builder) | `778edae086bc8f34d80f36f301bc8fb3eff2d906c146dfb533ea6840b6d64e00` |
| `libctf-nobfd0_2.42-4ubuntu2.10_amd64.deb` (mavsdk-builder/apt-builder) | `da352eb7fa6c4369d2a6c1e5e680f574eda2e38576563326b18d9e47e61c4078` |
| `libctf0_2.42-4ubuntu2.10_amd64.deb` (mavsdk-builder/apt-builder) | `7ec86d697c3668503c85f308a6832f092075b5880ad002f22185264da0bd4645` |
| `libgprofng0_2.42-4ubuntu2.10_amd64.deb` (mavsdk-builder/apt-builder) | `1b7e3c2fc162e8358ca6e5a3fffdb4d0d632f790630323215841bb36a63c0ab8` |
| `binutils-x86-64-linux-gnu_2.42-4ubuntu2.10_amd64.deb` (mavsdk-builder/apt-builder) | `1e510a15f30208d39edcd840e48f26a77bbca7c417805eeccb1e3f7de198ef29` |
| `binutils_2.42-4ubuntu2.10_amd64.deb` (mavsdk-builder/apt-builder) | `b3b5a84181a38fd191820b2cdcc1a3eeb1cd6333ad472f2092f96e81047e9c74` |
| `libisl23_0.26-3build1.1_amd64.deb` (mavsdk-builder/apt-builder) | `4e040926e50fb961fae9bf95660189d468336a4a17bc321872c434fc8f777e7f` |
| `libmpfr6_4.2.1-1build1.1_amd64.deb` (mavsdk-builder/apt-builder) | `aebc1c8b69a1f98bb43dfc268daecd181116dbf40b13ee4e822eb4bdd52b493a` |
| `libmpc3_1.3.1-1build1.1_amd64.deb` (mavsdk-builder/apt-builder) | `cebe6098bb3d66fdacac9dc6fe406a651216d9c00f27c3f9c159d15d96cdf864` |
| `cpp-13-x86-64-linux-gnu_13.3.0-6ubuntu2~24.04.1_amd64.deb` (mavsdk-builder/apt-builder) | `2ca48bf0c2d6465bc39322899715a85d934b4d7442dd5586a7bebbe3ce0f806b` |
| `cpp-13_13.3.0-6ubuntu2~24.04.1_amd64.deb` (mavsdk-builder/apt-builder) | `c7535331fbb183c802c3bf4b6b210872dcc12d0421b3212b3c4b940f2c59ed3a` |
| `cpp-x86-64-linux-gnu_4%3a13.2.0-7ubuntu1_amd64.deb` (mavsdk-builder/apt-builder) | `85059b30960de3582e8612740614da3dfe47241d0368a28dea686188cf7648dd` |
| `cpp_4%3a13.2.0-7ubuntu1_amd64.deb` (mavsdk-builder/apt-builder) | `b51f8094760f7b41afdcb1fe1b5a57fc64b75a090859918af17450a10f8c7d31` |
| `libcc1-0_14.2.0-4ubuntu2~24.04.1_amd64.deb` (mavsdk-builder/apt-builder) | `454456436ca767817a860557263d7cc2489f0a410f03efff4c0bb236d579ec09` |
| `gcc-13-x86-64-linux-gnu_13.3.0-6ubuntu2~24.04.1_amd64.deb` (mavsdk-builder/apt-builder) | `a134b0319a82d14581b3a14820d2832af4ec9778ed8b9b4ddaeecfb0555ec325` |
| `gcc-13_13.3.0-6ubuntu2~24.04.1_amd64.deb` (mavsdk-builder/apt-builder) | `7438ff160b020a74970672189ecae25d0ca650de6d7f543f12a3134192cffbd9` |
| `gcc-x86-64-linux-gnu_4%3a13.2.0-7ubuntu1_amd64.deb` (mavsdk-builder/apt-builder) | `72e79089a10e381360bfc6f03c5e5d8c2ff177d6dbac2cd7ffb3cc1383f57591` |
| `gcc_4%3a13.2.0-7ubuntu1_amd64.deb` (mavsdk-builder/apt-builder) | `0e0bb8b25153ed1c44ab92bc219eed469fcb5820c5c0bc6454b2fd366a33d3ee` |
| `g++-13-x86-64-linux-gnu_13.3.0-6ubuntu2~24.04.1_amd64.deb` (mavsdk-builder/apt-builder) | `0bd6af6164252d4ea9170d201ee4c10a3120fe0fc04985a00be3bc9076353844` |
| `g++-13_13.3.0-6ubuntu2~24.04.1_amd64.deb` (mavsdk-builder/apt-builder) | `0f3ef964b8a91a9cd7bebfd126839a21941c55547cadcf4aa00204bc8cc43985` |
| `g++-x86-64-linux-gnu_4%3a13.2.0-7ubuntu1_amd64.deb` (mavsdk-builder/apt-builder) | `145b027a542db5b21f85d4f9e242f51c62c69914fc484adb3a4881ca8e2f3913` |
| `g++_4%3a13.2.0-7ubuntu1_amd64.deb` (mavsdk-builder/apt-builder) | `800a84b369c64b18d65f0fb5c1533de0446c880ee4f1cadad64e3363327f63f0` |
| `make_4.3-4.1build2_amd64.deb` (mavsdk-builder/apt-builder) | `1fe6a815b56c7b6e9ce4086a363f09444bbd0a0d30e230c453d0b78e44b57a99` |
| `libdpkg-perl_1.22.6ubuntu6.6_all.deb` (mavsdk-builder/apt-builder) | `db57f9f0608f78001b5f468e03541fdb88b7e30a07b46d0bcdb57a916a6e8c11` |
| `bzip2_1.0.8-5.1ubuntu0.1_amd64.deb` (mavsdk-builder/apt-builder) | `60fb79214dd8366fa39d7e69911294e72b9f1a4425a3d22f211359c8626e4b9e` |
| `patch_2.7.6-7build3_amd64.deb` (mavsdk-builder/apt-builder) | `fb7d78ed25c2788a607802270f3c28ac5ed6857bfb6cce9f73ad2cafac9159b3` |
| `lto-disabled-list_47_all.deb` (mavsdk-builder/apt-builder) | `cac0f63c88188f376bb4df10a2af2b49a607bbef97a57db50ed58f678c21eee9` |
| `dpkg-dev_1.22.6ubuntu6.6_all.deb` (mavsdk-builder/apt-builder) | `70da073557dbed76dc14eb1e682e7ec30fcb736f3d98812490a4c785b4c481cb` |
| `build-essential_12.10ubuntu1_amd64.deb` (mavsdk-builder/apt-builder) | `9b2159b95a4c01937309783c7c8a6ec8dfbcb7ffd604951c9713ac52ee9c7268` |
| `liberror-perl_0.17029-2_all.deb` (mavsdk-builder/apt-builder) | `1907af6bf33dd8684447c09f216c675d2b8559fadd8ddace29fbf83c6fb2a636` |
| `git-man_1%3a2.43.0-1ubuntu7.3_all.deb` (mavsdk-builder/apt-builder) | `5701f931ed2cd30644700b0fc1cda7c2214f93a63ef84c0c80e43ce40d2cf1d2` |
| `git_1%3a2.43.0-1ubuntu7.3_amd64.deb` (mavsdk-builder/apt-builder) | `099bb129f543adc4c14203334b0fa0a909f8bf038c4d56bc9cc7c774ebf78f87` |
| `python3-setuptools_68.1.2-2ubuntu1.2_all.deb` (mavsdk-builder/apt-builder) | `27ede03d8c5046c30cf1dcdc8835708408d27bdd3561e881b5d66656e6ff23e7` |
| `python3-wheel_0.42.0-2_all.deb` (mavsdk-builder/apt-builder) | `e1a4076425d3d1f1772b11bff6a025055723b2d0a4972f9519849ddaa8cd3953` |
| `python3-pip_24.0+dfsg-1ubuntu1.3_all.deb` (mavsdk-builder/apt-builder) | `4e1af0b6f0b52c847ffa7b192b9f650845b99ff300138af05ab9a6252eb75682` |
| `absl.tar.gz` (mavsdk-builder) | `f17bec838a89633561fdfadbdc89efaca36a6bac04999290adb7c13559dfd504` |
| `cares.tar.gz` (mavsdk-builder) | `dcd919635f01b7c8c9c2f5fb38063cd86500f7c6d4d32ecf4deff5e3497fb157` |
| `grpc.tar.gz` (mavsdk-builder) | `afbc5d78d6ba6d509cc6e264de0d49dcd7304db435cbf2d630385bacf49e066c` |
| `jsoncpp.tar.gz` (mavsdk-builder) | `f93b6dd7ce796b13d02c108bc9f79812245a82e577581c4c9aabe57075c90ea2` |
| `libevents.tar.gz` (mavsdk-builder) | `03a940cf47863e9c943cb1466bd26cc225ee3549c2eb8bed5978a9d0ea488191` |
| `liblzma.tar.gz` (mavsdk-builder) | `135c90b934aee8fbc0d467de87a05cb70d627da36abe518c357a873709e5b7d6` |
| `libmavlike.tar.gz` (mavsdk-builder) | `7ca84d49104b615b00f9cb4af12241438d03581c6b0c2478197fa9b3831cdbd7` |
| `mavlink.tar.gz` (mavsdk-builder) | `1e82b295bfd4fb9c3ce4d7f503e445c2050ea6d254dfd8addf56b7f2fa8b984d` |
| `mavsdk.tar.gz` (mavsdk-builder) | `709de37d225f6374b3ebe65af8e34921759b7a8c2bbe611a581fd614126293c0` |
| `openssl.tar.gz` (mavsdk-builder) | `b6a5f44b7eb69e3fa35dbf15524405b44837a481d43d81daddde3ff21fcbb8e9` |
| `picosha2.tar.gz` (mavsdk-builder) | `b99ca6df2c596b68c4587a56007aa2305b715f379729a665ebeb18ec1b99f28f` |
| `protobuf.tar.gz` (mavsdk-builder) | `3d32940e975c4ad9b8ba69640e78f5527075bae33ca2890275bf26b853c0962c` |
| `pymavlink.tar.gz` (mavsdk-builder) | `e0949dd5858ecce28c52f5b30a7957f2236482417798867f5e961d7a2abd1fc8` |
| `re2.tar.gz` (mavsdk-builder) | `d59276d828823b73fd567d7f238d11a47fdab6e5542fbcf98e754e9319925141` |
| `tinyxml2.tar.gz` (mavsdk-builder) | `5556deb5081fb246ee92afae73efd943c889cef0cafea92b0b82422d6a18f289` |
| `mavlink-offline-python.patch` (mavsdk-builder) | `777a0f83ab88ab9eff71a51daeddc7e88efcf58c23b1c73c5477c788ce82734a` |
| `mavsdk-incoming-heartbeat-timeout.patch` (mavsdk-builder) | `972bef4a0d23a72a1fa46c3d9fa808e4f2f73467da32692b1c5b5d681e3bfed5` |
| `pymavlink-build-requirements.lock` (mavsdk-builder) | `4c51de8177452454bc62e795ee802c521e442b1b2a8429dc01cde45675546795` |
| `fastcrc-0.3.6-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl` (mavsdk-builder) | `129bf39d1f21a2d440170f8ea2afc7d5c9bc53e309cb27cf0b40f43dd950853a` |
| `h11-0.16.0-py3-none-any.whl` (mavsdk-builder) | `63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86` |
| `iniconfig-2.3.0-py3-none-any.whl` (mavsdk-builder) | `f631c04d2c48c52b84d0d0549c99ff3859c98df65b3101406327ecc7d53fbf12` |
| `lxml-6.1.2-cp312-cp312-manylinux_2_26_x86_64.manylinux_2_28_x86_64.whl` (mavsdk-builder) | `04cf9e3f4ee9cab9d9ba05401bef8668840fa9620fcd4d8e85a2d2fd0b0fa960` |
| `packaging-26.3-py3-none-any.whl` (mavsdk-builder) | `d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c` |
| `pluggy-1.6.0-py3-none-any.whl` (mavsdk-builder) | `e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746` |
| `pytest-7.4.4-py3-none-any.whl` (mavsdk-builder) | `b090cdf5ed60bf4c45261be03239c2c1c22df034fbffe691abe93cd80cea01d8` |
| `setuptools-84.0.0-py3-none-any.whl` (mavsdk-builder) | `51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670` |
| `syrupy-4.9.1-py3-none-any.whl` (mavsdk-builder) | `b94cc12ed0e5e75b448255430af642516842a2374a46936dd2650cfb6dd20eda` |
| `wheel-0.48.0-py3-none-any.whl` (mavsdk-builder) | `3217dcc807155e45db462d7ef2431f5ddda0d7273b700d05a67b271ceb1287ab` |
| `wsproto-1.3.2-py3-none-any.whl` (mavsdk-builder) | `61eea322cdf56e8cc904bd3ad7573359a242ba65688716b0710a5eb12beab584` |
| `mavsdk_server` (build-inputs) | `9318e6a62abb6e0738f4a1f280f2d04c9eb991e51007ef63c03a54f45e7d676e` |
| `grpcio-1.83.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl` (build-inputs) | `72578aa07a4008f17521ef52debcc3acfd1e2c5426243bc3ffb56a38bfe610b7` |
| `mavsdk-3.17.2-py3-none-manylinux1_x86_64.manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_5_x86_64.whl` (build-inputs) | `78ac2402306022bb6000e1fa02dfe4af7a634c803d4ca90673b1c7918a23c937` |
| `protobuf-7.36.0-cp310-abi3-manylinux2014_x86_64.whl` (build-inputs) | `70f5ec8eb0da81a44360c0dc0beac99a0d78071d21956a7076bae8bd2051841b` |
| `typing_extensions-4.16.0-py3-none-any.whl` (build-inputs) | `481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8` |

Build/source context fingerprints:

| File | SHA-256 |
|---|---|
| `Dockerfile` | `2c07b31442812e676244dd95396df2526b5295248083c6d5858726f6fe8d6101` |
| `.dockerignore` | `64d17a0a15d66396a1de7b214d3b447ca896be7c279457d9458dda9c9895c094` |
| `apt-install.sh` | `09b1a103d1dd6f2bad9ad4bbe28d999260222a6c365f8a0b5df67f2f7a0ac18f` |
| `service/__init__.py` | `4f6bc8d3b67c51f07ae429bb1b2e6369f07edc684d75835373c9cdb848ace5b5` |
| `service/__main__.py` | `93d8aae0b5484be0a723504fe9853c450557d8b5dccb73fa8bbb139f09434ef3` |
| `service/commands.py` | `ef9fd049f52d23879aa9ba4f148e449e78961c3c6d76592cd6ea5f987ec41385` |
| `service/config.py` | `916e4db4f5c741c71734c64507e7b1e0b2c3a0a5504a05c3dc1344643db856db` |
| `service/heartbeat.py` | `d16566dac0a9dc531c1c93fd17cceaf6a145c29fd411a230ceb0d3529f9c8ef8` |
| `service/processes.py` | `d6d6b76a85aa58292309301ff0a379dcbd8afea1b2ec9d3983b511c72fae6dba` |
| `service/rpc.py` | `89df897b8f8b8b233cdaef783e31a56ba155d4a3714818f3c28fc84d51e49fc3` |
| `service/runtime.py` | `817012ffbbf0ae0ccd2c001cafa301b1dd3e63034dab46eab5b6a67751ec2ddf` |
| `service/telemetry.py` | `93f90cc945e7409b5c1da1a08f0dcee0519de11e5b084328183279986239b381` |
| `service/transport.py` | `2eab4ebf68715d05e965bde71b61528badb23928a0d79092ec9fa42b4e354cc1` |
| `service/world_control.py` | `b38b6ebe989a3c71b004af3ca03c70f0d7dafebffbd4b40c2347d5a52e0e21c7` |
| `pymavlink-build-requirements.lock` | `4c51de8177452454bc62e795ee802c521e442b1b2a8429dc01cde45675546795` |
| `requirements.lock` | `3ddaf8892308ee2b1491a0a4dc74b89d279be2a57118814236a123f43bd2bde2` |
| `mavlink-offline-python.patch` | `777a0f83ab88ab9eff71a51daeddc7e88efcf58c23b1c73c5477c788ce82734a` |
| `mavsdk-incoming-heartbeat-timeout.patch` | `972bef4a0d23a72a1fa46c3d9fa808e4f2f73467da32692b1c5b5d681e3bfed5` |
| `inject_contact_sensors.py` | `6dfe60e70aae74fdde4c8f9be9af14d57a155f0473aaf85eb8ef985882e62437` |
| `patch_camera_model.py` | `5638b6dcebbf0b091d7f9ce63fe7adb05591e6f17cf01a4e4fcf50aefb90893c` |

### sumo

| Input / stage | SHA-256 |
|---|---|
| `libatomic1_12.2.0-14+deb12u1_amd64.deb` (build-inputs/apt-runtime) | `fbd4e154a6b444229ea002cc209df099209c0adc09102e5fd21239a3d2b55e2d` |
| `libbsd0_0.11.7-2_amd64.deb` (build-inputs/apt-runtime) | `bb31cc8b40f962a85b2cec970f7f79cc704a1ae4bad24257a822055404b2c60b` |
| `libdrm-common_2.4.114-1_all.deb` (build-inputs/apt-runtime) | `32f9664138b38b224383c6986457d5ad2ec8efd559b1a0ce7749405f7a451aad` |
| `libdrm2_2.4.114-1+b1_amd64.deb` (build-inputs/apt-runtime) | `be18fb670797ba32da9628cf3e8acd83160d8db8c8dd842501dd8e401c3b5371` |
| `libdrm-amdgpu1_2.4.114-1+b1_amd64.deb` (build-inputs/apt-runtime) | `b75a71e96f1faac0f131ac657e09efcbe8968eef62cc34b8abfcff2ff9f0cccd` |
| `libpciaccess0_0.17-2_amd64.deb` (build-inputs/apt-runtime) | `4025f3608cf431c163efb94fdc553e7b93e16b8f0d741ea87762e19025ffc80e` |
| `libdrm-intel1_2.4.114-1+b1_amd64.deb` (build-inputs/apt-runtime) | `b0e39318d14c07f4d85668b6da7f66a1341addf87a47d785c34d5a8b393f544c` |
| `libdrm-nouveau2_2.4.114-1+b1_amd64.deb` (build-inputs/apt-runtime) | `ba59bb9ec6e1baf59fc4d4eb095a524e40d045af2413dad9d28df517005388b6` |
| `libdrm-radeon1_2.4.114-1+b1_amd64.deb` (build-inputs/apt-runtime) | `2da3a9233187f995ad5a3e6db3d37252ea7209f0ca9605484d03478ebcc15feb` |
| `libedit2_3.1-20221030-2_amd64.deb` (build-inputs/apt-runtime) | `1cf14abf2716d3279db12d0657a5737cf70074a1e71d3bdf73206625e3c89ce6` |
| `libelf1_0.188-2.1_amd64.deb` (build-inputs/apt-runtime) | `619add379c606b3ac6c1a175853b918e6939598a83d8ebadf3bdfd50d10b3c8c` |
| `libexpat1_2.5.0-1+deb12u4_amd64.deb` (build-inputs/apt-runtime) | `ed010cc41577d75ab01cccc6afa93496d9a99f1e16bd469caf58e1b81fddae80` |
| `libglvnd0_1.6.0-1_amd64.deb` (build-inputs/apt-runtime) | `b6da5b153dd62d8b5e5fbe25242db1fc05c068707c365db49abda8c2427c75f8` |
| `libxau6_1%3a1.0.9-1_amd64.deb` (build-inputs/apt-runtime) | `679db1c4579ec7c61079adeaae8528adeb2e4bf5465baa6c56233b995d714750` |
| `libxdmcp6_1%3a1.1.2-3_amd64.deb` (build-inputs/apt-runtime) | `ecb8536f5fb34543b55bb9dc5f5b14c9dbb4150a7bddb3f2287b7cab6e9d25ef` |
| `libxcb1_1.15-1_amd64.deb` (build-inputs/apt-runtime) | `fdc61332a3892168f3cc9cfa1fe9cf11a91dc3e0acacbc47cbc50ebaa234cc71` |
| `libx11-data_2%3a1.8.4-2+deb12u2_all.deb` (build-inputs/apt-runtime) | `987a848aeb1c358e4186368871b0526f10bb14c6b53214ab3bf8b69abb830191` |
| `libx11-6_2%3a1.8.4-2+deb12u2_amd64.deb` (build-inputs/apt-runtime) | `d88c973e79fd9b65838d77624142952757e47a6eb1a58602acf0911cf35989f4` |
| `libglapi-mesa_22.3.6-1+deb12u2_amd64.deb` (build-inputs/apt-runtime) | `2db5b13a6b7f4042f9a417ad7fa4338b6c47528dc64d7f89cfd5e53d07d76bc4` |
| `libx11-xcb1_2%3a1.8.4-2+deb12u2_amd64.deb` (build-inputs/apt-runtime) | `f5da45e1d881a793250a96613f28c471a248877f1a0f18a5c90e2a620a76c898` |
| `libxcb-dri2-0_1.15-1_amd64.deb` (build-inputs/apt-runtime) | `ef4959aa9e09a0d38d1de432e747585129d5d2dc1d84c8b6b3d2ffc3708b5805` |
| `libxcb-dri3-0_1.15-1_amd64.deb` (build-inputs/apt-runtime) | `02699b144b9467de8636d27a76984b8f4e7b66e2d25d96df2b9677be86ee9a29` |
| `libxcb-glx0_1.15-1_amd64.deb` (build-inputs/apt-runtime) | `1bce55fc292d93fa5f7fa50f84cef99ec29be70d0ffe98e86b8008e59f4a34fa` |
| `libxcb-present0_1.15-1_amd64.deb` (build-inputs/apt-runtime) | `89383e627a4d17b9390d609b2459481bfd2029566367b43068586769e418b6e5` |
| `libxcb-randr0_1.15-1_amd64.deb` (build-inputs/apt-runtime) | `f86e3d8ff8622871008833e9d064919b7a6237399c903c59fc330ff00f199ff5` |
| `libxcb-shm0_1.15-1_amd64.deb` (build-inputs/apt-runtime) | `c1afcef29dc78b95c475159b181b28b1dedaf1d5aa06efd2fa6d90c73bfbe0e5` |
| `libxcb-sync1_1.15-1_amd64.deb` (build-inputs/apt-runtime) | `3366ce715220d38dd0148b78a8e738137bade25ef7eec0698850c6f66800844f` |
| `libxcb-xfixes0_1.15-1_amd64.deb` (build-inputs/apt-runtime) | `d744a7ebad2cbcf301c96cd6a1ab3ee856e436fc7be5cff5b7c28ac2ac181a64` |
| `libxext6_2%3a1.3.4-1+b1_amd64.deb` (build-inputs/apt-runtime) | `504b7be9d7df4f6f4519e8dd4d6f9d03a9fb911a78530fa23a692fba3058cba6` |
| `libxfixes3_1%3a6.0.0-2_amd64.deb` (build-inputs/apt-runtime) | `1cd616396ff2ecae77e6e8b5b7695d414f0146de2d147837a2a02165f99e1a2c` |
| `libxshmfence1_1.3-1_amd64.deb` (build-inputs/apt-runtime) | `1a38142e40e3d32dc4f9a326bf5617363b7d9b4bb762fdcdd262f2192092024d` |
| `libxxf86vm1_1%3a1.1.4-1+b2_amd64.deb` (build-inputs/apt-runtime) | `6f4ca916aaec26d7000fa7f58de3f71119309ab7590ce1f517abfe1825a676c7` |
| `libicu72_72.1-3+deb12u1_amd64.deb` (build-inputs/apt-runtime) | `f7f6f99c6d7b025914df2447fc93e11d22c44c0c8bdd8b6f36691c9e7ddcef88` |
| `libxml2_2.9.14+dfsg-1.3~deb12u6_amd64.deb` (build-inputs/apt-runtime) | `4460e39dda10a815881374217cde08474747cfa018358cd8612c14b390eff53b` |
| `libz3-4_4.8.12-3.1_amd64.deb` (build-inputs/apt-runtime) | `6221ca25ad5abcfbe1965801029d85a88b4775320384b4b716de8fab7a4d2f7a` |
| `libllvm15_1%3a15.0.6-4+b1_amd64.deb` (build-inputs/apt-runtime) | `9f0751109ba89e65b1313a4f3e34a29977a0db6fa30ed475e2c6bd555fa9e866` |
| `libsensors-config_1%3a3.6.0-7.1_all.deb` (build-inputs/apt-runtime) | `7f3c9fbd822858a9e30335e4a7f66c9468962eb26cd375b93bc8b789660bf02f` |
| `libsensors5_1%3a3.6.0-7.1_amd64.deb` (build-inputs/apt-runtime) | `b7eb91dce728fbb9203aec8b22637303b29821c3384e5f78a8ff348b4e44efe3` |
| `libgl1-mesa-dri_22.3.6-1+deb12u2_amd64.deb` (build-inputs/apt-runtime) | `350e4263aaacd521f6f757951262b57c003930edaf0419508f2c5675317577fe` |
| `libglx-mesa0_22.3.6-1+deb12u2_amd64.deb` (build-inputs/apt-runtime) | `88e7ffa61f1a0a86c30736ad7657ab4774c9b711bbaff76cef0bd32ffb85ec90` |
| `libglx0_1.6.0-1_amd64.deb` (build-inputs/apt-runtime) | `95f568df73dedf43ae66834a75502112e0d4f3ad7124f3dbfa790b739383b896` |
| `libgl1_1.6.0-1_amd64.deb` (build-inputs/apt-runtime) | `6f89b1702c48e9a2437bb3c1ffac8e1ab2d828fc28b3d14b2eecd4cc19b2c790` |
| `libxrender1_1%3a0.9.10-1.1_amd64.deb` (build-inputs/apt-runtime) | `b2a6d160578f359220101947c8d67059b44704a251e688de1d0b744fefedfd1a` |
| `eclipse_sumo-1.27.1-py3-none-manylinux_2_28_x86_64.whl` (build-inputs) | `fd96ace26bccb78312c8c04464c0894a5432db8e2c072388b73e8ae3a6a43586` |
| `sumo_data-1.27.1-py3-none-any.whl` (build-inputs) | `2e547fd20558e07d6004d576eeaf85f4aa061efb4a9bab7675233726364fba61` |
| `sumolib-1.27.1-py2.py3-none-any.whl` (build-inputs) | `2cc63e75de8db9219ab1a742a4f8870d1d2199881dcc006a0afbcdbb1e463796` |
| `traci-1.27.1-py2.py3-none-any.whl` (build-inputs) | `b4f4fb1cd145461c318aa62df16cfd48494a46631a8c3b62348b901def670c9e` |

Build/source context fingerprints:

| File | SHA-256 |
|---|---|
| `Dockerfile` | `1f3b0ceef245e3cefded48148d104cafe0627946af160dc1d00471b6314d00e3` |
| `.dockerignore` | `64d17a0a15d66396a1de7b214d3b447ca896be7c279457d9458dda9c9895c094` |
| `apt-install.sh` | `09b1a103d1dd6f2bad9ad4bbe28d999260222a6c365f8a0b5df67f2f7a0ac18f` |
| `service/__init__.py` | `466c14f617e10f60d70e15993ea83cdbe6dd8b4acf180dfc371f0887b62fd085` |
| `service/__main__.py` | `93d8aae0b5484be0a723504fe9853c450557d8b5dccb73fa8bbb139f09434ef3` |
| `service/commands.py` | `485a156fe3d0e574beecaefd0559da723b5eb7ebf059bc7d312615c8c0ae2bb5` |
| `service/rpc.py` | `1d9272696ad79fa3d0e60bedaf2964c2a60b5637a02d1a0ad39d735b7cd4d0b3` |
| `service/runtime.py` | `d38b350caf12517db95c5a7d893064a4771368898bd41ce12ff428a41be09f7f` |
| `service/scenario.py` | `4e08fc99e8804d0fadf6d5b8cd38dff0a366724fe79b5f8b689121394e1591bb` |
| `service/wire.py` | `ed822d522bb420797db881e467897713ab51d97026d961661495e7c5d20f2a9e` |
| `requirements.lock` | `1018386ff002e71e9085cba763e98c780982d0cedf6facb27d098a0f86cee271` |

### ns3

| Input / stage | SHA-256 |
|---|---|
| `libxml2_2.9.14+dfsg-1.3ubuntu3.10_amd64.deb` (build-inputs/apt-builder) | `c5a065f85c1e6f605741e365e40cad71c3ceb0e85e2791400bd35b8896e12ad1` |
| `libc6_2.39-0ubuntu8.9_amd64.deb` (build-inputs/apt-builder) | `ff5557d99b51f761c4b7c92368b9cc45565eda17df9bf9eb4b134d09825008be` |
| `libc-bin_2.39-0ubuntu8.9_amd64.deb` (build-inputs/apt-builder) | `066398a06ef1a218d4109461d4c636eafe83322ad19b179de9ef7af42b3fec58` |
| `libpython3.12-minimal_3.12.3-1ubuntu0.17_amd64.deb` (build-inputs/apt-builder) | `d646ad7112b5adec21ba0e1af015f04ae8ca7f5efdc619622547dd6673c4c14b` |
| `libexpat1_2.6.1-2ubuntu0.6_amd64.deb` (build-inputs/apt-builder) | `494b8e672f722130c6bca6a7bc4cc31a43ca891a31d60d868bfdd699a3c20b13` |
| `python3.12-minimal_3.12.3-1ubuntu0.17_amd64.deb` (build-inputs/apt-builder) | `d452689b9660845345a4c3e05e4ad82c082d5474e04031b7aa47f1a6d5610a6e` |
| `python3-minimal_3.12.3-0ubuntu2.1_amd64.deb` (build-inputs/apt-builder) | `9ba5dd55cdcf6121c147dc5cb87403169b5933e3a56da0de57774f2ce819cf06` |
| `media-types_10.1.0_all.deb` (build-inputs/apt-builder) | `31bfb7eec55ab6d34a50ba995150e1498d4cb897714085d8025e330d3b529747` |
| `netbase_6.4_all.deb` (build-inputs/apt-builder) | `8cdbc9c3dca01e660759bf9d840f72e45ac72faf5d19ca1faecacaf6a60c1a87` |
| `tzdata_2026c-0ubuntu0.24.04.1_all.deb` (build-inputs/apt-builder) | `ef12c9ef81905b5ac558504b2f8c20da9be5f28861020b3fb5e8b15fb3fae2f6` |
| `libbz2-1.0_1.0.8-5.1ubuntu0.1_amd64.deb` (build-inputs/apt-builder) | `36ee08b00b1ee5018b2fce02881b7d2d73c9b4e32990bde3388400d86c48126a` |
| `readline-common_8.2-4build1_all.deb` (build-inputs/apt-builder) | `879bfd7f8a9bc4c0f7cdc777cdd8bc6de5f8c4a2ac80c060322a1b22f13504bb` |
| `libreadline8t64_8.2-4build1_amd64.deb` (build-inputs/apt-builder) | `563977a16df03b611f5239cc1e9a0426e86479fcc616b5c9e200ea32063119e5` |
| `libsqlite3-0_3.45.1-1ubuntu2.8_amd64.deb` (build-inputs/apt-builder) | `b1190bb72359f5fcc47406aa46065eaf4f1ca208085c51224a52b04bedc0b4bb` |
| `libpython3.12-stdlib_3.12.3-1ubuntu0.17_amd64.deb` (build-inputs/apt-builder) | `45d3f530ba1f9d6e879ad46b92046fabab13fe50a82450e4f65557a3bbad1489` |
| `python3.12_3.12.3-1ubuntu0.17_amd64.deb` (build-inputs/apt-builder) | `6745c9463432e619d7402b117ad4ac86c31dcd3999ba20daa9e395f6f9909d86` |
| `libpython3-stdlib_3.12.3-0ubuntu2.1_amd64.deb` (build-inputs/apt-builder) | `57ebb378c59d2f9ef479bee0abc933c560878cc10e2c3af8e05d6fdb2ed63da4` |
| `python3_3.12.3-0ubuntu2.1_amd64.deb` (build-inputs/apt-builder) | `e691b9cc40841c41bbdc50bd794c876cb1b1801306ea27b06e9a1458180df1e9` |
| `libkrb5support0_1.20.1-6ubuntu2.10_amd64.deb` (build-inputs/apt-builder) | `9224ede3246a82a845ea97652ecfae57f88e8d8f520d8a186f9157167574fb34` |
| `libk5crypto3_1.20.1-6ubuntu2.10_amd64.deb` (build-inputs/apt-builder) | `273f7cc95a68d927d7f71c3e78b7717a16a8d86e646a206bae7bf797150ae9db` |
| `libkeyutils1_1.6.3-3build1_amd64.deb` (build-inputs/apt-builder) | `0679f198b0128179e46cdf956fb2022c23c758664c00bc8efa0382d509683a8a` |
| `libkrb5-3_1.20.1-6ubuntu2.10_amd64.deb` (build-inputs/apt-builder) | `60b48c5a3233f1d8caba30d6573edc8e131911ab6d63adcd664f7cbe62708362` |
| `libgssapi-krb5-2_1.20.1-6ubuntu2.10_amd64.deb` (build-inputs/apt-builder) | `0d4a0187bcdbbe3e6a10bfa574100087d12052d0d0ef28614fd6210021c587b2` |
| `libicu74_74.2-1ubuntu3.1_amd64.deb` (build-inputs/apt-builder) | `c9a70989678660eed9a1e904c74fa043da8bec8e2036856fc16e31ced79b04f8` |
| `libjansson4_2.14-2build2_amd64.deb` (build-inputs/apt-builder) | `0cf79113f5d193ce9af2be2ff4b2c3b30dd4e55a0b6c47f7d28f6c849ff3aa60` |
| `libnghttp2-14_1.59.0-1ubuntu0.4_amd64.deb` (build-inputs/apt-builder) | `73adcbb9df32cd7c9d46b1c7598bbbbcf249be705d62d966694cfc9976a15dfe` |
| `libpsl5t64_0.21.2-1.1build1_amd64.deb` (build-inputs/apt-builder) | `a6c85d1303ae90b6a3209d73c4f047f82c27cdc963c48adfd95dd7abca64f039` |
| `libuv1t64_1.48.0-1.1build1_amd64.deb` (build-inputs/apt-builder) | `72a1856b30af1e533494ab90742a2446f4491b15ddebcade6661a6e8d61b4359` |
| `wget_1.21.4-1ubuntu4.5_amd64.deb` (build-inputs/apt-builder) | `2aa5a734b21b49ff6bd2239546b3f88275f8abb55dd8a984249e6cd0c1173a92` |
| `binutils-common_2.42-4ubuntu2.10_amd64.deb` (build-inputs/apt-builder) | `d136073f5e2153f3df11c1d08d66727b9466b28ff483f50085f14bbe3464b5ee` |
| `libsframe1_2.42-4ubuntu2.10_amd64.deb` (build-inputs/apt-builder) | `72093fb456864db55f1352bfa5e952a94f7abaff64e71dff1fbf001db1984564` |
| `libbinutils_2.42-4ubuntu2.10_amd64.deb` (build-inputs/apt-builder) | `064dce00ce94e1fc2d33779cb0071088f4c8aac79e85345f2e78a020f7d14699` |
| `libctf-nobfd0_2.42-4ubuntu2.10_amd64.deb` (build-inputs/apt-builder) | `da352eb7fa6c4369d2a6c1e5e680f574eda2e38576563326b18d9e47e61c4078` |
| `libctf0_2.42-4ubuntu2.10_amd64.deb` (build-inputs/apt-builder) | `7ec86d697c3668503c85f308a6832f092075b5880ad002f22185264da0bd4645` |
| `libgprofng0_2.42-4ubuntu2.10_amd64.deb` (build-inputs/apt-builder) | `1b7e3c2fc162e8358ca6e5a3fffdb4d0d632f790630323215841bb36a63c0ab8` |
| `binutils-x86-64-linux-gnu_2.42-4ubuntu2.10_amd64.deb` (build-inputs/apt-builder) | `1e510a15f30208d39edcd840e48f26a77bbca7c417805eeccb1e3f7de198ef29` |
| `binutils_2.42-4ubuntu2.10_amd64.deb` (build-inputs/apt-builder) | `b3b5a84181a38fd191820b2cdcc1a3eeb1cd6333ad472f2092f96e81047e9c74` |
| `bzip2_1.0.8-5.1ubuntu0.1_amd64.deb` (build-inputs/apt-builder) | `60fb79214dd8366fa39d7e69911294e72b9f1a4425a3d22f211359c8626e4b9e` |
| `libarchive13t64_3.7.2-2ubuntu0.9_amd64.deb` (build-inputs/apt-builder) | `b4e618d2237da9b00b88947113ebad8f93134f07f670a2df59cc5201c6c4307f` |
| `libbrotli1_1.1.0-2build2_amd64.deb` (build-inputs/apt-builder) | `74492419b8fda803774b8c9acef6afc5d2f9ff31782635aae212906adae7b277` |
| `libsasl2-modules-db_2.1.28+dfsg1-5ubuntu3.1_amd64.deb` (build-inputs/apt-builder) | `1f13548b1774cd9c70c50b8c3267204a101334a4d2f979338896ba5a4c6f81b8` |
| `libsasl2-2_2.1.28+dfsg1-5ubuntu3.1_amd64.deb` (build-inputs/apt-builder) | `eda097f98dcb3a08b9ce157d6191d140e4885c1cba47b683c94b8ca45e88f458` |
| `libldap2_2.6.10+dfsg-0ubuntu0.24.04.1_amd64.deb` (build-inputs/apt-builder) | `7f3f8e565401256f21d5aa562c9f92dbb63537b73aaea6283e4db5264f1598f4` |
| `librtmp1_2.4+20151223.gitfa8646d.1-2build7_amd64.deb` (build-inputs/apt-builder) | `967a39dbc14236d1580ede01d80fd78444668572e716734e1ac66c175052594e` |
| `libssh-4_0.10.6-2ubuntu0.5_amd64.deb` (build-inputs/apt-builder) | `2448ce5f99e49721d12f8b227f2472dd08ed7a78184dd819fd03a42693024a16` |
| `libcurl4t64_8.5.0-2ubuntu10.15_amd64.deb` (build-inputs/apt-builder) | `02f8f39727a43d5a7057cba35cda866be00d929b91ea67ed02dd9d6402fa551c` |
| `libjsoncpp25_1.9.5-6build1_amd64.deb` (build-inputs/apt-builder) | `8efea5b75952f3ad1c40e6b5b31de687c297ea29f87f8b63efebb55e13467211` |
| `librhash0_1.4.3-3build1_amd64.deb` (build-inputs/apt-builder) | `e9ee69963ff1a56378b9c5ffdd21ea0feaec9647c522349b7612b25103490528` |
| `cmake-data_3.28.3-1build7_all.deb` (build-inputs/apt-builder) | `20a3b644211ce82f35c24f1f5052199adeb0ac159978a8740fd6c0959611557f` |
| `cmake_3.28.3-1build7_amd64.deb` (build-inputs/apt-builder) | `4b0a7f8c0daf27b26b46997d994ae5d1ee7a3d11dfcda9f7627bb1462c162295` |
| `gcc-13-base_13.3.0-6ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `e859aca26585bb91113a451f1e66bc0e5283cb08797d679aacc1d936ae6dff8e` |
| `libisl23_0.26-3build1.1_amd64.deb` (build-inputs/apt-builder) | `4e040926e50fb961fae9bf95660189d468336a4a17bc321872c434fc8f777e7f` |
| `libmpfr6_4.2.1-1build1.1_amd64.deb` (build-inputs/apt-builder) | `aebc1c8b69a1f98bb43dfc268daecd181116dbf40b13ee4e822eb4bdd52b493a` |
| `libmpc3_1.3.1-1build1.1_amd64.deb` (build-inputs/apt-builder) | `cebe6098bb3d66fdacac9dc6fe406a651216d9c00f27c3f9c159d15d96cdf864` |
| `cpp-13-x86-64-linux-gnu_13.3.0-6ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `2ca48bf0c2d6465bc39322899715a85d934b4d7442dd5586a7bebbe3ce0f806b` |
| `cpp-13_13.3.0-6ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `c7535331fbb183c802c3bf4b6b210872dcc12d0421b3212b3c4b940f2c59ed3a` |
| `cpp-x86-64-linux-gnu_4%3a13.2.0-7ubuntu1_amd64.deb` (build-inputs/apt-builder) | `85059b30960de3582e8612740614da3dfe47241d0368a28dea686188cf7648dd` |
| `cpp_4%3a13.2.0-7ubuntu1_amd64.deb` (build-inputs/apt-builder) | `b51f8094760f7b41afdcb1fe1b5a57fc64b75a090859918af17450a10f8c7d31` |
| `libcc1-0_14.2.0-4ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `454456436ca767817a860557263d7cc2489f0a410f03efff4c0bb236d579ec09` |
| `libgomp1_14.2.0-4ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `e8a95ec58125b4933597f30ff56c2ae10edf90f287262e366d4b6edea3019144` |
| `libitm1_14.2.0-4ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `1fca498129dd3510294809d77ee754f72a9de281111200e9b7b9a5adf37faa9f` |
| `libatomic1_14.2.0-4ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `fe49cbbc7be753528380c724a8eef5f1e31dffa9221f692c5069048d81c7449d` |
| `libasan8_14.2.0-4ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `8321aac6230fa1da320e76eb6288b7436164624aec449ed3933ea6c4cc86daac` |
| `liblsan0_14.2.0-4ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `dc0c2a1a053e833ba4d71e1ec2ba4244fe301761ad4a91d6725f927a52d86a14` |
| `libtsan2_14.2.0-4ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `8cbcc9b3ae5ef23b449383d47a9035b27596e307d7dce7df9b83d47a7acd1d91` |
| `libubsan1_14.2.0-4ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `a16dea3abe2dcac99bcfae27e7e5672fde64573c3c170dcb0cd55631238f9814` |
| `libhwasan0_14.2.0-4ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `2195318cfe68fe16b601913ef7b33c9a900372f57861643fdc9ae6fef84534cd` |
| `libquadmath0_14.2.0-4ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `dc8f0ca542e09d662f29370c8393c016440dd4bc5c996c5fcc19f632b63ce3b0` |
| `libgcc-13-dev_13.3.0-6ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `cd689db2691edaa10f37329307292796bb599e722e0505c79e14caaa1fe9a93a` |
| `gcc-13-x86-64-linux-gnu_13.3.0-6ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `a134b0319a82d14581b3a14820d2832af4ec9778ed8b9b4ddaeecfb0555ec325` |
| `gcc-13_13.3.0-6ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `7438ff160b020a74970672189ecae25d0ca650de6d7f543f12a3134192cffbd9` |
| `gcc-x86-64-linux-gnu_4%3a13.2.0-7ubuntu1_amd64.deb` (build-inputs/apt-builder) | `72e79089a10e381360bfc6f03c5e5d8c2ff177d6dbac2cd7ffb3cc1383f57591` |
| `gcc_4%3a13.2.0-7ubuntu1_amd64.deb` (build-inputs/apt-builder) | `0e0bb8b25153ed1c44ab92bc219eed469fcb5820c5c0bc6454b2fd366a33d3ee` |
| `libc-dev-bin_2.39-0ubuntu8.9_amd64.deb` (build-inputs/apt-builder) | `77db23aba4582d034d9a95e4818a16e7e51a2d4fb17a9204439688e288d67f4c` |
| `linux-libc-dev_6.8.0-146.146_amd64.deb` (build-inputs/apt-builder) | `1a7bb04132d01cd14bea3d615e5861096ca1f70c4659902eb14b36c21993cadc` |
| `libcrypt-dev_1%3a4.4.36-4build1_amd64.deb` (build-inputs/apt-builder) | `2edff420ef80b4a3f3751e65c33423ef30e563122a58b759e4854ea8d84ba1b1` |
| `rpcsvc-proto_1.4.2-0ubuntu7_amd64.deb` (build-inputs/apt-builder) | `7eb710fe148d224c159ddec1ceb0ba53ead52a80a6793dcdae1474acf20d8f71` |
| `libc6-dev_2.39-0ubuntu8.9_amd64.deb` (build-inputs/apt-builder) | `e13d5fcc1b2a86f75bca8e0026a8e39f24fe97ca86e92be79991f8697eb1306f` |
| `libstdc++-13-dev_13.3.0-6ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `ee5633e863e19c3381ed97842ce35ed32ede96a3d1ae4e94c051d3036fe21347` |
| `g++-13-x86-64-linux-gnu_13.3.0-6ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `0bd6af6164252d4ea9170d201ee4c10a3120fe0fc04985a00be3bc9076353844` |
| `g++-13_13.3.0-6ubuntu2~24.04.1_amd64.deb` (build-inputs/apt-builder) | `0f3ef964b8a91a9cd7bebfd126839a21941c55547cadcf4aa00204bc8cc43985` |
| `g++-x86-64-linux-gnu_4%3a13.2.0-7ubuntu1_amd64.deb` (build-inputs/apt-builder) | `145b027a542db5b21f85d4f9e242f51c62c69914fc484adb3a4881ca8e2f3913` |
| `g++_4%3a13.2.0-7ubuntu1_amd64.deb` (build-inputs/apt-builder) | `800a84b369c64b18d65f0fb5c1533de0446c880ee4f1cadad64e3363327f63f0` |
| `make_4.3-4.1build2_amd64.deb` (build-inputs/apt-builder) | `1fe6a815b56c7b6e9ce4086a363f09444bbd0a0d30e230c453d0b78e44b57a99` |
| `ninja-build_1.11.1-2_amd64.deb` (build-inputs/apt-builder) | `6a17f76a0f75586f1292f6c61a9b07fd0dec963baf6a102b17673f601c6b323a` |
| `libssl3t64_3.0.13-0ubuntu3.16_amd64.deb` (build-inputs/apt-ca-bootstrap) | `219f43b1cd836a4da550938db5fda93160d269d39a4edcf1f1ce698a470db797` |
| `openssl_3.0.13-0ubuntu3.16_amd64.deb` (build-inputs/apt-ca-bootstrap) | `675b84971ffd4467707008c25ef7520f90ea7c23ef27b7a76b0dccf1d7c4dc3f` |
| `ca-certificates_20260601~24.04.1_all.deb` (build-inputs/apt-ca-bootstrap) | `6bac2a01979e210d9eac1d4d56747ec709ea60654744d66705dc3c36e7629e50` |
| `aero-ns3-provider.cc` (build-inputs) | `73765241a0e8d55c1995a95e0fefefc3a2ca4b5dd5fc765d4b63893ab7815587` |
| `scene.h` (build-inputs) | `c973af9ec8c2a0edad66d971c1506d6e1e5f52aad2e0f6c1c21e4d66812baa88` |
| `aeroagentsim-bundle.py` (build-inputs) | `b96ba7aff5b106c5f83492eca51601470b9a52da5840c3f698f85393bd5ac395` |

Build/source context fingerprints:

| File | SHA-256 |
|---|---|
| `Dockerfile` | `7f6e3c3fa93d16b6a7932b07f7a6e5901a0e6bc411faa883cd79ce588dc3ce9e` |
| `.dockerignore` | `7b085c538bdd4fc9b1ef2d9308303b2807b66afefda29bed6502b99fe84cfd82` |
| `apt-install.sh` | `09b1a103d1dd6f2bad9ad4bbe28d999260222a6c365f8a0b5df67f2f7a0ac18f` |
| `service/__init__.py` | `20260cd84fd43183dc7ff56e0658c13561f61bebd6f5c4f164be10d47ae5402d` |
| `service/config.py` | `2ead5d8f57c8b9b4751d7bf9d471f7ba5021e1848b932dc25fc343e92e4ec0c3` |
| `service/rpc.py` | `2aaf4796bd949d914701ba2d93444cf24f0170ebee63e022cee2ffb8f65b1a7c` |
| `service/runtime.py` | `36420c813890a73da4f8f4615bc6cead19a42306576046b79e4fe38d575c79cf` |
| `service/wire.py` | `ca6e5d59bbbe9aed1d04c5bd7ec83a35d5cff07a6daf86abf24b39d0060e1aaa` |
| `bundle.py` | `b96ba7aff5b106c5f83492eca51601470b9a52da5840c3f698f85393bd5ac395` |
| `native/aero-ns3-provider.cc` | `73765241a0e8d55c1995a95e0fefefc3a2ca4b5dd5fc765d4b63893ab7815587` |
| `native/scene.h` | `c973af9ec8c2a0edad66d971c1506d6e1e5f52aad2e0f6c1c21e4d66812baa88` |


Large raw smoke outputs (PX4 smoke-metrics.json, SUMO run-a.jsonl.gz) are kept outside git under
`/mnt/data2/weizhiwei/aeroagentsim/artifacts/containers/` at the same relative paths.
