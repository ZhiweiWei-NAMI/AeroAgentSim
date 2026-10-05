"""Test aliases for pure modules copied to standalone workload images."""

from __future__ import annotations

import sys

from aero_bench.world import frame_math
from aero_bench.world import workload_scenario


sys.modules["aero_frame_math"] = frame_math
sys.modules["workload_scenario"] = workload_scenario
