"""Read-only presentation clock; never advances or mutates game rules."""
import math


def frame_index(elapsed, count, fps, loop=True):
    if count < 1 or not math.isfinite(fps) or fps <= 0:
        raise ValueError('Animation requires frames and a positive finite frame rate')
    index = int(max(0, elapsed) * fps)
    return index % count if loop else min(index, count - 1)
