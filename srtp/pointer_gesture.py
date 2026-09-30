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
        event=self.release_event(button,position,context)
        return event['context'] if event and event['gesture']=='click' else None

    def release_event(self, button, position, context, *, viewport_height=1):
        self.move(position)
        item = self.pending.pop(button, None)
        if item is None or not item[1]:
            return None
        start, pressed_context, dragged = item
        if dragged and button=='mouse.button.primary':
            dx=(position[0]-start[0])*viewport_height
            dy=(start[1]-position[1])*viewport_height  # source screen Y grows downward
            direction=('right' if dx>0 else 'left') if abs(dx)>=abs(dy) else ('down' if dy>0 else 'up')
            return {'gesture':'swipe','context':pressed_context,'direction':direction,
                    'distance_px':max(abs(dx),abs(dy))}
        if pressed_context==context and not dragged:
            return {'gesture':'background_click' if pressed_context.get('background') else 'click',
                    'context':pressed_context}
        return None

    def clear(self):
        self.pending.clear()

    def presentation_state(self, hovered):
        """A cancelled/dragged press stops visual feedback before release."""
        hovered={} if hovered.get('background') else hovered
        return {'hovered':deepcopy(hovered), 'pressed':{
            button:deepcopy(context) for button,(_,context,dragged) in self.pending.items()
            if context and not context.get('background') and context==hovered and not dragged}}
