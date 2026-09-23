"""Distinguish a board click from camera dragging before dispatching input."""
from copy import deepcopy


class PointerGesture:
    def __init__(self, threshold=0.012):
        self.threshold = threshold
        self.pending = {}

    def press(self, button, position, context):
        self.pending[button] = (tuple(position), deepcopy(context), False)

    def move(self, position):
        for button, (start, context, dragged) in list(self.pending.items()):
            dragged = dragged or sum((a-b)**2 for a,b in zip(start, position)) > self.threshold**2
            self.pending[button] = (start, context, dragged)

    def release(self, button, position, context):
        self.move(position)
        item = self.pending.pop(button, None)
        if item is not None and item[1] and item[1] == context and not item[2]:
            return item[1]
        return None

    def clear(self):
        self.pending.clear()
