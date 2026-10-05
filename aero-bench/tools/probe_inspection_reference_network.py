#!/usr/bin/env python3
"""Compare native links for measured airborne and proposed landed reference poses.

This is a diagnostic experiment, not formal Provider/evidence acceptance. It
uses the failed attempt's actual report bytes and native image without changing
its radio profile, loss model, rate, delay or seed. No host path or Docker socket
is passed to the diagnostic workload.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aero_bench.config.resolver import ResolvedRunSpec  # noqa: E402
from aero_bench.world.frame_math import EnuTransform  # noqa: E402


PROBE = r"""
import asyncio
import importlib.util
import json
import sys

sys.path.insert(0, "/opt/aero-bench")
spec = importlib.util.spec_from_file_location("native_ns3", "/opt/aero-bench/ns3-server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
data = json.load(sys.stdin)
scenario = server.ValidatedWorkloadScenario(
    scenario_digest=data["scenario"]["scenario_digest"],
    scenario=data["scenario"], assets=(),
)
provider = next(item for item in data["scenario"]["providers"] if item["provider_id"] == "network")
projection = server._validate_network_projection(
    scenario, provider_id="network", supported_wifi_standards=("802.11ax",),
    capabilities=tuple(provider["capability_ids"]),
)
b64 = server.b64
profiles = ";".join(
    ",".join((b64(p.radio_profile_id), b64(p.provider_id), b64(p.wifi_standard),
        str(p.frequency_mhz), str(p.channel_number), str(p.channel_width_mhz),
        b64(p.band), str(p.tx_power_dbm), str(p.rx_sensitivity_dbm),
        b64(p.data_mode), b64(p.control_mode), str(p.max_data_rate_bps)))
    for p in projection.radio_profiles
)
nodes = ";".join(
    ",".join((b64(n.node_id), b64(n.entity_id), b64(n.endpoint_id), b64(n.radio_profile_id),
        *(str(v) for v in n.position_enu_m))) for n in projection.node_bindings
)
links = ";".join(
    ",".join((b64(l.link_id), b64(l.source_node_id), b64(l.destination_node_id),
        str(l.data_rate_bps), str(l.propagation_delay_ns))) for l in projection.links
)

async def experiment(case):
    backend = server.Ns3Backend()
    await backend.start()
    try:
        await backend.request(["PREPARE", b64("network"), b64(server.PROTOCOL_VERSION),
            b64(data["image"]), b64(server.NS3_VERSION), b64(server.NS3_COMMIT),
            b64(server.NETWORK_MODEL), profiles, nodes, links])
        await backend.request(["RESET", str(data["seed"])])
        positions = {n.node_id: n.position_enu_m for n in projection.node_bindings}
        uav = next(n for n in projection.node_bindings if n.entity_id == data["vehicle_id"])
        positions[uav.node_id] = case["position"]
        node_token = ";".join(
            ",".join((b64(n.node_id), *(str(v) for v in positions[n.node_id])))
            for n in projection.node_bindings
        )
        losses = {}
        for link in projection.links:
            losses[link.link_id] = sum(v.attenuation_db for v in projection.propagation_volumes
                if server._segment_intersects_volume(
                    positions[link.source_node_id], positions[link.destination_node_id], v))
        link_token = ";".join(
            ",".join((b64(link.link_id), str(losses[link.link_id]))) for link in projection.links
        )

        async def step(time_ns):
            scene_digest = server.digest({"diagnostic_case":case, "time_ns":time_ns})
            await backend.request(["MOBILITY", scene_digest, node_token, link_token])
            return await backend.request(["STEP", str(time_ns)])

        await step(203_000_000_000)
        facts = []
        deliveries = []
        for offset in range(2):
            send_ns = 203_000_000_000 + offset * 1_000_000_000
            await backend.request(["MESSAGE", b64("diagnostic.message."+str(offset)),
                b64(uav.node_id), b64(data["destination_node_id"]), data["payload_base64"],
                str(send_ns // 500_000_000), str(send_ns)])
            for target in (send_ns+500_000_000, send_ns+1_000_000_000):
                response = await step(target)
                facts.append(response[0])
                deliveries.extend(line.split()[6] for line in response if line.startswith("DELIVERY "))
        response = await step(263_000_000_000)
        facts.append(response[0])
        deliveries.extend(line.split()[6] for line in response if line.startswith("DELIVERY "))
        print(json.dumps({"diagnostic_only":True, "case":case["id"],
            "source_position_enu_m":case["position"], "link_attenuation_db":losses,
            "profile_data_mode":projection.radio_profiles[0].data_mode,
            "payload_bytes":data["payload_bytes"], "submitted":2,
            "native_facts":facts, "arrival_times_ns":deliveries}, sort_keys=True), flush=True)
    finally:
        await backend.close()

async def main():
    for case in data["cases"]:
        await experiment(case)

asyncio.run(main())
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--failure-evidence", type=Path, required=True)
    args = parser.parse_args()
    run = ResolvedRunSpec.model_validate_json(
        (args.bundle / "resolved-run.json").read_bytes()
    )
    report = (args.failure_evidence / "agent/report.json").read_bytes()
    trajectory = json.loads(
        (args.failure_evidence / "flight/trajectory.json").read_bytes()
    )
    measured = trajectory[-1]
    if measured["run_id"] != run.run_id:
        raise ValueError("measured trajectory belongs to another run")
    origin = run.scenario.frame_authority.origin
    transform = EnuTransform.from_origin(
        longitude_deg=origin.wgs84.longitude_deg,
        latitude_deg=origin.wgs84.latitude_deg,
        altitude_m=origin.wgs84.ellipsoid_height_m,
    )
    wgs84 = measured["position_wgs84"]
    position = transform.geodetic_to_enu(
        longitude_deg=wgs84["longitude_deg"],
        latitude_deg=wgs84["latitude_deg"],
        altitude_m=wgs84["altitude_m"] + origin.geoid_separation_m,
    )
    launch = next(
        site for site in run.scenario.launch_sites if site.selected
    ).pose.position.enu
    network = run.scenario.network
    policy = json.loads((args.bundle / "agent/reference-policy.json").read_bytes())
    destination = next(
        node
        for node in network.node_bindings
        if node.endpoint_id == policy["destination_endpoint_id"]
    )
    data = {
        "scenario": run.scenario.model_dump(mode="json"),
        "image": next(
            p.workload.runtime.image
            for p in run.environment.providers
            if p.provider_id == "network"
        ),
        "seed": run.seed,
        "vehicle_id": measured["vehicle_id"],
        "destination_node_id": destination.node_id,
        "payload_base64": base64.b64encode(report).decode("ascii"),
        "payload_bytes": len(report),
        "cases": [
            {
                "id": "airborne-measured",
                "position": [position.x, position.y, position.z],
            },
            {
                "id": "landed-proposed",
                "position": [launch.east_m, launch.north_m, launch.up_m],
            },
        ],
    }
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--read-only",
            "--user",
            "65532:65532",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--network",
            "none",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--entrypoint",
            "python3",
            data["image"],
            "-c",
            PROBE,
        ],
        input=json.dumps(data).encode("utf-8"),
        check=True,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError("native reference link experiment failed")


if __name__ == "__main__":
    main()
