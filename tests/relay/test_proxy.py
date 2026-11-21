"""Strict active-turn MCP boundary with observable upstream effect counts."""
import json
import threading
import unittest
from support import OfflineCase, Dispatch, rpc
from deep32_relay.proxy import Gateway, ProxyError, TOOLS, MAX_REQUEST, MAX_RESPONSE


class Proxy(OfflineCase):
    def test_unverified_or_revoked_capability_has_zero_effects(self):
        cap=self.gateway.grant('synthetic',self.clock()+170)
        with self.assertRaises(ProxyError):self.gateway.call(cap,rpc('initialize'))
        self.gateway.activate(cap)
        self.gateway.revoke('synthetic')
        with self.assertRaises(ProxyError):self.gateway.call(cap,rpc('initialize'))
        self.assertEqual(self.dispatch.calls,[])

    def test_all_eleven_tools_and_four_methods_are_bound(self):
        cap=self.active_cap(); self.initialise(cap)
        for index,name in enumerate(sorted(TOOLS),3):
            self.gateway.call(cap,rpc('tools/call',ident=index,name=name,arguments={'synthetic':True}))
        self.assertEqual(len(self.dispatch.calls),14)
        for session,envelope,deadline in self.dispatch.calls:
            self.assertEqual(session,cap.session)
            self.assertEqual(deadline,cap.deadline)
            self.assertEqual(set(envelope)-{'protocol_version'},{'body','mcp_session_id'})
        self.assertTrue(cap.tools_verified)

    def test_tools_require_initialize_and_exact_schema_set(self):
        cap=self.active_cap()
        for body in (rpc('tools/list'),rpc('tools/call',name='rag_status',arguments={})):
            with self.assertRaises(ProxyError):self.gateway.call(cap,body)
        self.assertEqual(self.dispatch.calls,[])
        self.gateway.call(cap,rpc('initialize'))
        with self.assertRaises(ProxyError):self.gateway.call(cap,rpc('tools/call',name='rag_status'))
        self.assertEqual(len(self.dispatch.calls),1)

    def test_forbidden_methods_tools_servers_batches_and_ids_have_zero_effects(self):
        cap=self.active_cap(); self.initialise(cap)
        invalid=[[],[rpc('tools/call',name='rag_status')],{},True,
            rpc('resources/read'),rpc('prompts/get'),rpc('sampling/createMessage'),
            rpc('roots/list'),rpc('elicitation/create'),rpc('admin/reload'),
            rpc('tools/call',name='shell'),rpc('tools/call',name='WebFetch'),
            rpc('tools/call',name='mcp__other__rag_status'),
            rpc('tools/call',name=['rag_status']),rpc('tools/call',name={'a':'rag_status'}),
            rpc('tools/call',name='rag_status',arguments=[]),
            rpc('tools/call',name='rag_status',server='other'),
            rpc(['initialize']),rpc({'method':'initialize'}),
            rpc('initialize',ident=True),rpc('initialize',ident=1.5),
            rpc('initialize',ident='x'*129),rpc('initialize',ident=2**63),
            dict(jsonrpc='2.0',method='notifications/initialized',id=1),
            dict(jsonrpc='2.0',method='initialize',id=1,params=[]),
            dict(jsonrpc='2.0',method='initialize',id=1,extra=True)]
        baseline=len(self.dispatch.calls)
        for body in invalid:
            with self.subTest(kind=type(body).__name__),self.assertRaises(ProxyError):
                self.gateway.call(cap,body)
        self.assertEqual(len(self.dispatch.calls),baseline)

    def test_request_encoded_envelope_limit_precedes_upstream(self):
        cap=self.active_cap(); self.initialise(cap)
        baseline=len(self.dispatch.calls)
        for text in ('x'*MAX_REQUEST,'"'*MAX_REQUEST,'\ud800'):
            with self.subTest(kind=len(text)),self.assertRaises(ProxyError):
                self.gateway.call(cap,rpc('tools/call',name='rag_status',arguments={'text':text}))
        self.assertEqual(len(self.dispatch.calls),baseline)

    def test_deadline_refuses_new_calls_and_late_responses(self):
        cap=self.active_cap()
        def response(*args):
            self.clock.advance(170)
            return Dispatch()(*args)
        self.dispatch.hook=response
        with self.assertRaises(ProxyError):self.gateway.call(cap,rpc('initialize'))
        self.assertFalse(cap.initialised)
        self.assertEqual(cap.inflight,0)
        with self.assertRaises(ProxyError):self.gateway.call(cap,rpc('initialize'))
        self.assertEqual(len(self.dispatch.calls),1)

    def test_revoke_during_dispatch_discards_result(self):
        cap=self.active_cap()
        def response(*args):
            self.gateway.revoke(cap.session)
            return Dispatch()(*args)
        self.dispatch.hook=response
        with self.assertRaises(ProxyError):self.gateway.call(cap,rpc('initialize'))
        self.assertFalse(cap.initialised)
        self.assertEqual(cap.inflight,0)
        self.assertEqual(cap.bearer,'')

    def test_revoke_before_guarded_enqueue_has_zero_upstream_effects(self):
        entered,release=threading.Event(),threading.Event()
        effects=[];revocations=[];failures=[]
        def guarded(session,envelope,deadline,guard):
            entered.set()
            if not release.wait(5):raise AssertionError('bounded test wait exceeded')
            guard()
            effects.append(session)
            return Dispatch()(session,envelope,deadline)
        gateway=Gateway(self.dispatch,clock=self.clock,guarded_dispatch=guarded,
                        on_revoke=revocations.append)
        cap=gateway.grant('synthetic-guarded',self.clock()+170);gateway.activate(cap)
        def worker():
            try:gateway.call(cap,rpc('initialize'))
            except ProxyError:failures.append('inactive-turn')
        thread=threading.Thread(target=worker)
        try:
            thread.start();self.assertTrue(entered.wait(5))
            gateway.revoke(cap.session)
        finally:
            release.set();thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(effects,[])
        self.assertEqual(self.dispatch.calls,[])
        self.assertEqual(revocations,[cap.session])
        self.assertEqual(failures,['inactive-turn'])
        self.assertEqual(cap.inflight,0)

    def test_arbitrary_upstream_exception_becomes_fixed_reason(self):
        cap=self.active_cap()
        def fail(*_):raise RuntimeError('SYNTHETIC-SECRET-peer-exception')
        self.dispatch.hook=fail
        with self.assertRaises(ProxyError) as caught:self.gateway.call(cap,rpc('initialize'))
        self.assertNotIn('SYNTHETIC',str(caught.exception))
        self.assertEqual(cap.inflight,0)

    def test_response_binding_and_wrapper_validation(self):
        cap=self.active_cap()
        for mutation in (
            lambda r:r.update(http_status=True),lambda r:r.update(http_status=302),
            lambda r:r.update(extra=True),lambda r:r.update(mcp_session_id='bad\nvalue'),
            lambda r:r['body'].update(id=True),lambda r:r['body'].update(id=2),
            lambda r:r['body'].update(error={'message':'synthetic'}),
            lambda r:r['body']['result'].update(capabilities={'roots':{}}),
            lambda r:r['body']['result'].update(protocolVersion=[])):
            def response(*args):
                result=Dispatch()(*args); mutation(result); return result
            self.dispatch.hook=response
            with self.subTest(mutation=mutation),self.assertRaises(ProxyError):self.gateway.call(cap,rpc('initialize'))
            self.assertFalse(cap.initialised)
            self.assertEqual(cap.inflight,0)

    def test_incomplete_duplicate_or_invalid_tools_fail_before_tool_effect(self):
        for mode in ('missing','duplicate','foreign','schema','unhashable-name'):
            with self.subTest(mode=mode):
                cap=self.active_cap('synthetic-'+mode)
                self.gateway.call(cap,rpc('initialize'))
                def response(*args):
                    result=Dispatch()(*args); tools=result['body']['result']['tools']
                    if mode=='missing':tools.pop()
                    elif mode=='duplicate':tools[0]=tools[1]
                    elif mode=='foreign':tools[0]['name']='shell'
                    elif mode=='schema':tools[0]['inputSchema']={'type':'string'}
                    else:tools[0]['name']=['rag_status']
                    return result
                self.dispatch.hook=response
                with self.assertRaises(ProxyError):self.gateway.call(cap,rpc('tools/list'))
                baseline=len(self.dispatch.calls)
                with self.assertRaises(ProxyError):self.gateway.call(cap,rpc('tools/call',name='rag_status'))
                self.assertEqual(len(self.dispatch.calls),baseline)
                self.dispatch.hook=None

    def test_negotiated_mcp_session_id_cannot_change(self):
        cap=self.active_cap()
        self.gateway.call(cap,rpc('initialize'))
        def changed(*args):
            result=Dispatch()(*args);result['mcp_session_id']='different-synthetic-session';return result
        self.dispatch.hook=changed
        with self.assertRaises(ProxyError):self.gateway.call(cap,rpc('tools/list'))
        self.assertEqual(cap.mcp_session_id,'synthetic-mcp-session')
        self.assertFalse(cap.tools_verified)

    def test_http_endpoint_bearer_and_strict_json_before_upstream(self):
        cap=self.active_cap()
        body=json.dumps(rpc('initialize')).encode()
        options=dict(method='POST',path='/mcp',host='127.0.0.1:7452',authorisation='Bearer '+cap.bearer,
                     content_type='application/json',body=body)
        for field,value in (('method','GET'),('path','https://synthetic.invalid/mcp'),('path','/mcp?x=y'),
                            ('host','0.0.0.0:7452'),('host','127.0.0.1:7452@evil'),('host','127.0.0.1:0'),
                            ('authorisation','Bearer wrong'),('content_type','text/plain')):
            bad=dict(options); bad[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ProxyError):self.gateway.http_request(cap,**bad)
        for payload in (b'[]',b'{"jsonrpc":"2.0","jsonrpc":"2.0"}',b'\xef\xbb\xbf{}',b'{"x":NaN}',
                        b'{"x":"\\ud800"}',b'['*34+b']'*34,b'\xff',body+b' {}'):
            bad=dict(options,body=payload)
            with self.subTest(payload=payload[:30]),self.assertRaises(ProxyError):self.gateway.http_request(cap,**bad)
        self.assertEqual(self.dispatch.calls,[])
        self.gateway.http_request(cap,**options)
        self.assertEqual(len(self.dispatch.calls),1)

    def test_four_inflight_bound_refuses_fifth_before_effect(self):
        cap=self.active_cap(); self.initialise(cap)
        release=threading.Event(); entered=threading.Condition(); count=[0]; failures=[]
        def response(*args):
            with entered:count[0]+=1; entered.notify_all()
            if not release.wait(5):raise AssertionError('bounded test wait exceeded')
            return Dispatch()(*args)
        self.dispatch.hook=response
        def worker(index):
            try:self.gateway.call(cap,rpc('tools/call',ident=index,name='rag_status'))
            except Exception as error:failures.append(type(error).__name__)
        threads=[threading.Thread(target=worker,args=(index,)) for index in range(4)]
        try:
            for thread in threads:thread.start()
            with entered:self.assertTrue(entered.wait_for(lambda:count[0]==4,timeout=5))
            with self.assertRaises(ProxyError):self.gateway.call(cap,rpc('tools/call',ident=99,name='rag_status'))
            self.assertEqual(count[0],4)
        finally:
            release.set()
            for thread in threads:thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures,[])
        self.assertEqual(cap.inflight,0)

    def test_repeated_call_quota_is_bounded(self):
        cap=self.active_cap(); self.initialise(cap)
        while cap.calls<300:self.gateway.call(cap,rpc('tools/call',name='rag_status'))
        baseline=len(self.dispatch.calls)
        with self.assertRaises(ProxyError):self.gateway.call(cap,rpc('tools/call',name='rag_status'))
        self.assertEqual(len(self.dispatch.calls),baseline)


if __name__=='__main__':
    unittest.main()
