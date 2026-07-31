"""Entry point for `python -m cliradar`.

The console script is the normal way in, but a checkout that was never
installed still has to be runnable, and that is the form the stand runbooks use.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    # main() raises SystemExit itself, so the exit code survives.
    main()
