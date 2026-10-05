#!/usr/bin/env python3
"""Generate effective fixture geometry before real canonical SUMO recording."""
import argparse,json
from pathlib import Path
import xml.etree.ElementTree as ET
from city_effective_fixtures import build_effective_fixtures
from city_native_signals import canonical_signal_inventory
from city_preview_coordinates import CityEnuProjection,load_canonical_city_geometry

ROOT=Path(__file__).resolve().parents[2]
MODEL_URLS={'signal':'/models/incoming/furniture/glb/traffic_light_4.glb',
    'street_lamp':'/models/incoming/furniture/glb/street_light_8.glb'}


def build(network,source_osm,objects,render,pack,road,output):
    geometry=load_canonical_city_geometry(objects,render,pack)
    context=geometry.source_context(network,source_osm)
    native=ET.parse(network).getroot(); projection=CityEnuProjection(native,geometry.origin)
    models={kind:{'url':url,'path':ROOT/'frontend/public'/url.lstrip('/')} for kind,url in MODEL_URLS.items()}
    document=build_effective_fixtures(context,json.loads(Path(road).read_text()),canonical_signal_inventory(native,projection),geometry.footprints,models)
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    output.write_text(json.dumps(document,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+'\n')
    return document['counts']


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('network','source-osm','objects','render','pack','road','output'):p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args();print(json.dumps(build(args.network,args.source_osm,args.objects,args.render,args.pack,args.road,args.output)))
