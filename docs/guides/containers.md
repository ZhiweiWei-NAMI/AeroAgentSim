# Native simulator containers

PX4/Gazebo, SUMO and ns-3 run in separate service containers. Host adapters coordinate their clocks and publish their results through the shared kernel. Docker is optional for the default traffic demo.

## Build the services

From the repository root:

```bash
containers/px4-gazebo/build.sh
containers/sumo/build.sh
containers/ns3/build.sh
```

These scripts produce `aeroagentsim/px4-gazebo:standalone`, `aeroagentsim/sumo:standalone` and `aeroagentsim/ns3:standalone`. The [Dockerfiles](../../containers/) specify the simulator versions and build dependencies. Builds target Linux amd64 and can take substantially longer than the Python installation. The scripts forward existing HTTP/HTTPS proxy variables; Docker daemon pulls need their own network configuration.

## Choose managed or manual execution

**Managed execution** starts and cleans up the images declared by the adapter scenario, using available loopback ports:

```bash
python -m aeroagentsim.adapters.runner scenarios/adapters/sumo-grid.yaml \
  --containers --journal /tmp/sumo-example.jsonl
```

The [coupled scenario](../../scenarios/adapters/coupled.yaml) combines SUMO and ns-3. The [flight scenario](../../scenarios/adapters/px4-flight.yaml) configures PX4/Gazebo. The runner closes services and checks replay after execution.

**Manual execution** is useful for debugging one service:

```bash
docker run -d --name aas-sumo --cpus 4 --memory 8g \
  -p 127.0.0.1:19002:9000 aeroagentsim/sumo:standalone
python -m aeroagentsim.adapters.runner scenarios/adapters/sumo-grid.yaml \
  --journal /tmp/sumo-example.jsonl
docker rm -f aas-sumo
```

Keep adapter `host`/`port` consistent with the published port. Omit the managed `image` setting for your own manually configured scenario, and do not add `--containers` while attaching to the manual service. Each service listens on container port 9000 and owns one world per connection. Parallel experiments need separate containers. Bind experimental services to loopback unless you deliberately configure remote access.

## Supply your own data

For SUMO, mount a network/configuration directory read-only:

```bash
docker run -d --name aas-sumo -v "$PWD/data:/data:ro" \
  -p 127.0.0.1:19002:9000 aeroagentsim/sumo:standalone
```

Configure reset with `{"scenario": {"kind": "config", "path": "/data/city.sumocfg"}}`. Configuration-relative files must resolve inside the container. PX4/Gazebo can use an image world or a mounted SDF with ENU spherical coordinates and an explicit physics step. ns-3 needs explicit nodes, positions and seed configuration.

## Service contracts and limits

The adapters require exact advance boundaries, explicit source clocks and typed command outcomes. A transport acknowledgment is not action completion. Stateful native operations are not retried automatically. Disconnection/close cleans up the native world; native errors remain run errors.

PX4 reset includes process launch and warmup. Its MAVSDK streams lack confirmed source simulation timestamps, and PX4/Gazebo is not claimed bitwise deterministic. SUMO publishes network XY coordinates; it needs an explicit conversion owner when a consumer expects ENU. The traffic demo's SUMO/PX4 profiles are configuration contracts, not runnable native substitutions.

Read [Configure PX4/Gazebo](px4-gazebo.md), [Configure SUMO](sumo.md), and the [ns-3 service contract](../../containers/ns3/README.md) for actual configuration. Each backend also provides a smoke client under its container directory; consult its `--help` for available checks.
