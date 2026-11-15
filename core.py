"""Bounded collection adapted from the supplied instagram_account_comments.py."""
import csv
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

FIELDS = ['author_username', 'post_owner', 'post_id', 'post_shortcode', 'post_url',
          'comment_id', 'parent_comment_id', 'kind', 'timestamp_unix', 'timestamp_utc', 'text']
COVERAGE_FIELDS = ['account', 'scope', 'item_id', 'status', 'examined', 'detail']
LIMITATION = 'Only the material checked is represented. Deleted, hidden, inaccessible or unreturned comments may be missing. This is not a complete history.'
MESSAGES = {
    'finished': 'Bounded search finished.',
    'limits': 'A collection limit was reached. Additional material was not checked.',
    'gaps': 'Search finished with skipped material. Read the coverage report.',
    'cancelled': 'Search cancelled. Collected results are preserved.',
    'authentication': 'Authentication is required or the session expired. You may supply your own trusted session in Advanced Settings.',
    'denied': 'Access is denied. The account or posts may be private or unavailable to your session.',
    'blocked': 'Instagram blocked or rate-limited requests. Collection stopped; no automatic retry will be made.',
    'error': 'A network or other error occurred. Collection stopped; results may be partial.',
    'write_error': 'Could not save results. Last successful checkpoints remain; newer results may exist only in this window.',
}

class StorageError(Exception):
    pass

class Stop(Exception):
    def __init__(self, status, detail=''):
        self.status, self.detail = status, detail
        super().__init__(detail or status)

def username(value):
    value = value.strip().removeprefix('@').casefold()
    if not re.fullmatch(r'[a-z0-9._]{1,30}', value):
        raise ValueError('Enter a username (1–30 letters, numbers, periods or underscores), not a link. One leading @ is optional.')
    return value

@dataclass
class Settings:
    author: str
    owner: str
    own_posts: bool = False
    max_posts: int = 100
    max_comments: int = 500
    max_replies: int = 100
    max_total: int = 10000
    login_user: str = ''
    session_file: str = ''

    def validate(self):
        self.author, self.owner = username(self.author), username(self.owner)
        for name in ('max_posts', 'max_comments', 'max_replies', 'max_total'):
            try:
                value = int(getattr(self, name))
                if not 1 <= value <= 1000000:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError('Each collection limit must be a whole number between 1 and 1,000,000.') from None
            setattr(self, name, value)
        if bool(self.login_user.strip()) != bool(self.session_file.strip()):
            raise ValueError('For a session, provide both its login username and the session file, or leave both empty.')
        if self.login_user.strip():
            self.login_user = username(self.login_user)
            if not Path(self.session_file).is_file():
                raise ValueError('The selected session file does not exist. Choose a file you own and trust.')
        return self

    @property
    def accounts(self):
        return list(dict.fromkeys([self.owner] + ([self.author] if self.own_posts else [])))

def safe_cell(value):
    if isinstance(value, str) and (value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n'))):
        return "'" + value
    return value

def atomic_write(path, write):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', newline='', encoding='utf-8', dir=path.parent,
                                         prefix='.checkpoint-', delete=False) as stream:
            temporary = stream.name
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except (OSError, csv.Error) as error:
        raise StorageError(str(error)) from error
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass

def save_csv(rows, path, fields=FIELDS):
    def write(stream):
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: safe_cell(row.get(key, '')) for key in fields} for row in rows)
    atomic_write(path, write)

def filtered(rows, query='', kind='All'):
    query = query.casefold().strip()
    return [row for row in rows if (kind == 'All' or row['kind'] == kind)
            and query in ' '.join(str(v) for v in row.values()).casefold()]

def classify(error):
    if isinstance(error, Stop):
        return error.status
    name, text = type(error).__name__, str(error).casefold()
    if 'LoginRequired' in name or '401' in text or 'login_required' in text or 'logged out' in text or 'redirected to login' in text:
        return 'authentication'
    if '429' in text or name in ('TooManyRequestsException', 'AbortDownloadException') or any(s in text for s in ('challenge_required', 'checkpoint_required', 'feedback_required', 'please wait', 'rate limit')):
        return 'blocked'
    if '403' in text or 'Forbidden' in name or 'PrivateProfile' in name:
        return 'denied'
    return 'error'

class Collector:
    def __init__(self, settings, source, cancel, emit, root):
        self.settings, self.source, self.cancel, self.emit = settings, source, cancel, emit
        self.folder = Path(root) / (datetime.now().strftime('%Y-%m-%d_%H-%M-%S') + '_' + uuid4().hex[:8])
        self.rows, self.coverage, self.seen = [], [], set()
        self.parents = self.replies = 0
        self.account = self.post = ''

    @property
    def examined(self):
        return self.parents + self.replies

    def check(self):
        if self.cancel.is_set():
            raise Stop('cancelled')

    def progress(self):
        self.emit('progress', dict(account=self.account, post=self.post, parents=self.parents,
                                   replies=self.replies, matches=len(self.rows)))

    def record(self, scope, item, status, count=0, detail=''):
        self.coverage.append(dict(account=self.account, scope=scope, item_id=item, status=status, examined=count, detail=detail))
        save_csv(self.coverage, self.folder / 'coverage.csv', COVERAGE_FIELDS)

    def state(self, status, detail=''):
        data = dict(status=status, detail=detail, examined=self.examined, matches=len(self.rows),
                    limitation=LIMITATION, author=self.settings.author, accounts=self.settings.accounts,
                    limits={k: getattr(self.settings, k) for k in ('max_posts','max_comments','max_replies','max_total')})
        atomic_write(self.folder / 'status.json', lambda f: json.dump(data, f, indent=2))

    def walk(self, factory, limit, scope, item, visit):
        counter = [0]
        try:
            self._walk(factory, limit, scope, item, visit, counter)
        except StorageError:
            raise
        except Exception as error:
            self.record(scope, item, classify(error), counter[0], detail='Interrupted scope; ' + str(error)[:500])
            raise

    def _walk(self, factory, limit, scope, item, visit, counter):
        self.check()
        if self.examined >= self.settings.max_total:
            self.record(scope, item, 'global_capped', detail='Whole-search comment/reply limit reached.')
            return
        iterator = iter(factory())
        count = 0
        while count < limit:
            self.check()
            if self.examined >= self.settings.max_total:
                self.record(scope, item, 'global_capped', count, 'Whole-search comment/reply limit reached.')
                return
            try:
                value = next(iterator)
            except StopIteration:
                self.record(scope, item, 'iterator_exhausted', count, 'Iterator ended; not proof of complete historical coverage.')
                return
            count += 1
            counter[0] = count
            visit(value)
        self.record(scope, item, 'capped', count, 'Limit reached; additional items were not checked.')

    def match(self, comment, post_id, shortcode, parent='', post_owner=None):
        if parent:
            self.replies += 1
        else:
            self.parents += 1
        try:
            cid = comment.id
            author = comment.owner.username
            if cid is None or not isinstance(author, str):
                raise ValueError('Missing comment ID or author')
            cid = str(cid)
            if author.casefold() != self.settings.author or (post_id, cid) in self.seen:
                return
            moment = comment.created_at_utc
            if not isinstance(moment, datetime):
                raise ValueError('Missing comment date')
            moment = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)
            text = comment.text
            if not isinstance(text, str):
                raise ValueError('Missing comment text')
            row = dict(author_username=author, post_owner=post_owner or self.account, post_id=post_id,
                       post_shortcode=shortcode, post_url=f'https://www.instagram.com/p/{shortcode}/',
                       comment_id=cid, parent_comment_id=parent, kind='reply' if parent else 'comment',
                       timestamp_unix=int(moment.timestamp()), timestamp_utc=moment.isoformat(), text=text)
        except (AttributeError, TypeError, ValueError) as error:
            self.record('comment', '', 'skipped', 1, str(error))
            return
        finally:
            self.progress()
        self.seen.add((post_id, cid))
        self.rows.append(row)
        self.emit('match', row.copy())
        self.progress()
        save_csv(self.rows, self.folder / 'matching_comments.csv')

    def collect_post(self, post):
        self.check()
        try:
            if post.mediaid is None or not re.fullmatch(r'[A-Za-z0-9_-]+', post.shortcode):
                raise ValueError('Missing or invalid post ID/shortcode')
            pid, shortcode = str(post.mediaid), post.shortcode
            owner = post.owner_username
            if not isinstance(owner, str) or not owner:
                raise ValueError('Missing post owner')
        except (AttributeError, TypeError, ValueError) as error:
            self.record('post', '', 'skipped', detail=str(error))
            return
        if pid in self.post_seen:
            self.record('post', pid, 'skipped', detail='Duplicate post returned.')
            return
        self.post_seen.add(pid)
        self.post = shortcode
        self.progress()
        def parent(comment):
            self.match(comment, pid, shortcode, post_owner=owner)
            try:
                if comment.id is None:
                    raise ValueError('Missing parent ID')
                parent_id = str(comment.id)
            except (AttributeError, ValueError) as error:
                self.record('replies', pid, 'skipped', detail=str(error))
                return
            self.walk(lambda: comment.answers, self.settings.max_replies, 'replies', parent_id,
                      lambda reply: self.match(reply, pid, shortcode, parent_id, owner))
        self.walk(post.get_comments, self.settings.max_comments, 'parent_comments', pid, parent)

    def run(self):
        status, detail = 'finished', ''
        try:
            try:
                self.folder.mkdir(parents=True, exist_ok=False)
            except OSError as error:
                raise StorageError(str(error)) from error
            self.emit('folder', str(self.folder))
            self.state('unfinished', 'Search started; active scopes may not have final coverage records.')
            save_csv([], self.folder / 'matching_comments.csv')
            self.record('run', '', 'started')
            self.check()
            with self.source:
                for account in self.settings.accounts:
                    self.check()
                    self.account, self.post, self.post_seen = account, '', set()
                    self.progress()
                    self.walk(lambda: self.source.posts(account), self.settings.max_posts, 'posts', account, self.collect_post)
            self.check()
            if any(r['status'].endswith('capped') for r in self.coverage):
                status = 'limits'
            elif any(r['status'] == 'skipped' for r in self.coverage):
                status = 'gaps'
        except StorageError as error:
            status, detail = 'write_error', str(error)
        except Exception as error:
            status, detail = classify(error), str(error)[:600]
        try:
            self.record('run', self.post, status, self.examined, detail or MESSAGES[status])
            self.state(status, detail)
        except StorageError as error:
            status, detail = 'write_error', str(error)
        message = MESSAGES[status]
        if not self.rows:
            message += ' No matches found in the material checked; this does not mean the account has never commented.'
        self.emit('done', dict(status=status, message=message, detail=detail, folder=str(self.folder)))
        return status
