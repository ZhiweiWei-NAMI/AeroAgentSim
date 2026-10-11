"""Restaurant kitchens and meal orders using the shared low-altitude runtime."""

from __future__ import annotations

from typing import Any

from .everyday import _operators
from .tasks import FOOD_FIELDS
from .template import SECOND, parcel_document


def food_document() -> dict[str, Any]:
    """A reusable district: authored kitchens, seeded orders, real flight receipts.

    Sites use the shipped Huangpu placement coordinates. Restaurants are modeled
    facilities at those sites, not claims about real businesses at the addresses.
    """
    doc = parcel_document(fleet_size=2)
    doc.update(id="food-delivery", city="huangpu")
    doc["run"]["until_ns"] = 360 * SECOND
    doc["entities"] = []

    def site(
        identity: str,
        kind: str,
        point: list[float],
        label: str,
        label_zh: str,
        capacity: int = 1,
        **facts: Any,
    ) -> None:
        doc["entities"].append(
            {
                "id": identity,
                "type": "lowalt:" + kind,
                "label": label,
                "label_zh": label_zh,
                "business": "logistics",
                "facts": {
                    "lowalt.site_position": point,
                    "lowalt.site_open": True,
                    "lowalt.site_capacity": capacity,
                    **facts,
                },
            }
        )

    west, east = [-13.05, -100.037, 0.0], [328.337, -136.882, 12.0]
    site(
        "restaurant-west",
        "Restaurant",
        west,
        "West Plaza kitchen",
        "西广场餐厅厨房",
        1,
        **{"lowalt.food_preparation_s": 25.0},
    )
    site(
        "restaurant-east",
        "Restaurant",
        east,
        "East Roof kitchen",
        "东侧屋顶餐厅厨房",
        1,
        **{"lowalt.food_preparation_s": 35.0},
    )
    for identity, point, label, label_zh in (
        ("pad-west", west, "West kitchen charging pad", "西侧厨房充电位"),
        ("pad-east", east, "East kitchen charging pad", "东侧厨房充电位"),
    ):
        site(
            identity,
            "ChargingPad",
            point,
            label,
            label_zh,
            **{"lowalt.pad_power_w": 5000.0},
        )
    for index, point in enumerate(
        ([-86.612, 76.526, 12.0], [247.588, 73.959, 75.0], [93.756, 104.218, 12.0]), 1
    ):
        site(
            f"customer-{index}",
            "Customer",
            point,
            f"Meal customer {index}",
            f"餐食收货点 {index}",
        )
    doc["entities"].append({"id": "weather", "type": "lowalt:Environment", "facts": {}})
    orders = [f"meal-{index}" for index in range(1, 7)]
    for index, identity in enumerate(orders, 1):
        doc["entities"].append(
            {
                "id": identity,
                "type": "lowalt:FoodOrder",
                "label": f"Meal order {index}",
                "label_zh": f"外卖订单 {index}",
                "business": "logistics",
                "facts": {
                    "lowalt.task_state": "unreleased",
                    "lowalt.task_kind": "food",
                    "lowalt.task_priority": 1,
                    "lowalt.task_origin": "restaurant-west",
                    "lowalt.task_destination": "customer-1",
                    "lowalt.task_created_ns": 0,
                    "lowalt.task_vehicle": "",
                    "lowalt.task_payload_kg": 0.8,
                },
            }
        )
    fleet = doc["generators"]["fleet"]
    fleet.update(
        starts=[west, east],
        spacing_m=35.0,
        label="Meal courier {index}",
        label_zh="送餐无人机 {index}",
        business="logistics",
        vehicle_class="parcel",
    )
    tasks = doc["engines"]["tasks"]["config"]
    tasks.update(
        cruise_altitudes={"parcel": 125.0},
        vehicle_kinds={"parcel": ["food"]},
        generators=[
            {
                "kind": "food",
                "tasks": orders,
                "origins": ["restaurant-west", "restaurant-east"],
                "destinations": [f"customer-{i}" for i in range(1, 4)],
                "priority": 1,
                "payload_kg": 0.8,
                "delivery_window_s": 240.0,
                "arrivals": {
                    "mode": "poisson",
                    "rate_per_s": 0.075,
                    "start_ns": 0,
                    "end_ns": 100 * SECOND,
                    "burst_size": 2,
                },
            }
        ],
    )
    doc["algorithms"]["conflict_resolution"] = "hold_and_go"
    doc["bindings"]["rules"].extend(
        [
            {
                "writer": "tasks",
                "type": "lowalt:Restaurant",
                "fields": ["lowalt.food_preparation_s"],
            },
            {
                "writer": "tasks",
                "type": "lowalt:FoodOrder",
                "fields": [f for f in FOOD_FIELDS if f != "lowalt.food_preparation_s"],
            },
        ]
    )
    _operators(doc, {"restaurant_delay": "lowalt.operator.restaurant_delay"}, [])
    return doc
