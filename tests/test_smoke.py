"""Offline release smoke report and failure-path checks; no real collection."""
import io
import json
import socket
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app import smoke_test


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='comment-finder-SYNTHETIC-smoke-')
        self.addCleanup(self.temp.cleanup)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK FORBIDDEN'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_success_report_contains_actual_ui_checks_and_no_collection(self):
        report_path=Path(self.temp.name)/'report.json'
        with patch('app.App.start',side_effect=AssertionError('Smoke must not start collection')) as start:
            with redirect_stdout(io.StringIO()) as stdout:
                code=smoke_test(report_path)
        self.assertEqual(code,0,stdout.getvalue())
        report=json.loads(report_path.read_text())
        self.assertEqual(report['status'],'passed')
        self.assertEqual(report['mode'],'offline_synthetic')
        self.assertIn('backend_import_and_certificates',report['checks'])
        self.assertIn('tk_window_opened',report['checks'])
        self.assertIn('queue_progress_and_results',report['checks'])
        self.assertIn('result_filtering',report['checks'])
        self.assertIn('selected_comment_detail',report['checks'])
        self.assertEqual(json.loads(stdout.getvalue()),report)
        start.assert_not_called()

    def test_backend_resource_failure_returns_nonzero_and_report(self):
        path=Path(self.temp.name)/'failed.json'
        with patch('certifi.where',return_value=str(Path(self.temp.name)/'missing-SYNTHETIC.pem')):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(smoke_test(path),1)
        report=json.loads(path.read_text())
        self.assertEqual(report['status'],'failed')
        self.assertIn('certificate file is missing',report['error'])

    def test_report_write_failure_is_nonzero_and_visible(self):
        path=Path(self.temp.name)/'missing-directory'/'report.json'
        with patch('app.tk.Tk',side_effect=RuntimeError('SYNTHETIC window failure')):
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(smoke_test(path),1)
        report=json.loads(stdout.getvalue())
        self.assertEqual(report['status'],'failed')
        self.assertIn('SYNTHETIC window failure',report['error'])
        self.assertIn('report_write_error',report)
        self.assertFalse(path.exists())

if __name__=='__main__': unittest.main()
