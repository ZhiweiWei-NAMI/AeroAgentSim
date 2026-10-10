from __future__ import annotations

import xml.etree.ElementTree as ET

from tools.prepare_urban_engineering_traffic import _drivable_edges


def test_drivable_edges_exclude_lane_outside_conversion_boundary() -> None:
    network = ET.fromstring(
        """
        <net>
          <location convBoundary="-500,-500,500,500" />
          <edge id="inside">
            <lane id="inside_0" shape="450,-499 460,-490" />
          </edge>
          <edge id="outside">
            <lane id="outside_0" shape="450,-499 460,-500.27" />
          </edge>
        </net>
        """
    )

    assert set(_drivable_edges(network)) == {"inside"}
