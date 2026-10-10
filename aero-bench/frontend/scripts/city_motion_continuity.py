"""Certify the viewer's linear positions and shortest-angle body interpolation.

Certificates describe interpolation of the public samples. They make no claim
about unobserved native SUMO states between its TraCI integration steps.
"""
from __future__ import annotations

import math
from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.strtree import STRtree

from city_ground_fleet import BODY_DIMENSIONS

AREA_EPS_M2 = 1e-11
DISTANCE_EPS_M = 1e-11
MAX_DEPTH = 20


def body_corners(first: list, last: list, fraction: float) -> list[tuple[float, float]]:
    x = first[1] + (last[1]-first[1])*fraction
    z = first[2] + (last[2]-first[2])*fraction
    delta = (last[3]-first[3]+180) % 360-180
    angle = math.radians(first[3]+delta*fraction)
    c, s = math.cos(angle), math.sin(angle)
    width, length = BODY_DIMENSIONS[first[4]]
    return [(x+c*dx-s*dz, z+s*dx+c*dz) for dx, dz in
            [(-width/2,-length/2),(width/2,-length/2),(width/2,length/2),(-width/2,length/2)]]


def body_pose(first: list, last: list, fraction: float) -> Polygon:
    return Polygon(body_corners(first, last, fraction))


def interval_hull(first: list, last: list, low: float, high: float):
    """Contain every rotating corner by its chord and a derivative error bound."""
    hull = MultiPoint([*body_corners(first, last, low), *body_corners(first, last, high)]).convex_hull
    radius = math.hypot(*BODY_DIMENSIONS[first[4]])/2
    delta = math.radians((last[3]-first[3]+180) % 360-180)
    error = radius*delta**2*(high-low)**2/8
    return hull, error


def outer_hull(hull, error: float):
    # GEOS buffers are inscribed. Enlarge their radius to contain the error disk.
    return hull.buffer((error+DISTANCE_EPS_M)/math.cos(math.pi/64), quad_segs=16) if error else hull


def static_interval(first: list, last: list, obstacle) -> dict:
    maximum, witness = 0., None
    checked = 0
    pending = [(0., 1., 0)]
    while pending:
        low, high, depth = pending.pop()
        checked += 1
        hull, error = interval_hull(first, last, low, high)
        if obstacle.distance(hull) > error+DISTANCE_EPS_M:
            continue
        upper = obstacle.intersection(outer_hull(hull, error)).area
        if upper <= AREA_EPS_M2:
            continue
        for fraction in (low, (low+high)/2, high):
            area = obstacle.intersection(body_pose(first, last, fraction)).area
            if area > maximum:
                maximum, witness = area, fraction
            if area > AREA_EPS_M2:
                return {"status":"CONTACT", "fraction":fraction, "area_m2":area, "subintervals":checked}
        if depth >= MAX_DEPTH:
            return {"status":"UNRESOLVED", "fraction":witness, "witness_area_m2":maximum,
                    "area_upper_m2":upper, "subintervals":checked}
        mid = (low+high)/2
        pending.extend([(mid, high, depth+1), (low, mid, depth+1)])
    return {"status":"CLEAR", "subintervals":checked}


def pair_interval(first: list, first_after: list, second: list, second_after: list) -> dict:
    checked = 0
    pending = [(0.,1.,0)]
    while pending:
        low, high, depth = pending.pop()
        checked += 1
        a, ea = interval_hull(first, first_after, low, high)
        b, eb = interval_hull(second, second_after, low, high)
        if a.distance(b) > ea+eb+DISTANCE_EPS_M:
            continue
        upper = outer_hull(a, ea).intersection(outer_hull(b, eb)).area
        if upper <= AREA_EPS_M2:
            continue
        for fraction in (low,(low+high)/2,high):
            area = body_pose(first,first_after,fraction).intersection(body_pose(second,second_after,fraction)).area
            if area > AREA_EPS_M2:
                return {"status":"CONTACT", "fraction":fraction, "area_m2":area, "subintervals":checked}
        if depth >= MAX_DEPTH:
            return {"status":"UNRESOLVED", "area_upper_m2":upper, "subintervals":checked}
        mid=(low+high)/2
        pending.extend([(mid,high,depth+1),(low,mid,depth+1)])
    return {"status":"CLEAR", "subintervals":checked}


def continuity_contacts(traffic: dict, obstacles: list, *, include_vehicles: bool = True) -> dict:
    """Return strict contact witnesses and unresolved intervals; never repair poses."""
    tree = STRtree(obstacles)
    result = {"vehicle_intervals":0,"person_intervals":0,"static_candidate_pairs":0,"body_candidate_pairs":0,
              "vehicle_static_contacts":[],"vehicle_pair_contacts":[],"person_static_contacts":[],"unresolved":[]}
    frames = traffic["frames"]
    for index, frame in enumerate(frames):
        after_frame = frames[min(index+1,len(frames)-1)]
        after = {row[0]:row for row in after_frame["vehicles"]}
        records=[]
        for row in frame["vehicles"] if include_vehicles else []:
            last = after.get(row[0],row)  # Viewer holds a pose until its actor disappears.
            result["vehicle_intervals"] += index < len(frames)-1
            hull,error=interval_hull(row,last,0.,1.)
            outer=outer_hull(hull,error)
            records.append((row,last,outer))
            for obstacle_index in tree.query(outer):
                result["static_candidate_pairs"] += 1
                proof=static_interval(row,last,obstacles[obstacle_index])
                if proof["status"] != "CLEAR":
                    record={"actor_id":row[0],"start_second":frame["second"],"obstacle_index":int(obstacle_index),**proof}
                    result["vehicle_static_contacts" if proof["status"]=="CONTACT" else "unresolved"].append(record)
        envelopes=[record[2] for record in records]
        pairs=STRtree(envelopes)
        for first,(row,last,outer) in enumerate(records):
            for second in pairs.query(outer):
                if second <= first:
                    continue
                result["body_candidate_pairs"] += 1
                other,other_after,_=records[second]
                proof=pair_interval(row,last,other,other_after)
                if proof["status"] != "CLEAR":
                    record={"actor_ids":[row[0],other[0]],"start_second":frame["second"],**proof}
                    result["vehicle_pair_contacts" if proof["status"]=="CONTACT" else "unresolved"].append(record)
        after_people={row[0]:row for row in after_frame["persons"]}
        for row in frame["persons"]:
            last=after_people.get(row[0],row)
            result["person_intervals"] += index < len(frames)-1
            a,b=(row[1],row[2]),(last[1],last[2])
            line=LineString([a,b]) if a!=b else Point(a)
            for obstacle_index in tree.query(line.envelope.buffer(.301)):
                distance=obstacles[obstacle_index].distance(line)
                # Exact disk/line Minkowski distance; positive penetration is contact.
                if distance < .3-DISTANCE_EPS_M:
                    result["person_static_contacts"].append({"actor_id":row[0],"start_second":frame["second"],
                        "obstacle_index":int(obstacle_index),"minimum_circle_clearance_m":distance-.3})
    return result
