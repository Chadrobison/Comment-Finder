"""Documented Instaloader APIs with a shared transport stop guard.

Login and collection are exclusive: the scoped Requests guard also protects
redirects and temporary sessions created by Instaloader. No credentials are discovered.
"""
import threading
from contextlib import contextmanager
from unittest.mock import patch
from urllib.parse import urlsplit
import instaloader
import requests
from core import Stop
from compatibility import install_metadata_routes

_OPERATION_LOCK = threading.Lock()

# Only these fixed identifiers may enter diagnostics. Never retain response bodies,
# cookie/header values, query variables or challenge URLs.
_REASONS = ('challenge_required', 'checkpoint_required', 'feedback_required',
            'login_required', 'execution error', 'invalid doc id')
_PHASES = {'connection', 'profile lookup', 'profile metadata', 'post metadata', 'post listing',
           'comments', 'replies'}


def response_reason(response):
    try:
        body = response.json()
    except (ValueError, AttributeError):
        return ''
    if not isinstance(body, dict):
        return ''
    messages = [body.get('message'), body.get('error_type')]
    errors = body.get('errors')
    if isinstance(errors, list):
        messages.extend(error.get('message') for error in errors if isinstance(error, dict))
    for reason in _REASONS:
        if any(isinstance(message, str) and reason in message.casefold() for message in messages):
            return reason
    if body.get('checkpoint_url'):
        return 'checkpoint_required'
    if body.get('challenge'):
        return 'challenge_required'
    if any(isinstance(message, str) and 'please wait' in message.casefold() for message in messages):
        return 'please_wait'
    return ''


def endpoint_family(url):
    # Inspect only the path to choose a static label. Do not echo arbitrary URLs.
    try:
        path = urlsplit(url).path.rstrip('/')
    except (TypeError, ValueError):
        return 'Instagram request'
    if path.endswith('/api/graphql'):
        return 'web GraphQL'
    if path.endswith('/graphql/query'):
        return 'legacy GraphQL'
    if path.startswith('/api/'):
        return 'Instagram API'
    return 'Instagram web page'


def rejection_detail(code, reason, phase, endpoint):
    detail = f'Instagram returned HTTP {code} during {phase} ({endpoint}).'
    if reason:
        detail += f' Signal: {reason}.'
    if reason == 'challenge_required':
        detail += ' Instagram requested account verification. Check Instagram directly; this app cannot complete that challenge.'
    elif reason == 'checkpoint_required':
        detail += ' Instagram requested an account security checkpoint. Check Instagram directly; this app cannot complete that checkpoint.'
    elif reason == 'feedback_required':
        detail += ' Instagram refused this app request. This signal alone does not identify the cause; browser access does not prove this request is supported.'
    elif reason == 'login_required':
        detail += ' Instagram did not accept the session for this request.'
    elif code == 400:
        detail += ' The request was rejected. An API compatibility change or Instagram restriction is possible; the response does not establish which.'
    if code == 429 or reason == 'please_wait':
        detail += ' Instagram requested a pause. No automatic retry will be made.'
    return detail

class RequestGuard:
    def __init__(self, cancel):
        self.cancel, self.stopped = cancel, None
        self.stop_detail = ''
        self.phase = 'connection'
        self.patch = None
        self.acquired = False
        guard = self
        class Controller(instaloader.RateController):
            def sleep(self, seconds):
                if guard.cancel.wait(seconds):
                    raise Stop('cancelled')
            def wait_before_query(self, query_type):
                guard.check()
                super().wait_before_query(query_type)
                guard.check()
            def handle_429(self, query_type):
                guard.stopped = 'blocked'
                raise Stop('blocked', 'Instagram reported rate limiting.')
        self.rate_controller = Controller

    def check(self):
        if self.cancel.is_set():
            raise Stop('cancelled')
        if self.stopped:
            raise Stop(self.stopped, self.stop_detail)

    def __enter__(self):
        self.check()
        if not _OPERATION_LOCK.acquire(blocking=False):
            raise Stop('error', 'Another connection operation is still finishing. Try again when it stops.')
        self.acquired = True
        original = requests.Session.send
        guard = self
        def guarded(session, request, **kwargs):
            guard.check()
            if kwargs.get('timeout') is None:
                kwargs['timeout'] = 30
            response = original(session, request, **kwargs)
            code = response.status_code
            reason = response_reason(response)
            status = {400: 'error', 429: 'blocked', 401: 'authentication', 403: 'denied'}.get(code)
            if reason in ('challenge_required', 'checkpoint_required', 'feedback_required', 'please_wait'):
                status = 'blocked'
            elif reason == 'login_required':
                status = 'authentication'
            elif reason in ('execution error', 'invalid doc id'):
                status = 'error'
            if status:
                guard.stopped = status
                guard.stop_detail = rejection_detail(code, reason, guard.phase, endpoint_family(request.url))
                response.close()
                raise Stop(status, guard.stop_detail)
            guard.check()
            return response
        try:
            self.patch = patch.object(requests.Session, 'send', guarded)
            self.patch.start()
            self.check()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        try:
            if self.patch:
                self.patch.stop()
                self.patch = None
        finally:
            if self.acquired:
                _OPERATION_LOCK.release()
                self.acquired = False

class InstagramSource:
    def __init__(self, settings, cancel):
        self.settings, self.cancel = settings, cancel
        self.loader = None
        self.guard = RequestGuard(cancel)
        self.metadata_routes = False
        self.diagnostics = {'instaloader_version': instaloader.__version__,
                            'metadata_routing_backport': False}

    @property
    def stopped(self):
        return self.guard.stopped

    def check(self):
        self.guard.check()

    def set_phase(self, phase):
        self.guard.phase = phase if phase in _PHASES else 'connection'

    @contextmanager
    def phase_context(self, phase):
        previous = self.guard.phase
        self.set_phase(phase)
        try:
            yield
        finally:
            self.set_phase(previous)

    def __enter__(self):
        self.guard.__enter__()
        try:
            self.check()
            self.loader = instaloader.Instaloader(quiet=True, max_connection_attempts=1,
                request_timeout=30, fatal_status_codes=[429, 401, 403],
                rate_controller=self.guard.rate_controller, download_pictures=False,
                download_videos=False, save_metadata=False)
            self.metadata_routes = install_metadata_routes(self.loader.context, self.phase_context)
            self.diagnostics['metadata_routing_backport'] = self.metadata_routes
            if self.settings.session_data is not None:
                self.check()
                self.loader.load_session(self.settings.login_user, self.settings.session_data)
            elif self.settings.session_file:
                self.check()
                self.loader.load_session_from_file(self.settings.login_user, self.settings.session_file)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def posts(self, account):
        self.check()
        self.set_phase('profile lookup')
        profile = instaloader.Profile.from_username(self.loader.context, account)
        self.check()
        self.set_phase('post listing')
        return profile.get_posts()

    def __exit__(self, *args):
        try:
            if self.loader:
                self.loader.close()
        finally:
            self.guard.__exit__(*args)
