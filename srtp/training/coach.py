"""Observable adaptation of AI-Dev's Coach, NNetWrapper and balanced Arena.

The original MCTS and 3D network are retained in vendor/. RuleRuntime is the
only game implementation. Checkpoints are iteration boundaries: incomplete
candidate updates never replace the last resumable model.
"""
from copy import deepcopy
from types import SimpleNamespace
import math

import numpy as np
import torch

from .artifacts import atomic_json, load_checkpoint, save_checkpoint
from .vendor.mcts import MCTS
from .vendor.network import TicTacToe3DNet


class Cancelled(Exception):
    pass


class Network:
    def __init__(self, game, config):
        args = SimpleNamespace(num_channels=config.channels, dropout=0.3)
        self.model = TicTacToe3DNet(game, args).to(config.device)
        self.device = config.device

    def predict(self, board):
        self.model.eval()
        with torch.no_grad():
            tensor = torch.tensor(np.asarray(board), dtype=torch.float32, device=self.device).unsqueeze(0)
            pi, value = self.model(tensor)
        return torch.exp(pi).cpu().numpy()[0], float(value.item())


class Search(MCTS):
    def __init__(self, game, network, config, check):
        super().__init__(game, network, SimpleNamespace(numMCTSSims=config.simulations, cpuct=1.2))
        self.check = check

    def search(self, board):
        self.check()
        return super().search(board)

    def getActionProb(self, board, temp=1):
        self.check()
        probabilities = np.asarray(super().getActionProb(board, temp), dtype=np.float64)
        valid = self.game.getValidMoves(board, 1)
        probabilities *= valid
        total = probabilities.sum()
        if not np.isfinite(probabilities).all() or total <= 0:
            raise RuntimeError("MCTS produced an invalid policy; training stopped.")
        return probabilities / total


def random_state():
    state = np.random.get_state()
    return [state[0], state[1].tolist(), int(state[2]), int(state[3]), float(state[4])]


from .replay import verify_replay

class Coach:
    def __init__(self, game, config, root, identity, emit, check):
        self.game, self.config, self.root, self.identity = game, config, root, identity
        self.emit, self.check = emit, check
        self.network = Network(game, config)
        self.history = []
        self.counters = {"self_play_games": 0, "arena_games": 0, "samples": 0, "optimizer_steps": 0}
        self.iteration = 0
        self.checkpoint = None
        self.results = []
        self.trace_count = 0

    def save(self, iteration=None):
        committed_iteration = self.iteration if iteration is None else iteration
        self.checkpoint = save_checkpoint(self.root, self.network.model, self.identity, committed_iteration,
                                          self.history, self.counters, random_state())
        self.iteration = committed_iteration
        self.emit("checkpoint", checkpoint=self.checkpoint, completed_iteration=self.iteration, counters=self.counters)

    def restore(self, path):
        data, self.history = load_checkpoint(path, self.identity, self.network.model)
        self.iteration = int(data["iteration"])
        self.counters = dict(data["counters"])
        state = data["rng"]
        np.random.set_state((state[0], np.array(state[1], dtype=np.uint32), state[2], state[3], state[4]))
        torch.set_rng_state(torch.tensor(data["torch_rng"], dtype=torch.uint8))
        if self.config.device == "cuda" and data.get("cuda_rng"):
            torch.cuda.set_rng_state_all([torch.tensor(value, dtype=torch.uint8) for value in data["cuda_rng"]])
        self.emit("resumed", completed_iteration=self.iteration, source_checkpoint=str(path), counters=self.counters)

    def record(self, phase, iteration, actions, board, result, **extra):
        self.trace_count += 1
        record = {"binding": self.identity, "phase": phase, "iteration": iteration,
                  "actions": actions, "final_board": board.tolist(), "result": result, **extra}
        verify_replay(self.game, record)
        record["replay_verified"] = True
        atomic_json(self.root / "games" / ("game-{0:05d}.json".format(self.trace_count)), record)

    def episode(self, iteration, episode):
        game = self.game
        search = Search(game, self.network, self.config, self.check)
        board, player, actions, examples = game.getInitBoard(), 1, [], []
        for ply in range(27):
            self.check()
            canonical = game.getCanonicalForm(board, player)
            pi = search.getActionProb(canonical, temp=int(ply + 1 < 10))
            for symmetric, policy in game.getSymmetries(canonical, pi):
                examples.append([symmetric.tolist(), policy.tolist(), player])
            action = int(np.random.choice(len(pi), p=pi))
            actions.append(action)
            board, player = game.getNextState(board, player, action)
            self.emit("self_play_move", iteration=iteration, episode=episode, ply=ply + 1)
            ended = game.getGameEnded(board, player)
            if ended:
                result = int(ended * player) if abs(ended) == 1 else 0
                self.record("self_play", iteration, actions, board, result)
                return [[b, p, result * side] for b, p, side in examples]
        raise RuntimeError("Game exceeded the verified 27-ply placement bound.")

    def train(self, examples, iteration):
        model = self.network.model
        weighted = [e for e in examples if e[2] >= 0] + [e for e in examples if e[2] < 0] * 3
        np.random.shuffle(weighted)
        if len(weighted) < 2:
            raise RuntimeError("Insufficient samples for AI-Dev BatchNorm training.")
        optimizer = torch.optim.Adam(model.parameters(), lr=self.config.learning_rate)
        for epoch in range(self.config.epochs):
            model.train()
            losses = []
            for offset in range(0, len(weighted), self.config.batch_size):
                self.check()
                batch = weighted[offset:offset + self.config.batch_size]
                if len(batch) == 1:
                    batch = batch + weighted[:1]
                boards, policies, values = zip(*batch)
                boards = torch.tensor(boards, dtype=torch.float32, device=self.config.device)
                policies = torch.tensor(policies, dtype=torch.float32, device=self.config.device)
                values = torch.tensor(values, dtype=torch.float32, device=self.config.device)
                pi, value = model(boards)
                policy_loss = -(policies * pi).sum() / len(batch)
                value_loss = ((values - value.view(-1)) ** 2).mean()
                loss = policy_loss + value_loss
                if not torch.isfinite(loss):
                    raise RuntimeError("Training loss is not finite; the last complete checkpoint is retained.")
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                self.counters["optimizer_steps"] += 1
                losses.append(float(loss.item()))
                self.emit("training_batch", iteration=iteration, epoch=epoch + 1,
                          batch=offset // self.config.batch_size + 1, batches=math.ceil(len(weighted) / self.config.batch_size),
                          loss=losses[-1], counters=self.counters)
            self.emit("training_epoch", iteration=iteration, epoch=epoch + 1, loss=float(np.mean(losses)))

    def arena(self, previous, iteration):
        wins = {"previous": 0, "candidate": 0, "draws": 0}
        for number in range(self.config.arena_games):
            self.check()
            candidate_side = 1 if number >= self.config.arena_games // 2 else -1
            networks = {candidate_side: self.network, -candidate_side: previous}
            searches = {side: Search(self.game, network, self.config, self.check) for side, network in networks.items()}
            board, player, actions = self.game.getInitBoard(), 1, []
            for ply in range(27):
                self.check()
                canonical = self.game.getCanonicalForm(board, player)
                action = int(np.argmax(searches[player].getActionProb(canonical, temp=0)))
                actions.append(action)
                board, player = self.game.getNextState(board, player, action)
                self.emit("arena_move", iteration=iteration, game=number + 1, ply=ply + 1)
                ended = self.game.getGameEnded(board, player)
                if ended:
                    result = int(ended * player) if abs(ended) == 1 else 0
                    wins["draws" if result == 0 else "candidate" if result == candidate_side else "previous"] += 1
                    self.record("arena", iteration, actions, board, result, candidate_side=candidate_side)
                    self.counters["arena_games"] += 1
                    self.emit("arena_game", iteration=iteration, game=number + 1, arena=wins, counters=self.counters)
                    break
            else:
                raise RuntimeError("Arena exceeded the verified placement bound.")
        return wins

    def learn(self, resume=None):
        if resume:
            self.restore(resume)
        self.save()
        base = self.iteration
        for iteration in range(base + 1, base + self.config.iterations + 1):
            self.check()
            self.emit("iteration", iteration=iteration, run_iteration=iteration - base, iterations=self.config.iterations)
            samples = []
            for episode in range(1, self.config.episodes + 1):
                samples.extend(self.episode(iteration, episode))
                self.counters["self_play_games"] += 1
                self.emit("self_play_game", iteration=iteration, episode=episode, counters=self.counters)
            self.history.append(samples)
            self.history = self.history[-15:]
            # Retain the engineer's history window with a bounded total buffer.
            while sum(len(batch) for batch in self.history) > 200000:
                self.history.pop(0)
            examples = [e for batch in self.history for e in batch]
            self.counters["samples"] = len(examples)
            atomic_json(self.root / "iteration-samples.json", {"binding": self.identity, "iteration": iteration, "samples": samples})
            previous = Network(self.game, self.config)
            previous.model.load_state_dict(deepcopy(self.network.model.state_dict()))
            self.train(examples, iteration)
            wins = self.arena(previous, iteration)
            decisive = wins["candidate"] + wins["previous"]
            accepted = decisive > 0 and wins["candidate"] / decisive >= self.config.acceptance
            if not accepted:
                self.network.model.load_state_dict(previous.model.state_dict())
            self.check()
            self.save(iteration)
            self.results.append({"iteration": iteration, "arena": wins, "accepted": accepted,
                                 "acceptance_threshold": self.config.acceptance})
            self.emit("iteration_complete", iteration=iteration, arena=wins, accepted=accepted, counters=self.counters)
        return self.results
