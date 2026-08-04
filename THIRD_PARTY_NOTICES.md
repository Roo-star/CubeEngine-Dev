# Third-party reference games

The following unmodified upstream source files are included solely as runnable SRTP ingestion and fidelity fixtures.

## Free Python Games

- Upstream: <https://github.com/grantjenks/free-python-games>
- Copyright: 2017-2023 Grant Jenks
- License: Apache License 2.0
- Included: `freegames/__init__.py`, `utils.py`, `snake.py`, `minesweeper.py`, `connect.py`
- License copy: `srtp/reference_games/free_python_games/LICENSE`

These are the upstream educational programs without CubeEngine patches.
`connect.py` explicitly leaves row/win/full-board validation as exercises, so
the Workbench labels it as a source prototype rather than a complete ruleset.

## 2048-pygame

- Upstream: <https://github.com/rajitbanerjee/2048-pygame>
- Copyright: 2019 Rajit Banerjee
- License: MIT
- Included: complete runnable source project and its image assets
- License copy: `srtp/reference_games/pygame_2048/LICENSE`

CubeEngine code does not claim authorship of these games. Runtime compatibility code lives outside the upstream game folders so the reference sources remain unchanged.
