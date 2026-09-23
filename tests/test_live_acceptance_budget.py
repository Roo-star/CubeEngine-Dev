"""Offline safeguards for the explicitly authorized paid acceptance runner."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.run_live_conversion_acceptance import BoundedClient, prepare_audit
from srtp.llm_compiler_v1.client import OpenRouterLLMClient, LLMTransportError


class LiveAcceptanceBudgetTests(unittest.TestCase):
    def test_resume_preserves_receipt_and_cumulative_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'receipt.json'
            audit = prepare_audit(path, 8)
            audit.update(status='source_blocked', source={'ok': False})
            audit['http_requests'] = [{'number': 1}, {'number': 2}]
            path.write_text(json.dumps(audit), encoding='utf-8')
            with self.assertRaises(ValueError):
                prepare_audit(path, 8)
            with self.assertRaises(ValueError):
                prepare_audit(path, 7, resume=True)
            resumed = prepare_audit(path, 8, resume=True)
            self.assertEqual(resumed['http_requests'], audit['http_requests'])
            self.assertEqual(resumed['previous_attempts'][0]['source'], {'ok': False})
            self.assertEqual(json.loads(path.read_text()), audit)
            client = BoundedClient(resumed, lambda: None, 8)
            client.model = 'openai/gpt-6-sol'
            with patch.object(OpenRouterLLMClient, '_request', return_value={'usage': {}}) as transport:
                for _ in range(6):
                    client._request({})
                    client.phase = 'spatial_lift'
                with self.assertRaises(LLMTransportError):
                    client._request({})
                self.assertEqual(transport.call_count, 6)
            self.assertEqual([row['number'] for row in resumed['http_requests']], list(range(1, 9)))

    def test_missing_exhausted_or_invalid_receipt_cannot_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'receipt.json'
            with self.assertRaises(ValueError):
                prepare_audit(path, 8, resume=True)
            audit = prepare_audit(path, 8)
            audit['status'] = 'source_blocked'
            for rows in ([{'number': 2}], [{'number': i + 1} for i in range(8)]):
                audit['http_requests'] = rows
                path.write_text(json.dumps(audit), encoding='utf-8')
                with self.assertRaises(ValueError):
                    prepare_audit(path, 8, resume=True)


if __name__ == '__main__':
    unittest.main()
