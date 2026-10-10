"""Lower scheduled editor restrictions against the run's declared SUMO capability."""

from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

from aero_bench.authoring.compilation_contracts import CompilationBlocker
from aero_bench.config.loader import BundleReader
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.providers.sumo.config import SumoTrafficRestriction, TRAFFIC_RESTRICTION_CAPABILITY


def lower_traffic_events(events, *, run: ResolvedRunSpec, root: Path):
    providers = [item for item in run.environment.providers if item.adapter == "sumo.traci"]
    capable = len(providers) == 1 and TRAFFIC_RESTRICTION_CAPABILITY in providers[0].capabilities
    lowered = []
    blockers = []
    edge_ids = set()
    if capable:
        if run.scenario.sumo is None:
            raise ValueError("capable SUMO Provider has no resolved native network")
        asset = next(item for item in run.scenario.assets
                     if item.asset_id == run.scenario.sumo.network_asset_id)
        network = ElementTree.parse(BundleReader(root).resolve_file(asset.file))
        edge_ids = {edge.attrib["id"] for edge in network.findall("edge")
                    if edge.attrib.get("function") != "internal"}
    for index, event in enumerate(events):
        if event.type != "traffic.restricted":
            continue
        field = f"/draft/events/{index}"
        if not capable:
            blockers.append(CompilationBlocker(
                code="event.provider_capability_unavailable", field=field + "/type",
                message="The declared sumo.traci Provider lacks sumo.traffic.restrictions.",
            ))
            continue
        tick = Decimal(str(event.atS)) * 1_000_000_000 / run.environment.clock.step_ns
        if tick != tick.to_integral_value() or not 1 <= tick <= run.environment.clock.max_steps:
            blockers.append(CompilationBlocker(
                code="event.scheduled_tick_invalid", field=field + "/atS",
                message="A restriction must select an exact positive Provider tick within the run horizon.",
            ))
        elif event.targetId not in edge_ids:
            blockers.append(CompilationBlocker(
                code="event.native_target_unavailable", field=field + "/targetId",
                message="The restriction target is not an external edge in the pinned SUMO network.",
            ))
        elif event.payload != {"disallowedClasses": ["passenger"]}:
            blockers.append(CompilationBlocker(
                code="event.payload_unsupported", field=field + "/payload",
                message='This capability implements exactly {"disallowedClasses":["passenger"]}.',
            ))
        elif event.targetId in {item.edge_id for item in lowered}:
            blockers.append(CompilationBlocker(
                code="event.target_repeated", field=field + "/targetId",
                message="Permanent restrictions must target distinct native edges.",
            ))
        else:
            lowered.append(SumoTrafficRestriction(
                event_id=event.id, at_tick=int(tick), edge_id=event.targetId,
                disallowed_classes=("passenger",),
            ))
    return tuple(sorted(lowered, key=lambda item: (item.at_tick, item.event_id))), tuple(blockers)
