"""Host-owned session seeds; never rewrite a game's random declarations.

Compilation/replays use seed 0 unless supplied explicitly. Interactive sessions
choose a fresh seed and retain the per-stream sources for exact replay.
External and recorded streams still require their declared inputs.
"""
import hashlib


def session_sources(document, seed=0):
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError('Session seed must be an unsigned 64-bit integer')
    return {stream['id']:int.from_bytes(hashlib.sha256(
        (str(seed)+':'+stream['id']).encode()).digest()[:8], 'big')
        for stream in document.get('random_streams', []) if stream.get('seed_policy')=='session'}
