from __future__ import annotations

import sys


def main(arguments: list[str] | None = None) -> int:
    if arguments not in (None, []):
        print("usage: harness selfcheck", file=sys.stderr)
        return 2
    from aero_bench.runtime.service import HarnessRuntimeService

    if not callable(HarnessRuntimeService.from_environment):
        print("Harness runtime import failed", file=sys.stderr)
        return 1
    print("aero-bench harness runtime import/config surface is available")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
