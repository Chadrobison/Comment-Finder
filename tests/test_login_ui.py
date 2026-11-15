"""Offline login UI integration: synthetic credentials, mocked auth, no network."""
from contextlib import ExitStack
from pathlib import Path
import socket
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import auth
from app import App
from core import Stop

CREDENTIALS = auth.SessionCredentials('synthetic_login', {'csrftoken': 'SYNTHETIC_CSRF', 'sessionid': 'SYNTHETIC_SESSION'})


class EmptySource:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def posts(self, account):
        return iter(())


class LoginUiTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        forbidden = AssertionError('Network forbidden in offline login UI tests')
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.create_connection'):
            self.stack.enter_context(patch(target, side_effect=forbidden))
        self.stack.enter_context(patch('instaloader.Instaloader.load_session_from_file', side_effect=AssertionError('Real session file forbidden')))
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(prefix='SYNTHETIC-login-ui-'))
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk display required: {error}')
        self.callback_errors = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.store = Mock(spec=auth.SessionStore)
        self.store.load.return_value = None
        self.sources = []
        def source(settings, cancel):
            self.sources.append(settings)
            return EmptySource()
        self.app = App(self.root, source_factory=source, results_root=Path(self.temp), session_store=self.store)
        self.app.author.set('synthetic_author')
        self.app.owner.set('synthetic_owner')
        self.root.update()

    def tearDown(self):
        self.app.auth_cancel.set()
        self.app.cancel_event.set()
        if self.app.code_request:
            self.app.code_request['ready'].set()
        for name in ('auth_worker', 'worker'):
            worker = getattr(self.app, name, None)
            if worker:
                worker.join(timeout=4)
                self.assertFalse(worker.is_alive(), f'{name} did not stop')
        try:
            for timer in self.root.tk.call('after', 'info'):
                self.root.after_cancel(timer)
            self.root.destroy()
        except tk.TclError:
            pass
        self.assertEqual(self.callback_errors, [])

    def pump(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.root.update()
        self.assertTrue(predicate(), 'UI operation timed out')

    def fill(self, remember=False):
        self.app.show_login()
        self.app.login_username_var.set('@synthetic_login')
        self.app.login_password_var.set('SYNTHETIC_PASSWORD_ONLY')
        self.app.remember_var.set(remember)

    def test_constructor_does_not_load_and_explicit_restore_is_local(self):
        self.store.load.assert_not_called()
        self.store.load.return_value = CREDENTIALS
        with patch('app.auth.login', side_effect=AssertionError('Restore cannot login')):
            self.app.restore_saved_session()
        self.store.load.assert_called_once()
        self.assertEqual(self.app.credentials, CREDENTIALS)
        self.assertIn('remembered session', self.app.account_status.get())
        self.assertIn('still valid when you search', self.app.status.get())

    def test_password_masked_validation_clears_and_stays_idle(self):
        self.fill()
        self.assertTrue(self.app.password_entry.cget('show'))
        self.assertFalse(self.app.remember_var.get())
        self.app.login_username_var.set('bad email@example.invalid')
        with patch('app.auth.login') as login, patch('app.messagebox.showerror') as error:
            self.app.submit_login()
            error.assert_called_once()
            login.assert_not_called()
        self.assertEqual(self.app.login_password_var.get(), '')
        self.assertFalse(self.app.auth_running)

    def test_login_worker_clears_password_no_remember_then_search_uses_session(self):
        self.fill()
        threads = []
        def login(user, password, cancel, request_code, **kwargs):
            threads.append(threading.get_ident())
            self.assertEqual(user, 'synthetic_login')
            self.assertEqual(password, 'SYNTHETIC_PASSWORD_ONLY')
            self.assertFalse(kwargs['remember'])
            return CREDENTIALS
        with patch('app.auth.login', side_effect=login):
            self.app.submit_login()
            self.assertEqual(self.app.login_password_var.get(), '')
            self.pump(lambda: not self.app.auth_running)
        self.assertNotEqual(threads[0], threading.get_ident())
        self.store.save.assert_not_called()
        self.store.clear.assert_called_once()
        self.assertIn('this window only', self.app.account_status.get())
        self.assertEqual(self.app.credentials, CREDENTIALS)
        self.app.start()
        self.pump(lambda: not self.app.running)
        self.assertEqual(len(self.sources), 1)
        self.assertEqual(self.sources[0].session_data, CREDENTIALS.data)
        self.assertEqual(self.sources[0].login_user, CREDENTIALS.username)
        self.assertEqual(self.sources[0].session_file, '')
        self.assertNotIn('SYNTHETIC_PASSWORD_ONLY', self.app.status.get())

    def test_remember_saves_only_returned_credentials_and_logout_clears(self):
        self.fill(remember=True)
        with patch('app.auth.login', return_value=CREDENTIALS):
            self.app.submit_login()
            self.pump(lambda: not self.app.auth_running)
        self.store.save.assert_called_once_with(CREDENTIALS)
        self.assertIn('remembered session', self.app.account_status.get())
        self.app.logout()
        self.store.clear.assert_called_once()
        self.assertIsNone(self.app.credentials)
        self.assertEqual(self.app.account_status.get(), 'Not logged in')
        self.assertEqual(self.app.session.get(), '')

    def test_two_factor_prompt_runs_on_gui_thread_and_code_reaches_worker(self):
        self.fill()
        received = []
        def login(user, password, cancel, request_code, **kwargs):
            received.append(request_code())
            return CREDENTIALS
        def ask(*args, **kwargs):
            self.assertEqual(threading.current_thread(), threading.main_thread())
            return 'SYNTHETIC_123456'
        with patch('app.auth.login', side_effect=login), patch('app.simpledialog.askstring', side_effect=ask) as prompt:
            self.app.submit_login()
            self.pump(lambda: not self.app.auth_running)
            prompt.assert_called_once()
        self.assertEqual(received, ['SYNTHETIC_123456'])
        self.assertEqual(self.app.credentials, CREDENTIALS)

    def test_two_factor_dialog_cancel_unblocks_worker_without_session(self):
        self.fill()
        def login(user, password, cancel, request_code, **kwargs):
            request_code()
            raise AssertionError('Cancelled prompt must abort worker callback')
        with patch('app.auth.login', side_effect=login), patch('app.simpledialog.askstring', return_value=None):
            self.app.submit_login()
            self.pump(lambda: not self.app.auth_running)
        self.assertIsNone(self.app.credentials)
        self.assertIn('cancelled', self.app.status.get())
        self.store.save.assert_not_called()
        self.store.clear.assert_not_called()

    def test_cancel_active_login_blocks_search_and_preserves_previous_credentials(self):
        self.app.credentials = CREDENTIALS
        self.fill()
        entered = threading.Event()
        def login(user, password, cancel, request_code, **kwargs):
            entered.set()
            if not cancel.wait(3):
                raise AssertionError('Cancel was not delivered')
            raise Stop('cancelled')
        with patch('app.auth.login', side_effect=login):
            self.app.submit_login()
            self.pump(entered.is_set)
            self.assertEqual(str(self.app.start_button['state']), 'disabled')
            self.app.start()
            self.assertEqual(self.sources, [])
            self.app.cancel()
            self.pump(lambda: not self.app.auth_running)
        self.assertEqual(self.app.credentials, CREDENTIALS)
        self.assertIn('cancelled', self.app.status.get())
        self.store.save.assert_not_called()

    def test_cancel_during_save_removes_token_and_does_not_activate_login(self):
        self.fill(remember=True)
        saving, release_save = threading.Event(), threading.Event()
        def save(credentials):
            saving.set()
            if not release_save.wait(3):
                raise AssertionError('Synthetic save was not released')
        self.store.save.side_effect = save
        with patch('app.auth.login', return_value=CREDENTIALS):
            self.app.submit_login()
            try:
                self.pump(saving.is_set)
                self.app.cancel()
            finally:
                release_save.set()
            self.pump(lambda: not self.app.auth_running)
        self.store.clear.assert_called_once()
        self.assertIsNone(self.app.credentials)
        self.assertIn('cancelled', self.app.status.get())
        self.assertNotIn('Logged in.', self.app.status.get())

    def test_cancel_during_save_cleanup_failure_remains_actionable(self):
        self.fill(remember=True)
        saving, release_save = threading.Event(), threading.Event()
        def save(credentials):
            saving.set()
            if not release_save.wait(3):
                raise AssertionError('Synthetic save was not released')
        self.store.save.side_effect = save
        self.store.clear.side_effect = auth.AuthError('SYNTHETIC cleanup failure')
        with patch('app.auth.login', return_value=CREDENTIALS):
            self.app.submit_login()
            try:
                self.pump(saving.is_set)
                self.app.cancel()
            finally:
                release_save.set()
            self.pump(lambda: not self.app.auth_running)
        self.assertIsNone(self.app.credentials)
        self.assertIn('cancelled', self.app.status.get())
        self.assertIn('could not be removed', self.app.status.get())
        self.assertEqual(str(self.app.logout_button['state']), 'normal')

    def test_cancel_after_worker_success_before_ui_event_removes_saved_token(self):
        self.fill(remember=True)
        with patch('app.auth.login', return_value=CREDENTIALS):
            self.app.submit_login()
            # Do not pump Tk: success is queued, but not yet accepted by the UI.
            self.app.auth_worker.join(timeout=3)
            self.assertFalse(self.app.auth_worker.is_alive())
            self.app.cancel()
            self.pump(lambda: not self.app.auth_running)
        self.store.save.assert_called_once_with(CREDENTIALS)
        self.store.clear.assert_called_once()
        self.assertIsNone(self.app.credentials)
        self.assertIn('cancelled', self.app.status.get())

    def test_auth_error_is_visible_without_password(self):
        self.fill()
        with patch('app.auth.login', side_effect=auth.AuthError('SYNTHETIC login rejected')):
            self.app.submit_login()
            self.pump(lambda: not self.app.auth_running)
        self.assertEqual(self.app.status.get(), 'SYNTHETIC login rejected')
        self.assertNotIn('SYNTHETIC_PASSWORD_ONLY', self.app.status.get())
        self.assertIsNone(self.app.credentials)
        self.assertEqual(str(self.app.start_button['state']), 'normal')

    def test_persistence_failure_keeps_memory_session_and_warns(self):
        self.fill(remember=True)
        self.store.save.side_effect = auth.AuthError('SYNTHETIC storage failure')
        with patch('app.auth.login', return_value=CREDENTIALS):
            self.app.submit_login()
            self.pump(lambda: not self.app.auth_running)
        self.assertEqual(self.app.credentials, CREDENTIALS)
        self.assertIn('could not be updated', self.app.status.get())
        self.assertIn('this window only', self.app.account_status.get())

    def test_logout_failure_drops_memory_and_warns_saved_token_may_remain(self):
        self.app.credentials = CREDENTIALS
        self.store.clear.side_effect = auth.AuthError('SYNTHETIC remove failure')
        self.app.logout()
        self.assertIsNone(self.app.credentials)
        self.assertIn('may be restored', self.app.status.get())
        self.assertEqual(str(self.app.logout_button['state']), 'normal')

    def test_legacy_file_requires_explicit_confirmation_and_overrides_memory(self):
        self.app.credentials = CREDENTIALS
        legacy = Path(self.temp) / 'SYNTHETIC_NOT_A_REAL_SESSION'
        legacy.write_text('SYNTHETIC fixture; never loaded')
        self.app.login.set('synthetic_legacy')
        self.app.session.set(str(legacy))
        with patch('app.messagebox.askokcancel', return_value=False) as confirm:
            self.app.start()
            confirm.assert_called_once()
        self.assertEqual(self.sources, [])
        with patch('app.messagebox.askokcancel', return_value=True):
            self.app.start()
            self.pump(lambda: not self.app.running)
        self.assertIsNone(self.sources[0].session_data)
        self.assertEqual(self.sources[0].session_file, str(legacy))
        self.assertEqual(self.sources[0].login_user, 'synthetic_legacy')


if __name__ == '__main__':
    unittest.main()
