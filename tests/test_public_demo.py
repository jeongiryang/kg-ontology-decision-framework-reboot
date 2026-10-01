import asyncio
from dataclasses import replace
import json
import os
import time
import threading
import unittest
from unittest.mock import patch

from starlette.responses import JSONResponse, Response
from academic_assistant.public_demo import DemoLimits, PublicDemo, create_demo_app


async def backend(scope, receive, send):
    if scope['path'] == '/readyz':
        return await JSONResponse({'status': 'ready'})(scope, receive, send)
    if scope['method'] == 'POST':
        await receive()
    return await Response(b'OK', headers={'Set-Cookie': 'unwanted=1'})(scope, receive, send)


def request_scope(path='/', method='GET', headers=None, query=b'', raw=None):
    values = [(b'host', b'127.0.0.1:8765')]
    if method == 'POST':
        values.append((b'content-type', b'application/json'))
    if headers:
        values.extend(headers)
    return {'type': 'http', 'path': path, 'raw_path': raw or path.encode(),
            'method': method, 'query_string': query, 'headers': values,
            'http_version': '1.1', 'scheme': 'http', 'server': ('127.0.0.1', 8765)}


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, app=None, *, data=b'{}', events=None, **kwargs):
        app = app or PublicDemo(backend)
        sent = []
        pending = list(events or [{'type': 'http.request', 'body': data}])
        async def receive():
            return pending.pop(0) if pending else {'type': 'http.disconnect'}
        async def send(event):
            sent.append(event)
        await app(request_scope(**kwargs), receive, send)
        status = next((event['status'] for event in sent if event['type'] == 'http.response.start'), None)
        body = b''.join(event.get('body', b'') for event in sent)
        return status, body, sent

    async def test_demo_flags_are_anonymous_and_stateless(self):
        status, data, _ = await self.call(path='/v1/academic/demo')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {'public': True, 'authentication': False,
            'transcript_storage': 'request_memory', 'provider': 'cloudflare_quick_tunnel'})

    async def test_private_and_unknown_routes_are_closed(self):
        for path in ['/docs', '/redoc', '/openapi.json', '/v1/academic/feedback', '/.local/prototype.json', '/v1/models', '/metrics']:
            self.assertEqual((await self.call(path=path))[0], 404)
        self.assertEqual((await self.call(path='/v1/academic/feedback', method='POST'))[0], 404)

    async def test_needed_routes_are_allowed(self):
        for path in ['/', '/assets/app.js', '/assets/transcript.js', '/assets/evidence.css',
                     '/v1/academic/runtime', '/v1/academic/evidence/cwnu.test/preview.png']:
            self.assertEqual((await self.call(path=path))[0], 200)
        for path in ['/v1/academic/chat', '/v1/academic/answers', '/v1/academic/transcripts/assess', '/v1/academic/transcripts/chat']:
            self.assertEqual((await self.call(path=path, method='POST'))[0], 200)

    async def test_ready_is_a_profile_specific_verified_marker(self):
        status, data, _ = await self.call(path='/readyz')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {'status': 'ready', 'public_demo': True})
        async def wrong(scope, receive, send):
            await JSONResponse({'status': 'ready', 'extra': True})(scope, receive, send)
        self.assertEqual((await self.call(PublicDemo(wrong), path='/readyz'))[0], 503)

    async def test_aliases_encoded_paths_and_other_methods_rejected(self):
        self.assertEqual((await self.call(path='/', raw=b'/%2e%2e/'))[0], 404)
        self.assertEqual((await self.call(path='/assets/app.js/'))[0], 404)
        for method in ['OPTIONS', 'PUT', 'DELETE', 'TRACE']:
            self.assertEqual((await self.call(method=method))[0], 404)

    async def test_query_allowlist_and_duplicates(self):
        path = '/v1/academic/evidence/cwnu.test/preview'
        self.assertEqual((await self.call(path=path, query=b'evidence_index=0&pdf_page=43'))[0], 200)
        for query in [b'evidence_index=0&evidence_index=1', b'path=secret', b'pdf_page=-1', b'pdf_page=', b'pdf_page', b'pdf_page=1&token=x']:
            self.assertIn((await self.call(path=path, query=query))[0], [400, 422])
        self.assertEqual((await self.call(query=b'question=personal'))[0], 400)

    async def test_cross_origin_posts_rejected_no_login_required(self):
        for header in [(b'origin', b'https://evil.example'), (b'origin', b'null'), (b'sec-fetch-site', b'cross-site')]:
            self.assertEqual((await self.call(path='/v1/academic/chat', method='POST', headers=[header]))[0], 403)
        self.assertEqual((await self.call(path='/v1/academic/chat', method='POST', headers=[(b'origin', b'http://127.0.0.1:8765')]))[0], 200)

    async def test_invalid_hosts_and_duplicate_headers(self):
        for headers in [[(b'host', b'evil.example')], [(b'content-type', b'application/pdf')], [(b'content-length', b'2'), (b'content-length', b'2')]]:
            self.assertEqual((await self.call(path='/v1/academic/chat', method='POST', headers=headers))[0], 400)
        scope = request_scope(); scope['headers'] = [(b'host', b'evil.example')]
        sent=[]
        async def receive(): return {'type':'http.disconnect'}
        async def send(event): sent.append(event)
        await PublicDemo(backend)(scope,receive,send)
        self.assertEqual(sent[0]['status'],400)

    async def test_header_size_bound(self):
        self.assertEqual((await self.call(headers=[(b'x-header', b'x'*8192)]))[0],431)

    async def test_json_content_and_depth_are_fail_closed(self):
        invalid = [b'{"x":1,"x":2}', b'[]', b'{"x":NaN}', ('{}').encode('utf-16'), b'{"x":'+b'['*65+b'0'+b']'*65+b'}', b'not json']
        for data in invalid:
            self.assertEqual((await self.call(path='/v1/academic/chat',method='POST',data=data))[0],422)
        text=json.dumps({'question':'"['*100+'\\\\'}).encode()
        self.assertEqual((await self.call(path='/v1/academic/chat',method='POST',data=text))[0],200)

    async def test_streamed_and_declared_body_bounds(self):
        app=PublicDemo(backend,replace(DemoLimits(),json_bytes=4))
        self.assertEqual((await self.call(app,path='/v1/academic/chat',method='POST',headers=[(b'content-length',b'5')]))[0],413)
        events=[{'type':'http.request','body':b'{"x','more_body':True},{'type':'http.request','body':b'":1}'}]
        self.assertEqual((await self.call(app,path='/v1/academic/chat',method='POST',events=events))[0],413)
        self.assertEqual(app.active,0)

    async def test_content_length_mismatch(self):
        self.assertEqual((await self.call(path='/v1/academic/chat',method='POST',headers=[(b'content-length',b'3')]))[0],400)
        self.assertEqual((await self.call(headers=[(b'content-length',b'1')]))[0],413)

    async def test_receive_absolute_deadline(self):
        app=PublicDemo(backend,replace(DemoLimits(),body_seconds=.01))
        async def slow():
            await asyncio.sleep(.02)
            return {'type':'http.request','body':b'{}','more_body':True}
        sent=[]
        async def send(event):sent.append(event)
        await app(request_scope('/v1/academic/chat','POST'),slow,send)
        self.assertEqual(sent[0]['status'],408)
        self.assertEqual(app.active,0)

    async def test_disconnect_releases_capacity(self):
        app=PublicDemo(backend)
        self.assertIsNone((await self.call(app,path='/v1/academic/chat',method='POST',events=[{'type':'http.disconnect'}]))[0])
        self.assertEqual(app.active,0)

    async def test_response_bound_does_not_leak_partial_body(self):
        app=PublicDemo(backend,replace(DemoLimits(),response_bytes=1))
        status,data,_=await self.call(app)
        self.assertEqual(status,503);self.assertNotIn(b'OK',data)

    async def test_backend_deadline_and_crash_sanitized(self):
        async def slow(scope,receive,send):await asyncio.sleep(.03)
        async def crash(scope,receive,send):raise RuntimeError('PRIVATE PATH SECRET')
        for function in [slow,crash]:
            app=PublicDemo(function,replace(DemoLimits(),work_seconds=.01))
            status,data,_=await self.call(app)
            self.assertEqual(status,503);self.assertNotIn(b'SECRET',data)
            if function is slow:
                self.assertEqual(app.active,1)
                for _ in range(30):
                    if not app.active:break
                    await asyncio.sleep(.01)
            self.assertEqual(app.active,0)

    async def test_real_middleware_sync_deadline_retains_capacity_until_done(self):
        from fastapi import FastAPI
        started=threading.Event();release=threading.Event()
        api=FastAPI()
        @api.middleware('http')
        async def actual_middleware(request,call_next):
            return await call_next(request)
        @api.post('/v1/academic/chat')
        def slow_sync():
            started.set();release.wait(timeout=1)
            return {'answer':'never publish late response'}
        app=PublicDemo(api,replace(DemoLimits(),work_seconds=.1,work_concurrent=1))
        before=time.monotonic()
        status,data,_=await self.call(app,path='/v1/academic/chat',method='POST')
        self.assertTrue(started.is_set())
        self.assertEqual(status,503);self.assertLess(time.monotonic()-before,.3)
        self.assertNotIn(b'late response',data);self.assertEqual(app.work_active,1)
        self.assertEqual((await self.call(app,path='/v1/academic/chat',method='POST'))[0],429)
        release.set()
        for _ in range(20):
            if not app.work_active:break
            await asyncio.sleep(.01)
        self.assertEqual(app.work_active,0)

    async def test_global_post_budget_cannot_use_forwarded_ips_to_reset(self):
        now=[0.0];app=PublicDemo(backend,replace(DemoLimits(),posts_per_minute=1),clock=lambda:now[0])
        self.assertEqual((await self.call(app,path='/v1/academic/chat',method='POST'))[0],200)
        status,_,sent=await self.call(app,path='/v1/academic/chat',method='POST',headers=[(b'x-forwarded-for',b'other')])
        self.assertEqual(status,429)
        self.assertIn((b'retry-after',b'60'),sent[0]['headers'])
        now[0]=60.0
        self.assertEqual((await self.call(app,path='/v1/academic/chat',method='POST'))[0],200)

    async def test_extract_budget_and_pdf_media_type(self):
        app=PublicDemo(backend,replace(DemoLimits(),extracts_per_minute=1))
        scope=request_scope('/v1/academic/transcripts/extract','POST');scope['headers']=[(b'host',b'127.0.0.1:8765'),(b'content-type',b'application/pdf')]
        async def receive():return {'type':'http.request','body':b'%PDF-synthetic'}
        for expected in [200,429]:
            sent=[]
            async def send(event):sent.append(event)
            await app(scope,receive,send);self.assertEqual(sent[0]['status'],expected)

    async def test_global_work_concurrency(self):
        started=asyncio.Event();release=asyncio.Event()
        async def blocked(scope,receive,send):
            started.set();await release.wait();await backend(scope,receive,send)
        app=PublicDemo(blocked,replace(DemoLimits(),work_concurrent=1))
        task=asyncio.create_task(self.call(app,path='/v1/academic/chat',method='POST'))
        await started.wait()
        self.assertEqual((await self.call(app,path='/v1/academic/chat',method='POST'))[0],429)
        release.set();self.assertEqual((await task)[0],200);self.assertEqual(app.work_active,0)

    async def test_no_response_cookies(self):
        _,_,sent=await self.call()
        self.assertNotIn(b'set-cookie',dict(sent[0]['headers']))

    async def test_explicit_public_profile_required(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(RuntimeError):create_demo_app()

    def test_invalid_budgets(self):
        for key,value in [('body_seconds',float('nan')),('concurrent',True),('response_bytes',0),('posts_per_minute',1.5)]:
            with self.assertRaises(ValueError):replace(DemoLimits(),**{key:value})


if __name__=='__main__':unittest.main()
