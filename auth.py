"""Optional in-app login and app-owned JSON sessions.

Only documented Instaloader login/two_factor_login/save_session interfaces are
used. Passwords and verification codes are never persisted. Managed sessions
are JSON cookie dictionaries, never pickle files, and are not discovered from
browsers or other applications. Call login outside the GUI thread.
"""
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

import instaloader

from core import Stop, StorageError, atomic_write, username as normalize_username

SESSION_DIRECTORY = Path.home() / 'Library' / 'Application Support' / 'Comment Finder'
MAX_SESSION_BYTES = 1024 * 1024


class AuthError(Exception):
    """A fixed, user-safe authentication or session-storage message."""


@dataclass(frozen=True)
class SessionCredentials:
    username: str
    data: dict = field(repr=False)


def _validated(credentials):
    try:
        user = normalize_username(credentials.username)
        data = credentials.data
        if (not isinstance(data, dict) or not data
                or any(not isinstance(k, str) or not isinstance(v, str) for k, v in data.items())
                or any(k.casefold() in {'password', 'passwd', 'enc_password', 'verificationcode'} for k in data)
                or not data.get('csrftoken') or not data.get('sessionid')):
            raise ValueError()
        result = SessionCredentials(user, dict(data))
        if len(json.dumps(result.data).encode('utf-8')) > MAX_SESSION_BYTES // 2:
            raise ValueError()
        return result
    except (AttributeError, TypeError, ValueError):
        raise AuthError('The session data is invalid. Please log in again.') from None


class SessionStore:
    """Store only this application's explicitly remembered login.

    An optional directory is useful for offline tests; production uses the
    fixed Application Support directory. Loading is explicit, never discovery.
    """
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else SESSION_DIRECTORY
        self.path = self.directory / 'session.json'

    def _check_directory(self, create=False):
        if create:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink():
            raise AuthError('The saved-session folder is not a regular app folder. Login was not saved.')
        if self.directory.exists():
            info = self.directory.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise AuthError('The saved-session folder is not owned by your user account.')
            os.chmod(self.directory, 0o700)

    def save(self, credentials):
        credentials = _validated(credentials)
        try:
            self._check_directory(create=True)
            payload = {'version': 1, 'username': credentials.username, 'cookies': credentials.data}
            # NamedTemporaryFile creates mode 0600. os.replace atomically
            # installs that private file, so secrets never have public mode.
            atomic_write(self.path, lambda stream: json.dump(payload, stream))
        except (OSError, StorageError, TypeError, ValueError):
            raise AuthError('Could not save the login on this Mac. Check the app session folder permissions and available disk space.') from None

    def load(self):
        try:
            self._check_directory()
            if not self.path.exists() and not self.path.is_symlink():
                return None
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(descriptor, 'r', encoding='utf-8') as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > MAX_SESSION_BYTES):
                    raise AuthError('The saved login is not a private app-owned session file. Remove it and log in again.')
                payload = json.load(stream)
            if (not isinstance(payload, dict) or set(payload) != {'version', 'username', 'cookies'}
                    or payload['version'] != 1):
                raise AuthError('The saved login has an unsupported format. Remove it and log in again.')
            return _validated(SessionCredentials(payload['username'], payload['cookies']))
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            raise AuthError('Could not read the saved login. Remove it and log in again.') from None

    def clear(self):
        """Delete the local remembered token; does not revoke it at Instagram."""
        try:
            self._check_directory()
            self.path.unlink(missing_ok=True)
        except OSError:
            raise AuthError('Could not remove the saved login from this Mac. Check the app session folder permissions.') from None


def _check_cancel(cancel):
    if cancel.is_set():
        raise Stop('cancelled')


def _safe_error(error, two_factor=False):
    if isinstance(error, instaloader.BadCredentialsException):
        return ('Instagram did not accept the verification code. Start login again with a fresh code.'
                if two_factor else 'Instagram did not accept the username or password. Check them and try again.')
    if isinstance(error, Stop):
        return {
            'blocked': 'Instagram blocked or rate-limited this login. No automatic retry was made. Try again later.',
            'denied': 'Instagram denied this login request. No automatic retry was made.',
            'authentication': 'Instagram did not accept this login. Check your account details and try again.',
        }.get(error.status, 'Login could not be completed. No automatic retry was made.')
    # Match known upstream signals, but never expose its response text or URLs.
    detail = str(error).casefold()
    if any(word in detail for word in ('checkpoint', 'challenge', 'feedback_required')):
        return 'Instagram requires an account security check. Open Instagram yourself to review your account; this app cannot bypass that check.'
    if any(word in detail for word in ('429', 'rate limit', 'please wait', 'blocked')):
        return 'Instagram blocked or rate-limited this login. No automatic retry was made. Try again later.'
    return 'Login could not be completed. Check your connection and account details. Instagram may restrict login from this app.'


def login(username, password, cancel, request_code, remember=False, store=None):
    """Login once; request one 2FA code via a GUI-coordinated callback if needed.

    request_code() returns a code string, or None when cancelled. The caller
    must unblock that callback on Cancel/window-close. No verification request
    is made after cancellation. A successful result is in-memory unless
    remember=True, in which case only its JSON cookie data is persisted.
    """
    try:
        user = normalize_username(username)
    except ValueError:
        raise AuthError('Enter your Instagram login username, not an email address or profile link.') from None
    if not isinstance(password, str) or not password:
        raise AuthError('Enter your Instagram password.')
    _check_cancel(cancel)
    loader = None
    two_factor = False
    try:
        # Import lazily so source and auth remain independently importable.
        from source import RequestGuard
        with RequestGuard(cancel) as guard:
            loader = instaloader.Instaloader(quiet=True, max_connection_attempts=1,
                request_timeout=30, fatal_status_codes=[429, 401, 403],
                rate_controller=guard.rate_controller, download_pictures=False, download_videos=False, save_metadata=False)
            _check_cancel(cancel)
            try:
                loader.login(user, password)
            except instaloader.TwoFactorAuthRequiredException:
                two_factor = True
                password = None
                _check_cancel(cancel)
                code = request_code()
                _check_cancel(cancel)
                if code is None:
                    raise Stop('cancelled')
                if not isinstance(code, str) or not code.strip():
                    raise AuthError('Enter the verification code from your authenticator or Instagram message.')
                try:
                    loader.two_factor_login(code.strip())
                finally:
                    code = None
            finally:
                password = None
            _check_cancel(cancel)
            credentials = _validated(SessionCredentials(user, loader.save_session()))
            _check_cancel(cancel)
            if remember:
                (store if store is not None else SessionStore()).save(credentials)
            return credentials
    except AuthError:
        raise
    except Stop as error:
        if error.status == 'cancelled':
            raise Stop('cancelled') from None
        raise AuthError(_safe_error(error, two_factor)) from None
    except Exception as error:
        raise AuthError(_safe_error(error, two_factor)) from None
    finally:
        password = None
        if loader is not None:
            try:
                loader.close()
            except Exception:
                pass
