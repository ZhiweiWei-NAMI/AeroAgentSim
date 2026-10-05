# Gazebo airspace transition system

`aero_bench_gazebo_airspace_transition` is a Gazebo Sim 8 `System` plugin. It
reads the configured incident model's `components::Name` and
`components::Pose` from the ECM in `PostUpdate`; it does not add a model,
visual, collision, sensor, or other world geometry.

Attach the plugin to the world SDF and provide every field below. Coordinates
are ENU metres in the world frame, with inclusive bounds:

```xml
<plugin filename="libaero_bench_gazebo_airspace_transition.so"
        name="aero_bench::gazebo::AirspaceTransition">
  <world_id>city.demo</world_id>
  <world_digest>0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef</world_digest>
  <region_id>airspace.no-fly.1</region_id>
  <region_digest>abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789</region_digest>
  <incident_vehicle>x500_mono_cam_0</incident_vehicle>
  <transition_topic>/aero_bench/airspace/transitions</transition_topic>
  <min_east_m>-10</min_east_m>
  <max_east_m>10</max_east_m>
  <min_north_m>-10</min_north_m>
  <max_north_m>10</max_north_m>
  <min_up_m>0</min_up_m>
  <max_up_m>20</max_up_m>
</plugin>
```

The plugin publishes a `gz::msgs::String` on the configured Gazebo transport
topic. Its `data` is a JSON object with `schema_version`
`aero-bench.gazebo-airspace-transition/v1`, `source` `gazebo.system`,
`sequence`, `transition` (`entered` or `exited`), non-negative `sim_time_ns`,
world and region IDs plus SHA-256 digests, the incident model name, and the
observed ENU position. The first observed sample inside the region emits
`entered`; an inside-to-outside change emits `exited`; later re-entry starts a
new monotonic sequence. A model that is never observed does not produce
telemetry.

The container builds this target against the image's available `gz-sim8`,
`gz-transport13`, `gz-msgs10`, and `gz-plugin2` CMake packages. The build is
performed with network access disabled after package installation; no source
or dependency download fallback exists.
