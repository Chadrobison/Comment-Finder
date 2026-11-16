"""Offline transport verification. Every test denies socket connections.

No Instagram account, real session, login, or live response is used.
"""
import json
import socket
import threading
import unittest
from unittest.mock import Mock, patch

import instaloader
import requests
from core import Settings, Stop, classify
from source import InstagramSource


def response(code=200, body=None):
    result = requests.Response()
    result.status_code = code
    result._content = json.dumps(body or {'status': 'ok'}).encode()
    result._content_consumed = True
    result.headers['Content-Type'] = 'application/json'
    return result


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.block = patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden in offline tests'))
        self.block.start()
        self.addCleanup(self.block.stop)
        self.settings = Settings('synthetic_author', 'synthetic_owner')
        self.cancel = threading.Event()
        self.request = requests.Request('GET', 'https://offline.invalid/fixture').prepare()
        self.session_block = patch.object(instaloader.Instaloader, 'load_session_from_file', side_effect=AssertionError('No real sessions may be loaded'))
        self.load = self.session_block.start()
        self.addCleanup(self.session_block.stop)

    def test_http_stops_are_latched_across_sessions(self):
        for code, expected in [(429, 'blocked'), (403, 'denied'), (401, 'authentication')]:
            with self.subTest(code=code):
                with patch.object(requests.Session, 'send', return_value=response(code)) as transport:
                    with InstagramSource(self.settings, self.cancel) as source:
                        with self.assertRaises(Stop) as first:
                            requests.Session().send(self.request)
                        self.assertEqual(first.exception.status, expected)
                        self.assertEqual(source.stopped, expected)
                        with self.assertRaises(Stop):
                            requests.Session().send(self.request)
                        self.assertEqual(transport.call_count, 1)
                self.load.assert_not_called()

    def test_challenges_stop_without_retry(self):
        for message in ['challenge_required', 'checkpoint_required', 'feedback_required']:
            with self.subTest(message=message), patch.object(requests.Session, 'send', return_value=response(400, {'message': message})) as transport:
                with InstagramSource(self.settings, self.cancel):
                    with self.assertRaises(Stop) as caught:
                        requests.Session().send(self.request)
                    self.assertEqual(caught.exception.status, 'blocked')
                    with self.assertRaises(Stop):
                        requests.Session().send(self.request)
                    self.assertEqual(transport.call_count, 1)

    def test_cancel_before_source_restores_transport(self):
        self.cancel.set()
        with patch.object(requests.Session, 'send') as transport:
            with self.assertRaises(Stop) as caught:
                with InstagramSource(self.settings, self.cancel):
                    self.fail('Cancelled source must not enter')
            self.assertEqual(caught.exception.status, 'cancelled')
            transport.assert_not_called()
            self.assertIs(requests.Session.send, transport)

    def test_cancel_before_send(self):
        with patch.object(requests.Session, 'send') as transport:
            with InstagramSource(self.settings, self.cancel):
                self.cancel.set()
                with self.assertRaises(Stop) as caught:
                    requests.Session().send(self.request)
                self.assertEqual(caught.exception.status, 'cancelled')
                transport.assert_not_called()

    def test_cancel_during_active_response_blocks_next_send(self):
        def finish_active(*args, **kwargs):
            self.cancel.set()
            return response()
        with patch.object(requests.Session, 'send', side_effect=finish_active) as transport:
            with InstagramSource(self.settings, self.cancel):
                with self.assertRaises(Stop):
                    requests.Session().send(self.request)
                with self.assertRaises(Stop):
                    requests.Session().send(self.request)
                self.assertEqual(transport.call_count, 1)

    def test_redirect_followup_passes_stop_guard(self):
        """Model Session.send's nested redirect send; no network is contacted."""
        calls = []
        def redirect(session, request, **kwargs):
            calls.append(request.url)
            self.cancel.set()
            redirected = requests.Request('GET', 'https://offline.invalid/redirected').prepare()
            return session.send(redirected)
        with patch.object(requests.Session, 'send', redirect):
            with InstagramSource(self.settings, self.cancel):
                with self.assertRaises(Stop) as caught:
                    requests.Session().send(self.request)
                self.assertEqual(caught.exception.status, 'cancelled')
                self.assertEqual(calls, ['https://offline.invalid/fixture'])

    def test_success_and_cleanup_no_session_loading(self):
        with patch.object(requests.Session, 'send', return_value=response()) as transport:
            with InstagramSource(self.settings, self.cancel):
                self.assertEqual(requests.Session().send(self.request).status_code, 200)
            self.assertIs(requests.Session.send, transport)
            self.load.assert_not_called()

    def test_rejection_reasons_are_allowlisted_and_latched_without_raw_body(self):
        cases=[
            (400, {'message':'feedback_required SYNTHETIC-SECRET'}, 'blocked', 'feedback_required'),
            (400, {'error_type':'challenge_required'}, 'blocked', 'challenge_required'),
            (400, {'checkpoint_url':'https://secret.invalid/SYNTHETIC-SECRET'}, 'blocked', 'checkpoint_required'),
            (400, {'message':'login_required'}, 'authentication', 'login_required'),
            (400, {'errors':[{'message':'invalid doc id SYNTHETIC-SECRET'}]}, 'error', 'invalid doc id'),
            (400, {'message':'Please wait a few minutes SYNTHETIC-SECRET'}, 'blocked', 'please_wait'),
            (200, {'message':'feedback_required'}, 'blocked', 'feedback_required'),
            (400, ['SYNTHETIC-SECRET'], 'error', None),
        ]
        for code,body,status,reason in cases:
            with self.subTest(code=code,body=body),patch.object(requests.Session,'send',return_value=response(code,body)) as transport:
                with InstagramSource(self.settings,self.cancel) as source:
                    source.set_phase('replies')
                    with self.assertRaises(Stop) as caught:
                        requests.Session().send(self.request)
                    self.assertEqual(caught.exception.status,status)
                    self.assertIn('replies',str(caught.exception))
                    self.assertNotIn('SYNTHETIC-SECRET',str(caught.exception))
                    self.assertNotIn('secret.invalid',str(caught.exception))
                    if reason:self.assertIn(reason,str(caught.exception))
                    with self.assertRaises(Stop) as repeat:
                        requests.Session().send(self.request)
                    self.assertEqual(str(repeat.exception),str(caught.exception))
                    self.assertEqual(transport.call_count,1)

    def test_managed_session_uses_dictionary_interface_only(self):
        data = {'sessionid': 'SYNTHETIC-SESSION-TOKEN', 'csrftoken': 'SYNTHETIC-CSRF'}
        settings = Settings('synthetic_author', 'synthetic_owner', login_user='synthetic_login',
                            session_data=data).validate()
        with patch.object(instaloader.Instaloader, 'load_session') as load_data:
            with patch.object(requests.Session, 'send') as transport:
                with InstagramSource(settings, self.cancel):
                    load_data.assert_called_once_with('synthetic_login', data)
                transport.assert_not_called()
        self.load.assert_not_called()
        self.assertNotIn('SYNTHETIC-SESSION-TOKEN', repr(settings))

    def test_rate_controller_cancel_and_429(self):
        with patch.object(requests.Session, 'send') as transport:
            with InstagramSource(self.settings, self.cancel) as source:
                controller = source.loader.context._rate_controller
                with self.assertRaises(Stop) as caught:
                    controller.handle_429('synthetic')
                self.assertEqual(caught.exception.status, 'blocked')
                transport.assert_not_called()
        self.cancel.set()
        # Enter with a fresh event, then cancel during a rate-controller wait.
        self.cancel.clear()
        with patch.object(requests.Session, 'send') as transport:
            with InstagramSource(self.settings, self.cancel) as source:
                self.cancel.set()
                with self.assertRaises(Stop):
                    source.loader.context._rate_controller.sleep(100)
                transport.assert_not_called()

    def test_error_categories(self):
        for error, status in [
            (Stop('cancelled'), 'cancelled'),
            (instaloader.LoginRequiredException('synthetic'), 'authentication'),
            (instaloader.AbortDownloadException("Redirected to login page. You've been logged out"), 'authentication'),
            (instaloader.PrivateProfileNotFollowedException('synthetic'), 'denied'),
            (instaloader.TooManyRequestsException('synthetic'), 'blocked'),
            (requests.Timeout('synthetic'), 'error'),
        ]:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(classify(error), status)


if __name__ == '__main__':
    unittest.main()
