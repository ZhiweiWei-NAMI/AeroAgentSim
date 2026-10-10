"""Low-altitude route planners: coverage rows, asset survey and loop patrol."""

from __future__ import annotations

from aeroagentsim.algorithms.protocols import Point, Snapshot, Zone
from aeroagentsim.algorithms.routes import Coverage, PatrolRoute, SurveyRoute
from aeroagentsim.packs.geometry import segment_hits_box


def zone(identity: str, lower: Point, upper: Point, active: bool = True) -> Zone:
    return Zone(identity, "no_fly", lower, upper, active)


def snapshot(*zones: Zone) -> Snapshot:
    return Snapshot(0, (), (), tuple(zones))


class TestCoverage:
    def test_boustrophedon_rows_over_box_and_return_to_goal(self) -> None:
        planner = Coverage(lower=(0.0, 0.0), upper=(40.0, 20.0), spacing_m=10.0)
        route = planner.plan(snapshot(), (5.0, -10.0, 30.0), (5.0, -10.0, 30.0))
        assert route[-1] == (5.0, -10.0, 30.0)
        core = [p for p in route if p[1] >= 0]
        assert core == [
            (0.0, 0.0, 30.0),
            (40.0, 0.0, 30.0),
            (40.0, 10.0, 30.0),
            (0.0, 10.0, 30.0),
            (0.0, 20.0, 30.0),
            (40.0, 20.0, 30.0),
        ]

    def test_full_waypoints_avoid_active_no_fly_via_corridor_connector(self) -> None:
        planner = Coverage(lower=(0.0, 0.0), upper=(30.0, 15.0), spacing_m=5.0)
        # The exit leg to the goal is blocked; the connector must detour around it.
        state = snapshot(zone("cage", (10.0, 40.0, 0.0), (20.0, 60.0, 50.0)))
        route = planner.plan(state, (0.0, -5.0, 25.0), (15.0, 70.0, 25.0))
        assert route[-1] == (15.0, 70.0, 25.0)
        for a, b in zip(route, route[1:]):
            assert not any(
                z.active and segment_hits_box(a, b, z.lower, z.upper)
                for z in state.zones
            )


class TestSurveyRoute:
    def test_visits_configured_asset_waypoints_in_order(self) -> None:
        planner = SurveyRoute(
            waypoints=[(0.0, 0.0), (50.0, 0.0), (50.0, 30.0), (0.0, 30.0)]
        )
        state = snapshot()
        route = planner.plan(state, (-10.0, 0.0, 40.0), (-10.0, 40.0, 40.0))
        assert route[0] == (-10.0, 0.0, 40.0)
        assert route[-1] == (-10.0, 40.0, 40.0)
        ordered = [p for p in route if tuple(p[:2]) in map(tuple, planner.waypoints)]
        assert ordered == [
            (0.0, 0.0, 40.0),
            (50.0, 0.0, 40.0),
            (50.0, 30.0, 40.0),
            (0.0, 30.0, 40.0),
        ]

    def test_connector_routes_around_active_zone_between_waypoints(self) -> None:
        planner = SurveyRoute(waypoints=[(0.0, 0.0), (60.0, 0.0)])
        state = snapshot(zone("block", (20.0, -20.0, 0.0), (40.0, 20.0, 50.0)))
        route = planner.plan(state, (-5.0, 0.0, 30.0), (-5.0, 0.0, 30.0))
        assert route[-1] == (-5.0, 0.0, 30.0)
        for a, b in zip(route, route[1:]):
            assert not any(
                segment_hits_box(a, b, z.lower, z.upper) for z in state.zones
            )


class TestPatrolRoute:
    def test_completes_full_loop_and_returns_to_goal(self) -> None:
        planner = PatrolRoute(
            waypoints=[(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0)]
        )
        state = snapshot()
        route = planner.plan(state, (0.0, 0.0, 35.0), (0.0, 0.0, 35.0))
        corners = [p for p in route if tuple(p[:2]) in map(tuple, planner.waypoints)]
        assert corners == [
            (0.0, 0.0, 35.0),
            (40.0, 0.0, 35.0),
            (40.0, 40.0, 35.0),
            (0.0, 40.0, 35.0),
            (0.0, 0.0, 35.0),
        ]
        assert route[-1] == (0.0, 0.0, 35.0)

    def test_open_loop_skips_return_leg(self) -> None:
        planner = PatrolRoute(
            waypoints=[(0.0, 0.0), (40.0, 0.0), (40.0, 40.0)], close=False
        )
        route = planner.plan(snapshot(), (0.0, 0.0, 35.0), (0.0, 0.0, 35.0))
        assert route[-1] == (0.0, 0.0, 35.0)
        assert route.count((0.0, 0.0, 35.0)) == 2  # start/first corner and goal only
