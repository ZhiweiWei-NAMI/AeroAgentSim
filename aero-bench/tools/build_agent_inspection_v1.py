from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import build_urban_infrastructure_inspection_v1 as urban_builder  # noqa: E402


def parser():
    result = urban_builder.parser()
    result.set_defaults(
        output_root=str(urban_builder.AGENT_ACCEPTANCE_OUTPUT),
        agent_acceptance=True,
    )
    return result


def main() -> None:
    urban_builder.build(parser().parse_args())


if __name__ == "__main__":
    main()
