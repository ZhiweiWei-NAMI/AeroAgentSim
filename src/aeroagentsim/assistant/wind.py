"""Wind wording uses vector travel (toward), never meteorological origin (from).

set_wind.from_deg is the one authored exception: it states the origin the wind
comes FROM (clockwise from north) and the compiler converts it to the vector.
"""

from __future__ import annotations

import math


def from_deg(east: float, north: float) -> float | None:
    """Meteorological origin direction (clockwise from north) of a travel vector; None when calm."""
    speed2 = east * east + north * north
    return None if speed2 == 0 else math.degrees(math.atan2(-east, -north)) % 360


def vector_text(vector: dict, *, zh: bool = False) -> str:
    east, north = vector["east_mps"], vector["north_mps"]
    components = [("east", "东", east), ("north", "北", north)]
    if "up_mps" in vector:
        components.append(("up", "上", vector["up_mps"]))
    raw = ", ".join(f"{cn if zh else en} {value:g}" for en, cn, value in components)
    if east == north == 0:
        direction = "无水平流向" if zh else "no horizontal flow"
    else:
        angle = math.degrees(math.atan2(north, east)) % 360
        en = ("north" if north > 0 else "south" if north < 0 else "") + ("east" if east > 0 else "west" if east < 0 else "")
        cn = ("东" if east > 0 else "西" if east < 0 else "") + ("北" if north > 0 else "南" if north < 0 else "")
        direction = (f"吹向{cn}（矢量流向，自东向逆时针 {angle:g} 度）" if zh else
                     f"toward {en} (vector travel direction, {angle:g}° counterclockwise from east)")
    return f"{direction}; {raw} {'米/秒' if zh else 'm/s'}"


def wind_lines(wind: dict, *, gust: bool, zh: bool = False) -> list[str]:
    lines = []
    for profile in wind["profiles"].values():
        if gust:
            for event in profile["gusts"]:
                label = f"阵风 {event['start_s']:g}–{event['end_s']:g} 秒" if zh else f"Wind gust {event['start_s']:g}–{event['end_s']:g} s"
                lines.append(label + ": " + "; ".join(vector_text(v, zh=zh) for v in event["vectors"]))
        elif "vectors" in profile:
            lines.append(("背景风：" if zh else "Background wind: ") + "; ".join(vector_text(v, zh=zh) for v in profile["vectors"]))
    return list(dict.fromkeys(lines))


def ground_wind_prose(text: str, wind: dict, *, zh: bool = False) -> str:
    from .catalogue import callbacks

    for callback in callbacks("ground_wind"):
        text = callback(text, wind, zh=zh)
    return text
