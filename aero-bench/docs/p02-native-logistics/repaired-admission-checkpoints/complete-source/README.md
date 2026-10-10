# Native parcel admission repair source packet

The reported temporal, identity, qualifying-window, same-tick facility and
snapshot defects are repaired in the actual Python package. The two affected
test files pass **61/61** cases: 16 coordinator-owned regressions and 45 module
cases. Inputs are synthetic physical observations; this is source-contract
verification, not a native flight or full logistics workflow acceptance.

| Contract check | Current behavior |
| --- | --- |
| Admission time | Closing `SimulationTime` equals admission time in both tick and nanoseconds. Tick 3 / 3 s cannot authorize tick 3 / 100 s. |
| Principal/carrier | Required independently declared principal binds to the declared carrier. `participant.agent` is accepted; `participant.foreign` is rejected without custody transfer. IDs need not match. |
| Qualifying dwell | Invalid, moving or contact-lost samples clear the qualifying suffix. A later contiguous valid suffix can qualify. |
| Same-tick facilities | Each declared facility has its own frontier. Two facilities at the same time are absorbed; a duplicate facility sample is refused. |
| Action identity | Run + principal + action ID keys original outcomes; divergent payload reuse and foreign run/carrier/principal are rejected. |
| Snapshot v2 | Required fields retain both windows, global/per-facility frontiers and original keyed outcomes/tombstones. The independent last-decision time is saved exactly, including after duplicate replay. Missing keyed outcomes raise rather than replaying motion. |

The original ordinary `workbuddy/glm-5.3-flash` session authored the source
repairs in successive narrow checkpoints. The coordinator reran the assertions.
The coordinator updated five obsolete reason-string assertions after the window
reset semantics changed, retaining unconfirmed status and adding zero-transfer
and unchanged-state assertions. No admission policy or tolerance was weakened.
`tests/` preserves the failures before repair and final JUnit output.

`source/` uses repository-relative paths and includes the exact existing signed
pose-reference dependencies used by these tests. `diff/` compares with the
frozen partial source packet at dea2691; that older packet remains unchanged.
`manifest.json` pins every exported file's bytes. Review this packet as a source
overlay on the existing BENCH checkout, not as a replacement application root.

Reproduction in the existing environment:

```sh
/home/weizhiwei/data/iiot_predict/iiot_py311/airfogsim/bin/python -m pytest \
  tests/tasks/test_native_parcel_review_regressions.py \
  tests/tasks/test_native_parcel_runtime.py -q --tb=short
```

Native independent review is pending. Do not enable or launch the logistics run
from these module tests. Transition RPC, closed-stage hook activation, parcel
SceneState projection and the dedicated sealed logistics verifier/entrypoint
remain integration work. Custody here is modeled business state, not a physical
gripper or cargo aerodynamics claim. The accepted inspection run and reevaluation,
current service, other workers' files, P01 and P09 inputs remain untouched.
