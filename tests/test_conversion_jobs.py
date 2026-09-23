import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock

from srtp.conversion_jobs import CancellationToken, CompilationCancelled, ConversionJobs
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
from srtp.workbench import SrtpWorkbench


class ConversionJobTests(unittest.TestCase):
    def test_cancel_in_flight_and_duplicate_click(self):
        jobs=ConversionJobs(); entered=Event(); release=Event()
        def work(progress, token):
            progress({'stage':'rule_ir'}); entered.set(); release.wait(2)
            token.check()
            return 'must not publish'
        self.assertTrue(jobs.submit({'generation':0},work))
        self.assertTrue(entered.wait(2))
        self.assertFalse(jobs.submit({},lambda *args:None))
        self.assertTrue(jobs.cancel())
        thread=jobs.active['thread']; release.set(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual([e['kind'] for e in jobs.poll()],['progress','cancelled'])
        self.assertIsNone(jobs.active)

    def test_cancelled_compiler_cannot_replace_previous_bundle(self):
        token=CancellationToken(); token.cancel()
        compiler=SourceToIRCompiler(cancel_token=token)
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'source'; target.mkdir()
            previous=target/'project.manifest.json'; previous.write_text('previous')
            with self.assertRaises(CompilationCancelled):
                compiler._write_compile_artifacts(target,Mock())
            self.assertEqual(previous.read_text(),'previous')
        done=CancellationToken()
        with done.publication(): pass
        self.assertFalse(done.cancel())

    def test_previous_source_result_never_updates_current_ui(self):
        controller=SrtpWorkbench.__new__(SrtpWorkbench)
        controller.conversion_jobs=Mock()
        controller._source_generation=3
        controller.conversion_jobs.poll.return_value=[{'kind':'complete','value':None,
            'tag':{'generation':2,'kind':'source','out_dir':Path('.')}}]
        controller._finish_source_compilation=Mock()
        controller.poll_conversion()
        controller._finish_source_compilation.assert_not_called()


if __name__=='__main__': unittest.main()
