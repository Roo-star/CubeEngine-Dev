import unittest
from srtp.pointer_gesture import PointerGesture


class PointerGestureTests(unittest.TestCase):
    def test_drag_returning_to_start_is_not_a_click(self):
        gesture=PointerGesture(); cell={'coordinate':[1,2,3]}
        gesture.press('right',(0,0),cell)
        gesture.move((.1,0))
        self.assertIsNone(gesture.release('right',(0,0),cell))

    def test_click_requires_same_cell_and_no_overlay_or_layer_change(self):
        gesture=PointerGesture(); cell={'coordinate':[1,2,3]}
        gesture.press('left',(0,0),cell)
        self.assertEqual(gesture.release('left',(.001,0),cell),cell)
        for after in ({},{'coordinate':[1,2,4]}):
            gesture.press('left',(0,0),cell)
            self.assertIsNone(gesture.release('left',(0,0),after))
        gesture.press('left',(0,0),cell); gesture.clear()
        self.assertIsNone(gesture.release('left',(0,0),cell))


if __name__=='__main__': unittest.main()
