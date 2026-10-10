from __future__ import annotations

import pytest

from aero_bench.tasks.urban_recovery_demo.planner import (
    RecoveryPlanner,
    RecoveryPlanningError,
)


def test_recovery_planner_detours_around_inflated_no_fly_polygon() -> None:
    planner = RecoveryPlanner(
        vehicle_radius_m=2.0,
        obstacle_margin_m=3.0,
        cell_size_m=5.0,
        bounds=(-50.0, 50.0, -50.0, 50.0),
    )
    path = planner.plan(
        start_enu_m=(-40.0, 0.0),
        goal_enu_m=(40.0, 0.0),
        forbidden_polygons=(
            ((-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0)),
        ),
        altitude_m=30.0,
    )

    assert path[0].x == -40.0 and path[0].y == 0.0
    assert path[-1].x == 40.0 and path[-1].y == 0.0
    assert len(path) > 2
    assert all(point.z == 30.0 for point in path)
    assert all(
        not (-15.0 < point.x < 15.0 and abs(point.y) < 15.0)
        for point in path[1:-1]
    )


def test_recovery_planner_rejects_blocked_start() -> None:
    planner = RecoveryPlanner(
        vehicle_radius_m=1.0,
        obstacle_margin_m=1.0,
        bounds=(-20.0, 20.0, -20.0, 20.0),
    )
    with pytest.raises(RecoveryPlanningError, match="start"):
        planner.plan(
            start_enu_m=(0.0, 0.0),
            goal_enu_m=(10.0, 10.0),
            forbidden_polygons=(
                ((-5.0, -5.0), (5.0, -5.0), (5.0, 5.0), (-5.0, 5.0)),
            ),
            altitude_m=20.0,
        )


def test_recovery_planner_rejects_invalid_constraints() -> None:
    planner = RecoveryPlanner(vehicle_radius_m=1.0, obstacle_margin_m=1.0)
    with pytest.raises(RecoveryPlanningError, match="fewer"):
        planner.plan(
            start_enu_m=(0.0, 0.0),
            goal_enu_m=(10.0, 0.0),
            forbidden_polygons=(((0.0, 0.0), (1.0, 0.0)),),
            altitude_m=20.0,
        )
