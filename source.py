"""Documented Instaloader collection API, with a transport stop guard.

The app allows one worker. The scoped Session.send patch also protects redirects
and temporary sessions created inside Instaloader. No credentials are discovered.
"""
from unittest.mock import patch
import instaloader
import requests
from core import Stop

class InstagramSource:
    def __init__(self, settings, cancel):
        self.settings, self.cancel = settings, cancel
        self.loader = self.guard = None
        self.stopped = None

    def check(self):
        if self.cancel.is_set():
            raise Stop('cancelled')
        if self.stopped:
            raise Stop(self.stopped)

    def __enter__(self):
        source = self
        original = requests.Session.send
        def guarded(session, request, **kwargs):
            source.check()
            response = original(session, request, **kwargs)
            # Inspect every response, including redirects, before any subsequent send.
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
                source.stopped = status
                response.close()
                raise Stop(status, f'Instagram returned HTTP {code}.')
            source.check()
            return response
        class Controller(instaloader.RateController):
            def sleep(self, seconds):
                if source.cancel.wait(seconds):
                    raise Stop('cancelled')
            def wait_before_query(self, query_type):
                source.check()
                super().wait_before_query(query_type)
                source.check()
            def handle_429(self, query_type):
                source.stopped = 'blocked'
                raise Stop('blocked', 'Instagram reported rate limiting.')
        self.guard = patch.object(requests.Session, 'send', guarded)
        self.guard.start()
        try:
            self.check()
            self.loader = instaloader.Instaloader(quiet=True, max_connection_attempts=1,
                request_timeout=30, fatal_status_codes=[429, 401, 403],
                rate_controller=Controller, download_pictures=False,
                download_videos=False, save_metadata=False)
            if self.settings.session_file:
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
            if self.guard:
                self.guard.stop()
