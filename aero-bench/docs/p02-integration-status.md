This isolated AeroAgentSim branch carries the current BENCH integration source.
The application root/default branch and active sealed-v8 service are unchanged.
It is an integration preparation branch, not completed AeroAgentSim replacement.

Implemented source includes the original ThreeJS/GLB map presentation, light
bilingual Run/Configuration navigation, selected-frame entity/business bindings,
configuration compilation loading, authenticated Control client, bounded shared
asset queue, and nonsecret Start identity persistence. The native route remains
the actual digest-pinned PX4/Gazebo, ns-3 and SUMO provider-barrier runtime;
there is no mock backend or recreated WareTrack city.

Focused frontend verification passed: typecheck and 45 tests in five files in
this worktree. The matching server frontend production build also passed.
The authoring compilation-root fix and its focused tests are included. No
node_modules, private OSM/map fixtures, GLB assets or credentials are committed.
Those assets are supplied by the existing authorized server installation.

The new native run is
`9e07a000f35961e5d4bca89553729a8e743143781aad6d213fed722abfd324fd`,
bound to compilation
`a504dba5ab6c8c2183b5ce419173f89bd03289860e62ffeee760ff529417c610`.
Its authoritative ledger records runtime completion at tick300/150s, with
one aircraft, `uav.inspector`, landed and disarmed. Two facade work orders
are not two aircraft. The small evidence packet on
`codex/aerobench-source-config-20261005`, commit
`2ccb5d0e3afbfeaaa3d9838e65def5105ce7bdad`, is under
`aero-bench/docs/coordination-checkpoint-20261005/p02/sealed-native-run-9e07a000/`.

Pending: same-run independent verifier report, normal public projection,
asset-ready map/GIF of that new run, and root AeroAgentSim interface migration.
The previous browser closed before preserving its Start ID; the persistence fix
does not retroactively recover that identity or bypass authentication. Existing
configuration roundtrip/map restoration screenshots refer to the older sealed
run and are not evidence of new-run live UI acceptance. No default-branch merge
is authorized until runtime and visual acceptance are complete.
