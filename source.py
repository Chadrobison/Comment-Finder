"""Documented Instaloader APIs with a shared transport stop guard.

Login and collection are exclusive: the scoped Requests guard also protects
redirects and temporary sessions created by Instaloader. No credentials are discovered.
"""
import threading
from unittest.mock import patch
import instaloader
import requests
from core import Stop

_OPERATION_LOCK = threading.Lock()

class RequestGuard:
    def __init__(self, cancel):
        self.cancel, self.stopped = cancel, None
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
            raise Stop(self.stopped)

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
            status = {429: 'blocked', 401: 'authentication', 403: 'denied'}.get(code)
            if not status and code == 400:
                try:
                    message = str(response.json().get('message', ''))
                    if any(s in message for s in ('challenge_required', 'checkpoint_required', 'feedback_required')):
                        status = 'blocked'
                except (ValueError, AttributeError):
                    pass
            if status:
                guard.stopped = status
                response.close()
                raise Stop(status, f'Instagram returned HTTP {code}.')
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

    @property
    def stopped(self):
        return self.guard.stopped

    def check(self):
        self.guard.check()

    def __enter__(self):
        self.guard.__enter__()
        try:
            self.check()
            self.loader = instaloader.Instaloader(quiet=True, max_connection_attempts=1,
                request_timeout=30, fatal_status_codes=[429, 401, 403],
                rate_controller=self.guard.rate_controller, download_pictures=False,
                download_videos=False, save_metadata=False)
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
        profile = instaloader.Profile.from_username(self.loader.context, account)
        self.check()
        return profile.get_posts()

    def __exit__(self, *args):
        try:
            if self.loader:
                self.loader.close()
        finally:
            self.guard.__exit__(*args)
