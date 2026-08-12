"""Compatibility boundary for launching an unchanged Python source game.

This is intentionally not imported by static analysis.  It is used only after
the designer explicitly asks to run a trusted original project.
"""

from __future__ import annotations

import runpy
import sys
import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--script")
    source.add_argument("--module")
    args = parser.parse_args()
    _install_pygame_windows_font_fallback()
    _install_numpy_legacy_aliases()
    if args.module:
        sys.argv = [args.module]
        runpy.run_module(args.module, run_name="__main__", alter_sys=True)
        return
    entrypoint = Path(args.script).resolve()
    sys.path.insert(0, str(entrypoint.parent))
    sys.argv = [str(entrypoint)]
    runpy.run_path(str(entrypoint), run_name="__main__")


def _install_numpy_legacy_aliases() -> None:
    """Keep older complete games runnable without rewriting their source."""

    try:
        import numpy
    except ModuleNotFoundError:
        return
    aliases = {"int": int, "float": float, "bool": bool, "object": object}
    for name, value in aliases.items():
        if name not in numpy.__dict__:
            setattr(numpy, name, value)


def _install_pygame_windows_font_fallback() -> None:
    """Work around invalid Windows font-registry values in Pygame 2.6.x.

    The requested source font remains first choice.  Pygame's bundled default
    is used only when its Windows registry scan raises TypeError.
    """

    try:
        import pygame
    except ModuleNotFoundError:
        return
    original = pygame.font.SysFont

    def safe_sys_font(name, size, bold=False, italic=False, constructor=None):
        try:
            return original(name, size, bold=bold, italic=italic, constructor=constructor)
        except TypeError:
            font = pygame.font.Font(None, size)
            font.set_bold(bool(bold))
            font.set_italic(bool(italic))
            return font

    pygame.font.SysFont = safe_sys_font


if __name__ == "__main__":
    main()
