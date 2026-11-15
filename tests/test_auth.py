"""Synthetic authentication tests. No real login, session, or network is allowed."""
import io
import json
import os
import socket
import stat
import tempfile
import threading
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

import instaloader
import requests
from auth import AuthError, SessionCredentials, SessionStore, login
from core import Stop
from source import RequestGuard

SYNTHETIC_PASSWORD = 'SYNTHETIC-NOT-A-REAL-PASSWORD'
SYNTHETIC_CODE = 'SYNTHETIC-NOT-A-REAL-CODE'
COOKIES = {'sessionid': 'SYNTHETIC-SESSION-TOKEN', 'csrftoken': 'SYNTHETIC-CSRF', 'ds_user_id': 'SYNTHETIC-ID'}


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='comment-finder-SYNTHETIC-auth-')
        self.addCleanup(self.temp.cleanup)
        self.cancel = threading.Event()
        self.store = SessionStore(Path(self.temp.name) / 'app-session')
        self.loader = Mock()
        self.loader.save_session.return_value = dict(COOKIES)
        factory = patch('auth.instaloader.Instaloader', return_value=self.loader)
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('NETWORK PROHIBITED'))
            guard.start()
            self.addCleanup(guard.stop)
        for target in ('pickle.load','pickle.loads'):
            guard = patch(target, side_effect=AssertionError('PICKLE PROHIBITED'))
            guard.start()
            self.addCleanup(guard.stop)

    def attempt(self, **kwargs):
        defaults = dict(username='@Synthetic_Login', password=SYNTHETIC_PASSWORD,
                        cancel=self.cancel, request_code=Mock(return_value=SYNTHETIC_CODE), store=self.store)
        defaults.update(kwargs)
        return login(**defaults)

    def test_success_memory_only_has_no_disk_or_sensitive_output(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = self.attempt()
        self.assertEqual(result.username, 'synthetic_login')
        self.assertEqual(result.data, COOKIES)
        self.assertFalse(self.store.directory.exists())
        self.loader.login.assert_called_once_with('synthetic_login', SYNTHETIC_PASSWORD)
        self.loader.two_factor_login.assert_not_called()
        self.loader.close.assert_called_once()
        self.assertEqual(stdout.getvalue()+stderr.getvalue(), '')
        self.assertNotIn(COOKIES['sessionid'], repr(result))
        self.assertNotIn(SYNTHETIC_PASSWORD, repr(result))
        self.assertEqual(self.factory.call_args.kwargs['max_connection_attempts'],1)
        self.assertEqual(self.factory.call_args.kwargs['request_timeout'],30)

    def test_remember_writes_only_cookie_json_private_permissions(self):
        result = self.attempt(remember=True)
        payload = json.loads(self.store.path.read_text())
        self.assertEqual(payload, {'version':1,'username':'synthetic_login','cookies':COOKIES})
        self.assertEqual(stat.S_IMODE(self.store.directory.stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode),0o600)
        self.assertNotIn(SYNTHETIC_PASSWORD,self.store.path.read_text())
        self.assertNotIn(SYNTHETIC_CODE,self.store.path.read_text())
        self.assertEqual(self.store.load(),result)
        self.store.clear()
        self.assertFalse(self.store.path.exists())
        self.assertIsNone(self.store.load())

    def test_two_factor_success_has_one_code_request_and_no_code_saved(self):
        self.loader.login.side_effect=instaloader.TwoFactorAuthRequiredException('SYNTHETIC')
        code=Mock(return_value='  '+SYNTHETIC_CODE+'  ')
        result=self.attempt(request_code=code,remember=True)
        code.assert_called_once_with()
        self.loader.two_factor_login.assert_called_once_with(SYNTHETIC_CODE)
        self.assertEqual(result.data,COOKIES)
        self.assertNotIn(SYNTHETIC_CODE,self.store.path.read_text())
        self.assertNotIn(SYNTHETIC_PASSWORD,self.store.path.read_text())

    def test_wrong_code_has_no_retry_and_sanitized_error(self):
        self.loader.login.side_effect=instaloader.TwoFactorAuthRequiredException('SYNTHETIC')
        self.loader.two_factor_login.side_effect=instaloader.BadCredentialsException(SYNTHETIC_CODE+' '+COOKIES['sessionid'])
        code=Mock(return_value=SYNTHETIC_CODE)
        with self.assertRaises(AuthError) as caught:
            self.attempt(request_code=code,remember=True)
        self.assertIn('verification code',str(caught.exception))
        self.assertNotIn(SYNTHETIC_CODE,str(caught.exception))
        self.assertNotIn(COOKIES['sessionid'],str(caught.exception))
        self.loader.two_factor_login.assert_called_once()
        self.loader.save_session.assert_not_called()
        self.assertFalse(self.store.directory.exists())
        self.loader.close.assert_called_once()

    def test_cancel_before_login_makes_no_loader(self):
        self.cancel.set()
        with self.assertRaises(Stop) as caught: self.attempt(remember=True)
        self.assertEqual(caught.exception.status,'cancelled')
        self.factory.assert_not_called()
        self.assertFalse(self.store.directory.exists())

    def test_cancel_during_two_factor_wait_makes_no_verification_request(self):
        self.loader.login.side_effect=instaloader.TwoFactorAuthRequiredException('SYNTHETIC')
        def cancelled_code():
            self.cancel.set()
            return SYNTHETIC_CODE
        with self.assertRaises(Stop) as caught: self.attempt(request_code=cancelled_code,remember=True)
        self.assertEqual(caught.exception.status,'cancelled')
        self.loader.two_factor_login.assert_not_called()
        self.loader.save_session.assert_not_called()
        self.assertFalse(self.store.directory.exists())
        self.loader.close.assert_called_once()

    def test_none_two_factor_response_is_cancellation(self):
        self.loader.login.side_effect=instaloader.TwoFactorAuthRequiredException('SYNTHETIC')
        with self.assertRaises(Stop) as caught: self.attempt(request_code=lambda:None)
        self.assertEqual(caught.exception.status,'cancelled')
        self.loader.two_factor_login.assert_not_called()

    def test_cancel_after_success_before_persistence(self):
        self.loader.login.side_effect=lambda *a:self.cancel.set()
        with self.assertRaises(Stop): self.attempt(remember=True)
        self.loader.save_session.assert_not_called()
        self.assertFalse(self.store.directory.exists())

    def test_cancel_after_cookie_extraction_before_persistence(self):
        def session():
            self.cancel.set()
            return dict(COOKIES)
        self.loader.save_session.side_effect=session
        with self.assertRaises(Stop): self.attempt(remember=True)
        self.assertFalse(self.store.directory.exists())

    def test_empty_two_factor_code_is_not_sent(self):
        self.loader.login.side_effect=instaloader.TwoFactorAuthRequiredException('SYNTHETIC')
        with self.assertRaises(AuthError): self.attempt(request_code=lambda:'  ')
        self.loader.two_factor_login.assert_not_called()

    def test_login_failures_never_expose_raw_response_or_secrets(self):
        secret=SYNTHETIC_PASSWORD+' '+COOKIES['sessionid']+' https://secret.invalid/checkpoint'
        for error in [instaloader.BadCredentialsException(secret), requests.Timeout(secret),
                      RuntimeError(secret), Stop('blocked',secret), Stop('denied',secret), Stop('authentication',secret)]:
            with self.subTest(kind=type(error).__name__):
                self.loader.login.side_effect=error
                stdout, stderr=io.StringIO(),io.StringIO()
                with redirect_stdout(stdout),redirect_stderr(stderr),self.assertRaises(AuthError) as caught:
                    self.attempt(remember=True)
                output=str(caught.exception)+stdout.getvalue()+stderr.getvalue()
                for forbidden in (SYNTHETIC_PASSWORD,COOKIES['sessionid'],'https://secret.invalid'):
                    self.assertNotIn(forbidden,output)
                self.assertFalse(self.store.directory.exists())

    def test_login_transport_is_guarded_and_blocks_next_send_after_cancel(self):
        request=requests.Request('POST','https://offline.invalid/SYNTHETIC-login').prepare()
        response=requests.Response(); response.status_code=200; response._content=b'{}'; response._content_consumed=True
        def transport(session, request, **kwargs):
            self.assertEqual(kwargs['timeout'],30)
            self.cancel.set()
            return response
        def mocked_login(*args):
            # Model direct Requests use by Instaloader.login (not context.get_json).
            requests.Session().send(request)
        self.loader.login.side_effect=mocked_login
        with patch.object(requests.Session,'send',autospec=True,side_effect=transport) as send:
            with self.assertRaises(Stop) as caught:self.attempt()
            self.assertEqual(caught.exception.status,'cancelled')
            self.assertEqual(send.call_count,1)
        self.loader.save_session.assert_not_called()

    def test_concurrent_operation_prevents_login(self):
        with RequestGuard(threading.Event()):
            with self.assertRaises(AuthError):self.attempt()
        self.factory.assert_not_called()

    def test_invalid_credentials_rejected_before_loader(self):
        for values in [dict(username='https://example.invalid/a'),dict(password=''),dict(password=None)]:
            with self.subTest(values=values),self.assertRaises(AuthError):self.attempt(**values)
        self.factory.assert_not_called()

    def test_session_atomic_failure_preserves_old_private_file(self):
        original=SessionCredentials('synthetic_login',dict(COOKIES))
        self.store.save(original)
        before=self.store.path.read_bytes()
        with patch('core.os.replace',side_effect=OSError(SYNTHETIC_PASSWORD)):
            with self.assertRaises(AuthError) as caught:
                self.store.save(SessionCredentials('synthetic_login',dict(COOKIES,sessionid='SYNTHETIC-NEW')))
        self.assertNotIn(SYNTHETIC_PASSWORD,str(caught.exception))
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode),0o600)
        self.assertEqual(list(self.store.directory.glob('.checkpoint-*')),[])

    def test_rejects_nested_non_string_or_credential_session_fields(self):
        invalid=[{},dict(COOKIES,extra={'nested':'secret'}),dict(COOKIES,sessionid=123),
                 dict(COOKIES,password=SYNTHETIC_PASSWORD),dict(COOKIES,verificationcode=SYNTHETIC_CODE)]
        for data in invalid:
            with self.subTest(data=data),self.assertRaises(AuthError):
                self.store.save(SessionCredentials('synthetic_login',data))
        self.assertFalse(self.store.directory.exists())

    def test_malformed_or_pickle_session_never_executes(self):
        self.store.directory.mkdir(mode=0o700)
        for text in ('not JSON','[]','{"version":2,"username":"synthetic","cookies":{}}', '\u0080pickle-like-SYNTHETIC'):
            self.store.path.write_text(text)
            self.store.path.chmod(0o600)
            with self.subTest(text=text),self.assertRaises(AuthError): self.store.load()

    def test_broad_permission_and_symlink_session_rejected(self):
        self.store.save(SessionCredentials('synthetic_login',dict(COOKIES)))
        self.store.path.chmod(0o644)
        with self.assertRaises(AuthError):self.store.load()
        self.store.path.unlink()
        other=Path(self.temp.name)/'SYNTHETIC-other.json'; other.write_text('{}')
        self.store.path.symlink_to(other)
        with self.assertRaises(AuthError):self.store.load()

if __name__=='__main__':unittest.main()
