"""Synthetic-only unit tests. Network is prohibited even if a test regresses."""
import csv
import json
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from core import Collector, Settings, Stop, StorageError, atomic_write, classify, filtered, save_csv, username


def comment(cid, author='synthetic_author', text='SYNTHETIC comment', answers=()):
    return NS(id=cid, owner=NS(username=author), text=text,
              created_at_utc=datetime(2024, 1, 2, 12, 30), answers=iter(answers))


def post(pid, comments):
    return NS(mediaid=pid, shortcode=f'SYNTHETIC_{pid}', owner_username='synthetic_owner', get_comments=lambda: iter(comments))


class Source:
    def __init__(self, posts=(), error=None):
        self.items, self.error, self.calls, self.entered = posts, error, [], False
    def __enter__(self):
        self.entered = True
        return self
    def __exit__(self, *args):
        pass
    def posts(self, account):
        self.calls.append(account)
        if self.error:
            raise self.error
        return iter(self.items)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='comment-finder-SYNTHETIC-')
        self.addCleanup(self.temp.cleanup)
        self.network = patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK PROHIBITED'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.event, self.events = threading.Event(), []

    def collector(self, source, **kwargs):
        return Collector(Settings('synthetic_author', 'synthetic_owner', **kwargs).validate(), source,
                         self.event, lambda k,v:self.events.append((k,v)), self.temp.name)

    def test_reply_under_nonmatching_parent_and_timestamp(self):
        reply = comment(2, 'SYNTHETIC_AUTHOR', text='SYNTHETIC long\nreply, with unicode ☀')
        reply.created_at_utc = datetime(2024, 1, 2, 14, 30, tzinfo=timezone(timedelta(hours=2)))
        c = self.collector(Source([post(1,[comment(1, 'someone_else', answers=[reply])])]))
        self.assertEqual(c.run(),'finished')
        self.assertEqual((c.parents,c.replies,len(c.rows)), (1,1,1))
        row = c.rows[0]
        self.assertEqual(row['kind'],'reply')
        self.assertEqual(row['parent_comment_id'],'1')
        self.assertEqual(row['timestamp_utc'],'2024-01-02T12:30:00+00:00')
        self.assertEqual(row['post_url'],'https://www.instagram.com/p/SYNTHETIC_1/')
        self.assertEqual(row['post_owner'],'synthetic_owner')
        progress = [v for k,v in self.events if k=='progress']
        self.assertEqual(progress[-1]['matches'],1)
        self.assertEqual(progress[-1]['replies'],1)
        self.assertTrue(all('percent' not in v for v in progress))

    def test_duplicate_comments_and_posts_are_not_repeated(self):
        p = post(1,[comment(1),comment(1)])
        c = self.collector(Source([p,p]))
        self.assertEqual(c.run(),'gaps')
        self.assertEqual(len(c.rows),1)
        self.assertTrue(any(r['detail']=='Duplicate post returned.' for r in c.coverage))

    def test_own_account_scope_is_optional_and_deduplicated(self):
        source=Source()
        c=self.collector(source,own_posts=True)
        c.run()
        self.assertEqual(source.calls,['synthetic_owner','synthetic_author'])
        self.assertEqual(Settings('same','same',True).validate().accounts,['same'])
        self.assertEqual(Settings('a','b').validate().accounts,['b'])

    def test_global_limit_prevents_reply_fetch_and_later_account(self):
        class Parent:
            id=1; owner=NS(username='synthetic_author'); text='SYNTHETIC'; created_at_utc=datetime(2024,1,1)
            @property
            def answers(self):
                raise AssertionError('Replies must not be fetched after cap')
        source=Source([post(1,[Parent()])])
        c=self.collector(source,max_total=1,own_posts=True)
        self.assertEqual(c.run(),'limits')
        self.assertEqual(c.examined,1)
        self.assertEqual(source.calls,['synthetic_owner'])
        self.assertTrue(any(r['status']=='global_capped' for r in c.coverage))

    def test_per_scope_caps_are_conservative(self):
        c=self.collector(Source([post(1,[comment(1,answers=[comment(2),comment(3)])]),post(2,[])]),
                         max_posts=1,max_comments=1,max_replies=1)
        self.assertEqual(c.run(),'limits')
        self.assertEqual(c.examined,2)
        capped={r['scope'] for r in c.coverage if r['status']=='capped'}
        self.assertEqual(capped,{'posts','parent_comments','replies'})

    def test_cancel_preserves_checkpoint_and_prevents_next_item(self):
        def parents():
            yield comment(1)
            self.event.set()
            yield comment(2)
            raise AssertionError('Must not advance after cancellation')
        c=self.collector(Source([post(1,parents())]),own_posts=True)
        # Trigger cancel from a saved-result notification: no additional iterator pull.
        def emit(k,v):
            self.events.append((k,v))
            if k=='match': self.event.set()
        c.emit=emit
        self.assertEqual(c.run(),'cancelled')
        self.assertEqual(len(c.rows),1)
        with (c.folder/'matching_comments.csv').open() as f:
            self.assertEqual(len(list(csv.DictReader(f))),1)
        self.assertEqual(json.loads((c.folder/'status.json').read_text())['status'],'cancelled')

    def test_cancel_before_start_never_enters_source(self):
        self.event.set()
        source=Source()
        c=self.collector(source)
        self.assertEqual(c.run(),'cancelled')
        self.assertFalse(source.entered)

    def test_failure_stops_later_accounts_and_preserves_match(self):
        def parents():
            yield comment(1)
            raise Stop('blocked','SYNTHETIC HTTP 429')
        source=Source([post(1,parents())])
        c=self.collector(source,own_posts=True)
        self.assertEqual(c.run(),'blocked')
        self.assertEqual(len(c.rows),1)
        self.assertEqual(source.calls,['synthetic_owner'])
        self.assertEqual(c.coverage[-1]['status'],'blocked')

    def test_missing_fields_report_gap_while_other_comments_survive(self):
        broken=comment(1)
        broken.text=None
        c=self.collector(Source([post(1,[broken,comment(2)])]))
        self.assertEqual(c.run(),'gaps')
        self.assertEqual([r['comment_id'] for r in c.rows],['2'])
        self.assertTrue(any(r['status']=='skipped' for r in c.coverage))

    def test_empty_result_message_does_not_claim_never_commented(self):
        c=self.collector(Source())
        self.assertEqual(c.run(),'finished')
        message=self.events[-1][1]['message']
        self.assertIn('No matches found in the material checked',message)
        self.assertIn('does not mean',message)

    def test_separate_folders_and_unfinished_status_during_collection(self):
        c=self.collector(Source())
        def posts(account):
            self.assertEqual(json.loads((c.folder/'status.json').read_text())['status'],'unfinished')
            return iter([])
        c.source.posts=posts
        c.run()
        second=self.collector(Source())
        second.run()
        self.assertNotEqual(c.folder,second.folder)
        self.assertTrue((c.folder/'coverage.csv').is_file())

    def test_atomic_write_failure_preserves_previous_checkpoint(self):
        path=Path(self.temp.name)/'checkpoint.txt'
        path.write_text('previous checkpoint')
        with patch('core.os.replace',side_effect=OSError('SYNTHETIC disk failure')):
            with self.assertRaises(StorageError): atomic_write(path,lambda f:f.write('new data'))
        self.assertEqual(path.read_text(),'previous checkpoint')
        self.assertEqual(list(Path(self.temp.name).glob('.checkpoint-*')),[])

    def test_write_failure_reported_and_no_source_requests(self):
        source=Source()
        c=self.collector(source)
        with patch('core.save_csv',side_effect=StorageError('SYNTHETIC disk full')):
            self.assertEqual(c.run(),'write_error')
        self.assertEqual(source.calls,[])
        self.assertEqual(self.events[-1][1]['status'],'write_error')

    def test_csv_formula_mitigation_and_multiline_roundtrip(self):
        values=['=1+1',' +SUM(A1:A2)','-1+2','@SUM(A1)', '\tformula', '\nformula','SYNTHETIC "quoted",\nline ☀']
        path=Path(self.temp.name)/'export.csv'
        save_csv([{'text':v} for v in values],path,['text'])
        with path.open(newline='') as f: saved=[row['text'] for row in csv.DictReader(f)]
        self.assertEqual(saved[:-1],["'"+v for v in values[:-1]])
        self.assertEqual(saved[-1],values[-1])

    def test_filtering_combines_query_and_kind(self):
        rows=[{'kind':'reply','text':'SYNTHETIC Sunshine','author_username':'test'},
              {'kind':'comment','text':'SYNTHETIC sunshine','author_username':'test'}]
        self.assertEqual(filtered(rows,'SUNSHINE','reply'),[rows[0]])
        self.assertEqual(filtered(rows,'missing'),[])
        self.assertEqual(filtered(rows),rows)

    def test_validation(self):
        self.assertEqual(username(' @Synthetic.Name_1 '),'synthetic.name_1')
        for value in ('','@@name','https://example.com/name','name space','a'*31):
            with self.subTest(value=value), self.assertRaises(ValueError): username(value)
        for value in ('0','-1','1.5','bad','1000001'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings('a','b',max_posts=value).validate()
        with self.assertRaises(ValueError): Settings('a','b',login_user='a').validate()
        with self.assertRaises(ValueError): Settings('a','b',login_user='a',session_file='/missing/SYNTHETIC').validate()

    def test_actual_post_owner_is_preserved(self):
        item=post(1,[comment(1)])
        item.owner_username='synthetic_actual_owner'
        c=self.collector(Source([item]))
        self.assertEqual(c.run(),'finished')
        self.assertEqual(c.rows[0]['post_owner'],'synthetic_actual_owner')

    def test_requests_connection_error_is_not_a_storage_failure(self):
        import requests
        c=self.collector(Source(error=requests.ConnectionError('SYNTHETIC connection failed')))
        self.assertEqual(c.run(),'error')
        self.assertEqual(self.events[-1][1]['status'],'error')
        self.assertTrue(any(r['status']=='error' and r['scope']=='posts' for r in c.coverage))

    def test_managed_session_tokens_are_not_written_to_collector_outputs(self):
        token='SYNTHETIC-SECRET-TOKEN-NOT-FOR-RESULTS'
        c=self.collector(Source(),login_user='synthetic_login',
                         session_data={'sessionid':token,'csrftoken':'SYNTHETIC-CSRF'})
        self.assertEqual(c.run(),'finished')
        for path in c.folder.iterdir():
            self.assertNotIn(token,path.read_text(),str(path))
            self.assertNotIn('SYNTHETIC-CSRF',path.read_text(),str(path))
        self.assertNotIn(token,repr(self.events))

    def test_error_categories(self):
        cases=[('LoginRequiredException','session needed','authentication'),
               ('PrivateProfileNotFollowedException','private','denied'),
               ('TooManyRequestsException','rate limited','blocked'),
               ('RuntimeError','HTTP 429','blocked'),('TimeoutError','timeout','error')]
        for name,message,expected in cases:
            with self.subTest(name=name):
                self.assertEqual(classify(type(name,(Exception,),{})(message)),expected)
        for status in ('authentication','denied','blocked','error'):
            c=self.collector(Source(error=Stop(status,'SYNTHETIC failure')))
            self.assertEqual(c.run(),status)
            self.assertEqual(self.events[-1][1]['status'],status)

if __name__=='__main__': unittest.main()
