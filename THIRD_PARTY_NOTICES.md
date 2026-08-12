# Third-party reference games

The following licensed upstream projects are included as runnable SRTP ingestion and fidelity fixtures.

## Snake Game with Python and Pygame

- Upstream: <https://github.com/anishvedant/Snake-game>
- Copyright: 2024 Anish Kumar Vedant
- License: MIT
- Included: complete source, graphics, font, sound and high-score data file
- License copy: `srtp/reference_games/pygame_snake/LICENSE`

The upstream files are preserved unchanged. This is now the default Snake
fidelity reference because it provides a complete visible game, documented
keyboard controls, scoring, pause, audio and terminal feedback.

## pygame-minesweeper

- Upstream: <https://pypi.org/project/pygame-minesweeper/>
- Copyright: Andreas Isnes Nilsen and contributors
- License: MIT
- Included published packages: UI 1.0.11, core 1.0.18 and sprites 1.0.41
- Dependency: appdirs 1.4.4, MIT
- License copies: `srtp/reference_games/pygame_minesweeper/LICENSE-*.txt`

The three published `minesweeper` namespace packages are assembled as Python
would install them. CubeEngine's `run_game.py` supplies the documented `basic`
CLI argument so one Workbench Play action opens a game immediately; it does not
replace the upstream game logic or assets.

## Free Python Games

- Upstream: <https://github.com/grantjenks/free-python-games>
- Copyright: 2017-2023 Grant Jenks
- License: Apache License 2.0
- Included: `freegames/__init__.py`, `utils.py`, `snake.py`, `minesweeper.py`, `connect.py`
- License copy: `srtp/reference_games/free_python_games/LICENSE`

These are the upstream educational programs without CubeEngine patches.
`connect.py` explicitly leaves row/win/full-board validation as exercises, so
the Workbench labels it as a source prototype rather than a complete ruleset.

## Complete Turtle Connect Four acceptance source

- Derived from: <https://github.com/grantjenks/free-python-games>
- Copyright: 2017-2023 Grant Jenks; CubeEngine completion changes
- License: Apache License 2.0
- Included: standalone Turtle source with explicit `CONNECT_N = 4`, terminal
  line detection, full-board draw and restart
- License copy: `srtp/reference_games/turtle_connect_complete/LICENSE`

The upstream educational Connect file explicitly leaves winner detection as a
TODO. It is retained above as an honest incomplete-source fixture. The
CubeEngine-maintained derivative is separately labelled and is the Workbench
acceptance source used to prove that SRTP reads and lifts an actual terminal
rule instead of silently inventing one.

## 2048-pygame

- Upstream: <https://github.com/rajitbanerjee/2048-pygame>
- Copyright: 2019 Rajit Banerjee
- License: MIT
- Included: complete runnable source project and its image assets
- License copy: `srtp/reference_games/pygame_2048/LICENSE`

The upstream key map contained Pygame 1 arrow key integers only. CubeEngine adds
the equivalent SDL2/Pygame 2 arrow codes and a pointer-swipe input path; the
movement, merge, spawn and outcome algorithms remain upstream code.

CubeEngine code does not claim authorship of these games.
