# Agent submissions

AERO-BENCH follows the benchmark boundary used by system-level agent benchmarks:
the benchmark owns the task, environment, isolation, evidence contracts, and
independent verifier. It does **not** provide a task-solving policy, expected
answer, model call, or Inspection-specific Agent implementation.

## Benchmark-owned interface

A submission supplies one digest-addressed production image through an
`aero-bench.agent/v1` `AgentSpec`. The executor gives that image only:

- the resolved instruction and explicitly public Agent assets;
- an executor-issued run identity, seed, and Gateway credential;
- the granted tool and observation contracts;
- a private writable artifact volume containing only the Agent's declared
  outputs.

The Agent network can reach the Harness Gateway, but not Provider or Verifier
workloads directly. Verifier-private truth is never mounted into the Agent.

`aero_bench.agent.AgentContext` and `GatewayClient` are the single generic SDK
boundary. They validate executor identity, bundle digests, grants, Gateway
responses, and declared artifact paths. They contain no benchmark-specific
policy.

```python
from aero_bench.agent import AgentContext
from aero_bench.runtime.contracts import SimulationTime

context = AgentContext.from_environment()
with context.gateway() as gateway:
    result = gateway.command(
        command_id="submission.command.1",
        tool_id="a-granted-tool",
        at=SimulationTime(tick=0, sim_time_ns=0),
        arguments={"declared": "arguments"},
    )
```

A submission is responsible for deciding which granted operations to invoke,
interpreting real observations, writing every declared Agent artifact, and
ending each turn with `complete_turn`. Command acceptance is not evidence of
physical execution, network delivery, or task success.

## Inspection v1 validation participant

The committed Inspection v1 bundle pins an externally built participant image
so the release can be executed and independently scored:

```text
127.0.0.1:5000/participants/inspection-validation@
sha256:a9b0795773ea7d096bc89bdadc4f3db2883f32a4fcf2e7565859c7348bf8fc95
```

Its policy source is not part of the benchmark implementation. The bundle's
`AgentSpec` is a submission record, not a benchmark-provided answer Agent.
Benchmark users replace that spec with their own digest-addressed image and
resolve a new run while leaving the task, environment, evidence inventory, and
Verifier unchanged. The exact participant identity and the passed validation
run are recorded separately in `releases/inspection-v1/release-lock.json`.
