"""Pygame Minesweeper.

The upstream command-line entry point requires a positional difficulty.  This
tiny launcher supplies its documented default so the Workbench's single Play
action opens a game immediately.  The upstream game modules are unchanged.
"""

from __future__ import annotations

import sys

from minesweeper.__main__ import main


if __name__ == "__main__":
    sys.argv = [sys.argv[0], "basic"]
    main()
