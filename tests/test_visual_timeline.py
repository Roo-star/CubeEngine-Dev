import unittest
from unittest.mock import Mock
from srtp.visual_timeline import frame_index
from srtp.ursina_scene_backend import UrsinaSceneBackend


class VisualTimelineTests(unittest.TestCase):
    def test_timing_loop_and_final_frame(self):
        self.assertEqual([frame_index(t,3,4) for t in (0,.25,.5,.75)],[0,1,2,0])
        self.assertEqual(frame_index(100,3,4,False),2)
        with self.assertRaises(ValueError): frame_index(1,0,4)
        with self.assertRaises(ValueError): frame_index(1,3,float('nan'))

    def test_audio_event_counter_plays_once_volume_changes_do_not_retrigger(self):
        backend=UrsinaSceneBackend.__new__(UrsinaSceneBackend)
        backend.audio_states={}; backend.components={}
        sound=Mock(); backend._component=Mock(return_value=sound)
        props={'clip':'asset:audio.move','trigger':0,'volume':.7}
        backend._sync_audio(('node','sound'),None,props,True)
        sound.play.assert_not_called()
        props['trigger']=1
        backend._sync_audio(('node','sound'),None,props,True)
        backend._sync_audio(('node','sound'),None,props,True)
        props['volume']=.3
        backend._sync_audio(('node','sound'),None,props,True)
        sound.play.assert_called_once()
        props['trigger']=2
        backend._sync_audio(('node','sound'),None,props,True)
        self.assertEqual(sound.play.call_count,2)
        backend._sync_audio(('node','sound'),None,props,False)
        sound.stop.assert_called_with(destroy=False)


if __name__=='__main__': unittest.main()
