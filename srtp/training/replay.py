"""Saved-run replay verification, usable without the optional torch dependency."""
from pathlib import Path

from .adapter import compile_training_game
from .artifacts import atomic_json, read_json


def verify_replay(game, record):
    runtime = game._new_runtime()
    for action in record["actions"]:
        if runtime.evaluate_outcome().terminal or not runtime.is_legal(action):
            raise ValueError("Replay contains a move after the end or an illegal action.")
        runtime.apply_action(action)
    if game._encode_runtime(runtime).tolist() != record["final_board"]:
        raise ValueError("Replay board differs from the recorded Rule result.")
    outcome = runtime.evaluate_outcome()
    value = 1 if game.positive_player in outcome.winners else -1 if game.negative_player in outcome.winners else 0
    if not outcome.terminal or value != record["result"]:
        raise ValueError("Replay outcome differs from the recorded result.")
    return True


def verify_saved_run(root):
    root = Path(root)
    rule, adapter, run = (read_json(root / name) for name in ("rule.json", "adapter.json", "run.json"))
    game = compile_training_game(rule, adapter)
    identity = run["binding"]
    if identity["rule_hash"] != rule["content_hash"] or identity["adapter_hash"] != adapter["content_hash"]:
        raise ValueError("Run identity does not match the archived Rule/adapter.")
    games = sorted((root / "games").glob("game-*.json"))
    if not games:
        raise ValueError("This run has no completed games to replay.")
    for path in games:
        record = read_json(path)
        if record.get("binding") != identity:
            raise ValueError("Replay belongs to another game or adapter: " + path.name)
        verify_replay(game, record)
    result = {"binding": identity, "games": len(games), "passed": True}
    atomic_json(root / "replay-verification.json", result)
    return result
