# AI-Dev integration provenance

Source: the same CubeEngine Git repository, `Dev` commit `2f42b6b`.

- `mcts.py` is `MCTS.py` with whitespace cleanup only.
- `network.py` contains the unchanged `TicTacToe3DNet` architecture from `tictactoe3d_nnet.py`, with standalone imports.
- `../coach.py` adapts the lifecycle of `Coach.learn`, `Coach.executeEpisode`, `NNetWrapper.train` and `Arena.playGames`: self-play → weighted replay buffer → candidate training → balanced-seat Arena → accept/reject.

The Workbench integration adds cancellation at search/batch boundaries, structured progress, finite rollout bounds, legal-action masking at the MCTS boundary, explicit device selection, pinned artifact manifests and atomic iteration checkpoints. It preserves the engineer's loss-example weighting (3), default acceptance threshold (0.55), and network layer dimensions. Legacy unpinned pickle checkpoints are deliberately not imported. RuleRuntime supplies the game; `game_for_training.py` and the handwritten rules are not used.
