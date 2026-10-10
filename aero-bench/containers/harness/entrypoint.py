from __future__ import annotations

import sys


def main() -> int:
    arguments = sys.argv[1:]
    if arguments == ["selfcheck"]:
        from selfcheck import main as selfcheck_main

        return selfcheck_main([])
    from aero_bench.runtime.service import main as service_main

    return service_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
