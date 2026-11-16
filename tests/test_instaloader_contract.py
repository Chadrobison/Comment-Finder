"""Real installed-library methods with synthetic HTTP responses only.

These tests verify routing and diagnostics, not acceptance by Instagram.
Profile, Post, NodeIterator, Requests preparation, and response parsing are real;
Session.send is replaced and socket connections are prohibited.
"""
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import instaloader
import requests
from core import Collector, Settings, Stop
from source import InstagramSource

PROFILE_DOC='27937681195819736'
POST_DOC='27128499623469141'
TIMELINE_DOC='29104886249145907'
PROFILE={'pk':'1001','username':'synthetic_owner','is_private':False,'media_count':0,
         'friendship_status':{'following':False}}
PAGE='<script type="application/json">'+json.dumps({'__bbox':{'result':{'data':{'xig_user_by_username':PROFILE}}}})+'</script>'
TOKENS='["DTSGInitialData",[],{"token":"SYNTHETIC-DTSG"}], ["LSD",[],{"token":"SYNTHETIC-LSD"}], {"IG_USER_EIMU":"1001"}'
MEDIA={'pk':'2001','code':'SYNTHETICPOST','media_type':1,'taken_at':1700000000,
       'user':{'pk':'1001','username':'synthetic_owner'},'comment_count':0}


def response(request,code=200,body=None,html=None):
    result=requests.Response()
    result.request=request; result.url=request.url
    result.status_code=code; result.reason='OK' if code==200 else 'Bad Request'
    result._content=(html if html is not None else json.dumps(body or {'status':'ok'})).encode()
    result._content_consumed=True
    result.headers['Content-Type']='text/html' if html is not None else 'application/json'
    return result


class InstalledLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='comment-finder-SYNTHETIC-contract-')
        self.addCleanup(self.temp.cleanup)
        for target in ('socket.socket.connect','socket.socket.connect_ex','socket.create_connection'):
            guard=patch(target,side_effect=AssertionError('NETWORK PROHIBITED'))
            guard.start(); self.addCleanup(guard.stop)
        # Suppress random pacing only; request routing and response handling stay real.
        pace=patch.object(instaloader.InstaloaderContext,'do_sleep')
        pace.start(); self.addCleanup(pace.stop)
        self.settings=Settings('synthetic_author','synthetic_owner',login_user='synthetic_login',
                               session_data={'sessionid':'SYNTHETIC-SESSION','csrftoken':'SYNTHETIC-CSRF'}).validate()
        self.cancel=threading.Event()
        self.calls=[]

    def transport(self,session,request,**kwargs):
        params=parse_qs(request.body or '') if request.method=='POST' else {}
        doc=params.get('doc_id',[None])[0]
        variables=json.loads(params.get('variables',['{}'])[0])
        path=urlparse(request.url).path
        self.calls.append({'path':path,'method':request.method,'doc':doc,'variables':variables})
        if request.method=='GET' and path=='/synthetic_owner/':
            return response(request,html=PAGE)
        if request.method=='GET' and path=='/':
            return response(request,html=TOKENS)
        if doc==PROFILE_DOC:
            return response(request,body={'data':{'user':PROFILE}})
        if doc==TIMELINE_DOC:
            return response(request,body={'data':{'xdt_api__v1__feed__user_timeline_graphql_connection':
                {'edges':[],'page_info':{'has_next_page':False,'end_cursor':None}}}})
        if doc==POST_DOC:
            return response(request,body={'data':{'xdt_api__v1__media__shortcode__web_info':{'items':[MEDIA]}}})
        if doc=='SYNTHETIC_UNRELATED_DOC':
            return response(request,body={'status':'ok','data':{'synthetic':True}})
        raise AssertionError('Unexpected synthetic request: '+repr(self.calls[-1]))

    def test_profile_metadata_and_timeline_use_web_api_upfront(self):
        self.assertEqual(instaloader.__version__,'4.16','Re-evaluate the compatibility adapter before upgrading its library contract.')
        with patch.object(requests.Session,'send',autospec=True,side_effect=self.transport):
            with InstagramSource(self.settings,self.cancel) as source:
                self.assertEqual(list(source.posts('synthetic_owner')),[])
        queries=[c for c in self.calls if c['method']=='POST']
        self.assertEqual([c['doc'] for c in queries],[PROFILE_DOC,TIMELINE_DOC])
        self.assertTrue(all(c['path']=='/api/graphql' for c in queries))
        self.assertEqual(queries[0]['variables']['id'],'1001')
        self.assertEqual(queries[0]['variables']['render_surface'],'PROFILE')
        self.assertTrue(queries[0]['variables']['enable_integrity_filters'])
        self.assertEqual(queries[1]['variables']['username'],'synthetic_owner')
        self.assertEqual(queries[1]['variables']['data']['count'],12)
        self.assertEqual(self.calls[0]['path'],'/synthetic_owner/')
        self.assertEqual(sum(c['path']=='/' for c in self.calls),1)

    def test_post_metadata_uses_web_api_and_preserves_variables(self):
        with patch.object(requests.Session,'send',autospec=True,side_effect=self.transport):
            with InstagramSource(self.settings,self.cancel) as source:
                post=instaloader.Post.from_shortcode(source.loader.context,'SYNTHETICPOST')
                self.assertEqual(post.mediaid,2001)
                self.assertEqual(post.owner_username,'synthetic_owner')
        queries=[c for c in self.calls if c['method']=='POST']
        self.assertEqual(len(queries),1)
        self.assertEqual(queries[0]['path'],'/api/graphql')
        self.assertEqual(queries[0]['doc'],POST_DOC)
        self.assertEqual(queries[0]['variables'],{'shortcode':'SYNTHETICPOST',
            '__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider':False})

    def test_unrelated_document_query_keeps_original_route_and_variables(self):
        with patch.object(requests.Session,'send',autospec=True,side_effect=self.transport):
            with InstagramSource(self.settings,self.cancel) as source:
                result=source.loader.context.doc_id_graphql_query('SYNTHETIC_UNRELATED_DOC',{'synthetic':'untouched'})
                self.assertTrue(result['data']['synthetic'])
        self.assertEqual(self.calls,[{'path':'/graphql/query','method':'POST','doc':'SYNTHETIC_UNRELATED_DOC',
                                     'variables':{'synthetic':'untouched'}}])

    def test_challenge_on_metadata_stops_without_fallback_or_second_account(self):
        def refused(session,request,**kwargs):
            result=self.transport(session,request,**kwargs)
            if self.calls[-1]['doc']==PROFILE_DOC:
                return response(request,400,{'status':'fail','message':'feedback_required','secret':'SYNTHETIC-PRIVATE-RESPONSE'})
            return result
        events=[]
        settings=Settings(**dict(vars(self.settings),own_posts=True))
        with patch.object(requests.Session,'send',autospec=True,side_effect=refused):
            collector=Collector(settings,InstagramSource(settings,self.cancel),self.cancel,
                                lambda k,v:events.append((k,v)),self.temp.name)
            self.assertEqual(collector.run(),'blocked')
        self.assertEqual(collector.examined,0)
        self.assertEqual(sum(c['doc']==PROFILE_DOC for c in self.calls),1)
        self.assertFalse(any(c['doc']==TIMELINE_DOC for c in self.calls))
        self.assertFalse(any(c['path']=='/synthetic_author/' for c in self.calls))
        self.assertTrue(any(r['scope']=='posts' and r['status']=='blocked' for r in collector.coverage))
        done=events[-1][1]
        self.assertIn('feedback_required',done['detail'])
        self.assertIn('profile metadata',done['detail'])
        self.assertIn('web GraphQL',done['detail'])
        self.assertIn('No comments or replies were examined',done['message'])
        self.assertNotIn('No matches found',done['message'])
        for path in collector.folder.iterdir():
            self.assertNotIn('SYNTHETIC-PRIVATE-RESPONSE',path.read_text())
            self.assertNotIn('SYNTHETIC-SESSION',path.read_text())

    def test_bootstrap_challenge_is_stopped_before_metadata_send(self):
        def refused(session,request,**kwargs):
            result=self.transport(session,request,**kwargs)
            if self.calls[-1]['path']=='/':
                return response(request,400,{'status':'fail','message':'checkpoint_required'})
            return result
        with patch.object(requests.Session,'send',autospec=True,side_effect=refused):
            with InstagramSource(self.settings,self.cancel) as source:
                with self.assertRaises(Stop) as caught:list(source.posts('synthetic_owner'))
                self.assertEqual(caught.exception.status,'blocked')
                count=len(self.calls)
                with self.assertRaises(Stop):list(source.posts('synthetic_owner'))
                self.assertEqual(len(self.calls),count)
        self.assertFalse(any(c['method']=='POST' for c in self.calls))

    def test_nonempty_library_collection_parses_parent_and_reply_end_to_end(self):
        media=dict(MEDIA,comment_count=3,has_liked=False,like_count=0,
                   user=dict(MEDIA['user'],is_private=False,full_name='SYNTHETIC owner',profile_pic_url='https://example.invalid/SYNTHETIC.jpg'))
        def node(cid,author,text):
            return {'id':cid,'created_at':1700000100,'text':text,
                    'owner':{'id':'3001','username':author},'edge_liked_by':{'count':0}}
        other=node('4001','synthetic_other','SYNTHETIC nonmatching parent')
        other['edge_threaded_comments']={'count':1,'edges':[]}
        matching=node('4002','synthetic_author','SYNTHETIC matching parent')
        matching['edge_threaded_comments']={'count':0,'edges':[]}
        reply=node('4003','synthetic_author','SYNTHETIC matching reply')
        def connection(nodes):
            return {'count':len(nodes),'edges':[{'node':n} for n in nodes],
                    'page_info':{'has_next_page':False,'end_cursor':None}}
        def fixtures(session,request,**kwargs):
            query=parse_qs(urlparse(request.url).query)
            query_hash=query.get('query_hash',[None])[0]
            if query_hash:
                variables=json.loads(query['variables'][0])
                self.calls.append({'path':urlparse(request.url).path,'method':request.method,
                                   'doc':None,'hash':query_hash,'variables':variables})
                if query_hash=='97b41c52301f77ce508f55e66d17620e':
                    self.assertEqual(variables['shortcode'],'SYNTHETICPOST')
                    return response(request,body={'status':'ok','data':{'shortcode_media':
                        {'edge_media_to_parent_comment':connection([other,matching])}}})
                if query_hash=='51fdd02b67508306ad4484ff574a0b62':
                    self.assertEqual(variables['comment_id'],'4001')
                    return response(request,body={'status':'ok','data':{'comment':
                        {'edge_threaded_comments':connection([reply])}}})
                raise AssertionError('Unexpected synthetic comment query')
            result=self.transport(session,request,**kwargs)
            if self.calls[-1]['doc']==TIMELINE_DOC:
                return response(request,body={'data':{'xdt_api__v1__feed__user_timeline_graphql_connection':connection([media])}})
            if self.calls[-1]['doc']==POST_DOC:
                return response(request,body={'data':{'xdt_api__v1__media__shortcode__web_info':{'items':[media]}}})
            return result
        events=[]
        with patch.object(requests.Session,'send',autospec=True,side_effect=fixtures):
            collector=Collector(self.settings,InstagramSource(self.settings,self.cancel),self.cancel,
                                lambda k,v:events.append((k,v)),self.temp.name)
            self.assertEqual(collector.run(),'finished',events[-1])
        self.assertEqual((collector.parents,collector.replies),(2,1))
        self.assertEqual({row['comment_id'] for row in collector.rows},{'4002','4003'})
        reply_row=next(row for row in collector.rows if row['kind']=='reply')
        self.assertEqual(reply_row['parent_comment_id'],'4001')
        self.assertEqual(reply_row['post_owner'],'synthetic_owner')
        self.assertEqual(reply_row['post_url'],'https://www.instagram.com/p/SYNTHETICPOST/')
        self.assertEqual(reply_row['timestamp_unix'],1700000100)
        self.assertTrue(reply_row['timestamp_utc'].endswith('+00:00'))
        self.assertTrue(any(c.get('doc')==POST_DOC for c in self.calls))
        self.assertEqual(sum('hash' in c for c in self.calls),2)
        self.assertTrue((collector.folder/'matching_comments.csv').is_file())

    def test_post_listing_failure_reports_stage_after_metadata_completed(self):
        def refused(session,request,**kwargs):
            result=self.transport(session,request,**kwargs)
            if self.calls[-1]['doc']==TIMELINE_DOC:
                return response(request,400,{'errors':[{'message':'execution error SYNTHETIC-SECRET'}]})
            return result
        events=[]
        with patch.object(requests.Session,'send',autospec=True,side_effect=refused):
            collector=Collector(self.settings,InstagramSource(self.settings,self.cancel),self.cancel,
                                lambda k,v:events.append((k,v)),self.temp.name)
            self.assertEqual(collector.run(),'error')
        self.assertEqual([c['doc'] for c in self.calls if c['method']=='POST'],[PROFILE_DOC,TIMELINE_DOC])
        detail=events[-1][1]['detail']
        self.assertIn('post listing',detail)
        self.assertIn('execution error',detail)
        self.assertNotIn('SYNTHETIC-SECRET',detail)
        self.assertEqual(collector.examined,0)

    def test_generic_400_does_not_guess_private_or_bad_credentials(self):
        def refused(session,request,**kwargs):
            self.calls.append({'path':urlparse(request.url).path})
            return response(request,400,{'status':'fail','message':'SYNTHETIC-UNKNOWN-SERVER-TEXT'})
        events=[]
        with patch.object(requests.Session,'send',autospec=True,side_effect=refused):
            collector=Collector(self.settings,InstagramSource(self.settings,self.cancel),self.cancel,
                                lambda k,v:events.append((k,v)),self.temp.name)
            self.assertEqual(collector.run(),'error')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(collector.examined,0)
        diagnostic=events[-1][1]['detail']
        self.assertIn('400',diagnostic)
        self.assertIn('profile lookup',diagnostic)
        self.assertNotIn('SYNTHETIC-UNKNOWN-SERVER-TEXT',diagnostic)
        self.assertNotIn('password',diagnostic.lower())

if __name__=='__main__':unittest.main()
