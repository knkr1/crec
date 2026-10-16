"""Entry point for the frozen build.

Separate from ``crec/__main__.py`` on purpose: PyInstaller runs its entry script
as top-level ``__main__`` with no parent package, so the relative import in
``__main__.py`` fails there. This uses an absolute import instead.
``python -m crec`` still goes through ``crec/__main__.py``.
"""

import os
import sys

# crecw.exe has no console, so sys.stdout/stderr are None and the first status
# line would raise. Point them at nothing; the tray reports through its icon.
if sys.stdout is None or sys.stderr is None:
    sink = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - lives as long as the process
    sys.stdout = sys.stdout or sink
    sys.stderr = sys.stderr or sink

from crec.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
