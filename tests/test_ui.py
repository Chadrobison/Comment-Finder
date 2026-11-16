"""Offline UI integration tests; all account/comment names and data are synthetic.
Run with a display (macOS normally, or DISPLAY=:99 under local Xvfb).
"""
import csv
import json
import socket
import tempfile
import threading
import time
import tkinter as tk
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from app import App
from core import Stop


def comment(cid, text, author='synthetic_author', answers=()):
    return NS(id=cid, text=text, owner=NS(username=author),
              created_at_utc=datetime(2026, 1, 1), answers=iter(answers))


class SyntheticSource:
    def __init__(self, settings, cancel, mode='normal'):
        self.cancel, self.mode = cancel, mode
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def posts(self, account):
        if self.mode.startswith('fail:'):
            raise Stop(self.mode.split(':', 1)[1], 'Synthetic failure only')
        def comments():
            yield comment(1, 'SYNTHETIC parent comment\nLong detail text.')
            if self.mode == 'slow':
                # Represents one active operation; no network is used.
                self.cancel.wait(3)
                if self.cancel.is_set():
                    raise Stop('cancelled')
            yield comment(2, 'SYNTHETIC other author', author='synthetic_other',
                          answers=[comment(3, '=SYNTHETIC reply')])
        yield NS(mediaid=100, shortcode='SYNTHETIC_ONLY', owner_username=account, get_comments=comments)


class UiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='comment-finder-SYNTHETIC-')
        self.net = patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden in offline UI tests'))
        self.net.start()
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.net.stop()
            self.temp.cleanup()
            self.skipTest(f'Tk display required: {error}')
        self.errors = []
        self.root.report_callback_exception = lambda *a: self.errors.append(a)
        self.mode = 'normal'
        self.app = App(self.root, lambda settings,cancel: SyntheticSource(settings,cancel,self.mode), Path(self.temp.name))
        self.app.author.set('@synthetic_author')
        self.app.owner.set('synthetic_owner')
        self.root.update()

    def tearDown(self):
        if hasattr(self.app, 'worker'):
            self.app.cancel_event.set()
            self.app.worker.join(timeout=4)
        try:
            self.root.after_cancel(self.app.poll_id)
            self.root.destroy()
        except tk.TclError:
            pass
        self.net.stop()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def pump_until(self, condition, timeout=4):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.root.update()
        self.assertTrue(condition(), 'UI condition timed out')

    def complete_search(self):
        self.app.start()
        self.pump_until(lambda: not self.app.running)

    def test_validation_stays_idle(self):
        self.app.author.set('https://invalid.example')
        with patch('app.messagebox.showerror') as error:
            self.app.start()
            error.assert_called_once()
        self.assertFalse(self.app.running)
        self.assertFalse(hasattr(self.app, 'worker'))
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_start_progress_results_detail_filter_and_open(self):
        self.complete_search()
        self.assertEqual(len(self.app.rows), 2)
        self.assertIn('2 comments + 1 replies examined', self.app.progress.get())
        self.assertIn('2 matches', self.app.progress.get())
        self.assertIn('Bounded search finished', self.app.status.get())
        self.app.table.selection_set('0')
        self.app.select()
        self.assertIn('Long detail text.', self.app.detail.get('1.0', 'end'))
        with patch('app.webbrowser.open') as opened:
            self.app.open_post()
            opened.assert_called_once_with('https://www.instagram.com/p/SYNTHETIC_ONLY/')
        self.app.kind.set('reply')
        self.assertEqual(len(self.app.visible), 1)
        self.assertEqual(self.app.visible[0]['comment_id'], '3')
        self.app.query.set('not present')
        self.assertEqual(self.app.visible, [])
        self.assertEqual(str(self.app.open_button['state']), 'disabled')
        self.app.query.set('SYNTHETIC')
        self.assertEqual(len(self.app.visible), 1)

    def test_filtered_export_and_folder(self):
        self.complete_search()
        self.app.kind.set('reply')
        dest = Path(self.temp.name) / 'SYNTHETIC_export.csv'
        with patch('app.filedialog.asksaveasfilename', return_value=str(dest)), patch('app.messagebox.showinfo'):
            self.app.export()
        with dest.open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['text'], "'=SYNTHETIC reply")
        with patch('app.subprocess.Popen') as opened:
            self.app.open_folder()
            self.assertEqual(opened.call_args.args[0][-1], str(self.app.folder))

    def test_export_failure_is_visible(self):
        self.complete_search()
        with patch('app.filedialog.asksaveasfilename', return_value=str(Path(self.temp.name) / 'x.csv')), patch('app.save_csv', side_effect=OSError('SYNTHETIC disk failure')), patch('app.messagebox.showerror') as error:
            self.app.export()
            self.assertIn('SYNTHETIC disk failure', error.call_args.args[1])

    def test_cancel_preserves_matches_and_checkpoints(self):
        self.mode = 'slow'
        self.app.start()
        self.pump_until(lambda: len(self.app.rows) == 1)
        self.assertEqual(str(self.app.start_button['state']), 'disabled')
        self.app.cancel()
        self.pump_until(lambda: not self.app.running)
        self.assertEqual(len(self.app.rows), 1)
        self.assertIn('cancelled', self.app.status.get())
        self.assertEqual(json.loads((self.app.folder / 'status.json').read_text())['status'], 'cancelled')
        with (self.app.folder / 'matching_comments.csv').open() as f:
            self.assertEqual(len(list(csv.DictReader(f))), 1)
        self.assertEqual(str(self.app.start_button['state']), 'normal')

    def test_failure_messages_reach_ui(self):
        for mode, phrase in [('authentication','Authentication is required'), ('denied','Instagram refused this request'), ('blocked','blocked or rate-limited'), ('error','network or other error')]:
            self.mode = 'fail:' + mode
            self.complete_search()
            self.assertIn(phrase, self.app.status.get())
            self.assertIn('No comments or replies were examined', self.app.status.get())
            self.assertIn('cannot determine whether', self.app.status.get())

    def test_advanced_collapsed_and_session_picker_never_loads(self):
        self.assertFalse(self.app.advanced.winfo_manager())
        self.app.toggle_advanced()
        self.root.update()
        self.assertEqual(self.app.advanced.winfo_manager(), 'pack')
        with patch('app.filedialog.askopenfilename', return_value='/SYNTHETIC/NOT_A_REAL_SESSION'):
            self.app.pick_session()
        self.assertEqual(self.app.session.get(), '/SYNTHETIC/NOT_A_REAL_SESSION')
        self.app.toggle_advanced()
        self.assertFalse(self.app.advanced.winfo_manager())

    def test_advanced_at_minimum_window_keeps_main_controls_visible(self):
        self.root.geometry('850x720')
        self.app.toggle_advanced()
        self.root.update()
        for widget in (self.app.detail, self.app.open_button, self.app.start_button):
            self.assertTrue(widget.winfo_ismapped(), 'Advanced settings hid a main control')

    def test_new_search_preserves_old_output(self):
        self.complete_search()
        first = self.app.folder
        self.complete_search()
        self.assertNotEqual(first, self.app.folder)
        self.assertTrue((first / 'matching_comments.csv').exists())


if __name__ == '__main__':
    unittest.main()
