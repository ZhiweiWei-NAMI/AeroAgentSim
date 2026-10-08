"""Deprecated convenience entry point for the kernel platform HTTP/viewer host.

Build the optional frontend with npm in frontend/ beforehand. This launcher
never installs dependencies or starts the deprecated SimPy workbench.
"""

import sys
import warnings

from aeroagentsim.services.cli import main

if __name__ == "__main__":
    warnings.warn(
        "Use 'aeroagentsim serve'; see docs/platform/MIGRATION-v1.md",
        DeprecationWarning,
        stacklevel=1,
    )
    sys.argv.insert(1, "serve")
    main()
