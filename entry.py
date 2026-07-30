"""Entry point for the frozen build.

Separate from ``crec/__main__.py`` on purpose: PyInstaller runs its entry script
as top-level ``__main__`` with no parent package, so the relative import in
``__main__.py`` fails there. This uses an absolute import instead.
``python -m crec`` still goes through ``crec/__main__.py``.
"""

import sys

from crec.cli import main

if __name__ == "__main__":
    sys.exit(main())
