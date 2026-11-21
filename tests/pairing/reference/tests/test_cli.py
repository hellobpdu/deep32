import json
import re
import subprocess
import sys
import time
from support import Case, SHIP, CODE, TOKEN, PUBLIC
from deep32net.codes import is_canonical
from deep32net.cli import HELPER_REQUIRED
from deep32net import store

class TestCLI(Case):
    def setUp(self):
        super().setUp()
        self.outputs = []
        self.secret_values = [CODE,TOKEN]

    def secret(self,value):
        if value:
            self.secret_values.append(value)
            for output in self.outputs:
                self.assertNotIn(value,output)

    def command(self, args, helper=False):
        return [sys.executable,'-I','-B',str(SHIP/'tests/guarded_run.py'),str(SHIP/'bin'/('pairing-helper' if helper else 'deep32-pair')),*args]

    def clean(self, output):
        self.outputs.append(output)
        for value in self.secret_values:
            self.assertNotIn(value,output)
        self.assertNotIn('PRIVATE KEY',output)
        self.assertNotIn('https://login.tailscale.com/a/0000000000',output)
        for value in re.findall(r'"(?:forge_token|mcp_token)":"([0-9a-f]{64})"',output):
            self.fail('token in output')

    def run_cli(self, args, request=None, helper=False, expected=0):
        if request and isinstance(request.get('code'),str):
            self.secret(request['code'])
        p = subprocess.run(self.command(args,helper),input=json.dumps(request) if request is not None else '',text=True,capture_output=True,timeout=20)
        self.clean(p.stdout+p.stderr)
        self.assertEqual(p.returncode,expected,p.stderr+p.stdout)
        obj = json.loads(p.stdout)
        self.assertEqual(obj['exit'],expected)
        self.assertEqual(obj['ok'],expected == 0)
        self.assertEqual(obj['reason'] is None,expected == 0)
        if p.stderr:
            self.assertEqual(p.stderr,('pairing-helper' if helper else 'deep32-pair')+': '+obj['reason']+'\n')
        return obj

    def pair_cli(self, kind='forge', helper=False, model_endpoints=None):
        self.assertIsNotNone(self.openssl,'openssl not found')
        token,public = self.files()
        ready = self.root/'ready.json'
        req = dict(code=CODE,expires_seconds=300,attempts=1,transport='loopback',publish='serve-https',state_dir=str(self.root/'a'))
        if kind == 'forge':
            req.update(token_path=str(token),kb_public_path=str(public))
        if helper:
            req['form'] = 'iso'
            self.assertEqual(set(req),HELPER_REQUIRED)
            verb = 'pair-forge'
        else:
            req['kind'] = kind
            verb = 'serve'
        req.update(port=0,ready_file=str(ready),openssl=self.openssl)
        if model_endpoints is not None:
            req['model_endpoints'] = model_endpoints
        process = subprocess.Popen(self.command([verb],helper),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def cleanup():
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
        self.addCleanup(cleanup)
        process.stdin.write(json.dumps(req))
        process.stdin.close()
        process.stdin = None
        deadline = time.monotonic()+10
        while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.01)
        self.assertTrue(ready.exists())
        port = json.loads(ready.read_bytes())['port']
        result = self.run_cli(['join'],dict(kind=kind,code=CODE,transport='loopback',state_dir=str(self.root/'b'),port=port,openssl=self.openssl))
        stdout,stderr = process.communicate(timeout=20)
        self.clean(stdout+stderr)
        self.assertEqual(process.returncode,0,stderr+stdout)
        self.assertEqual(json.loads(stdout)['ok'],True)
        return result,token

    def test_serve_forge_join_and_unpair_rotate(self):
        result,token = self.pair_cli()
        old = token.read_text().strip()
        self.secret(old)
        b = store.list_peers(self.root/'b')[0]
        a = store.list_peers(self.root/'a')[0]
        out = self.run_cli(['unpair','--state-dir',str(self.root/'a'),'--kind','forge','--side','joined','--peer-id',a['peer_id'],'--token-path',str(token)])
        self.assertTrue(out['rotated'])
        self.assertEqual(out['restart_required'],['door'])
        new = token.read_text().strip()
        self.secret(new)
        self.assertNotEqual(new,old)
        self.assertRegex(new,r'\A[0-9a-f]{64}\Z')
        self.run_cli(['unpair','--state-dir',str(self.root/'b'),'--kind','forge','--side','joiner','--peer-id',b['peer_id']])
        self.assertFalse(store.received_dir(self.root/'b','forge',b['peer_id']).exists())
        self.assertEqual(store.list_peers(self.root/'a'),[])

    def test_serve_gateway_join_write_and_unpair(self):
        result,_ = self.pair_cli('gateway')
        edge = self.root/'edge-env.txt'
        self.assertEqual(self.run_cli(['write-edge-tokens','--state-dir',str(self.root/'a'),'--env-file',str(edge)])['count'],1)
        token = next(iter(store.mcp_tokens(self.root/'a').values()))
        self.secret(token)
        a = store.list_peers(self.root/'a')[0]
        self.run_cli(['unpair','--state-dir',str(self.root/'a'),'--kind','gateway','--side','joined','--peer-id',a['peer_id'],'--edge-env',str(edge)])
        self.assertEqual(edge.read_bytes(),b'RAG_MCP_TOKENS=\n')
        self.assertEqual(store.mcp_tokens(self.root/'a'),{})

    def test_helper_exact_nine_keys_plus_optional_pair(self):
        self.pair_cli(helper=True)

    def test_helper_endpoint_handover_is_saved_after_commit(self):
        endpoints = {'embeddings': 'https://forge.synthetic.ts.net:18443',
                     'reranker': 'https://forge.synthetic.ts.net:19443'}
        result, _ = self.pair_cli(helper=True, model_endpoints=endpoints)
        received = store.received_dir(self.root/'b', 'forge', result['peer_id'])
        self.assertEqual(json.loads((received/'model-endpoints.json').read_bytes()), endpoints)

    def test_helper_four_bad_requests(self):
        token,public = self.files()
        base = dict(code=CODE,expires_seconds=300,attempts=1,form='iso',transport='loopback',publish='serve-https',token_path=str(token),kb_public_path=str(public),state_dir=str(self.root/'a'))
        requests = [dict(base,expires_seconds=299),dict(base,attempts=2),dict(base,unknown=True),{k:v for k,v in base.items() if k != 'token_path'},
                    dict(base, model_endpoints={'embeddings': 'http://forge.synthetic.ts.net:18443',
                                                'reranker': 'https://forge.synthetic.ts.net:19443'})]
        for req in requests:
            obj = self.run_cli(['pair-forge'],req,True,2)
            self.assertEqual(obj['reason'],'bad-request')
        self.assertFalse((self.root/'a').exists())

    def test_host_signin_not_provided(self):
        self.assertEqual(self.run_cli(['host-signin'],helper=True,expected=64)['reason'],'not-provided')

    def test_identity_make_token_peers_hosts_and_conflicts(self):
        obj = self.run_cli(['identity','--state-dir',str(self.root/'state'),'--node','forge','--openssl',self.openssl])
        self.assertTrue(obj['created'])
        token = self.root/'engine-token'
        self.run_cli(['make-token','--path',str(token)])
        self.run_cli(['make-token','--path',str(token)],expected=7)
        old = token.read_text()
        self.secret(old.strip())
        self.run_cli(['make-token','--path',str(token),'--rotate'])
        self.assertNotEqual(token.read_text(),old)
        self.secret(token.read_text().strip())
        self.assertEqual(self.run_cli(['peers','--state-dir',str(self.root/'state')])['peers'],[])
        self.assertEqual(self.run_cli(['hosts','--state-dir',str(self.root/'state'),'--hosts-file',str(self.root/'hosts')])['entries'],0)
        self.run_cli(['unpair','--state-dir',str(self.root/'state'),'--kind','forge','--side','joined','--peer-id','absent','--token-path',str(token)],expected=7)

    def test_discovery_status_errors_no_authurl(self):
        for fixture,exit in [('status-mixed.json',0),('status-needslogin.json',9),('status-malformed.json',9),('status-notobject.json',9)]:
            self.run_cli(['discover','--want','forge','--transport','tailnet','--status-json',str(SHIP/'tests/fixtures'/fixture),'--no-probe'],expected=exit)
        self.assertEqual(self.run_cli(['discover','--want','relay','--transport','loopback','--no-probe'])['backend_state'],None)

    def test_bad_json_exact_sets_and_bad_code(self):
        for text in ['[]','{} trailing','{"kind":NaN}','{"kind":"forge","kind":"gateway"}','{}'*40000]:
            p = subprocess.run(self.command(['join']),input=text,text=True,capture_output=True,timeout=10)
            self.clean(p.stdout+p.stderr)
            self.assertEqual(p.returncode,2)
        self.run_cli(['join'],dict(kind='forge',code='invalid!',transport='loopback',state_dir=str(self.root/'absent')),expected=8)
        self.assertFalse((self.root/'absent').exists())
        self.run_cli(['join'],dict(kind='forge',code=CODE,transport='tailnet',state_dir=str(self.root/'absent')),expected=2)
        self.run_cli(['serve'],dict(kind='gateway',code=CODE.lower(),transport='loopback',publish='serve-https',state_dir=str(self.root/'absent'),expires_seconds=300,attempts=1),expected=8)

    def test_join_local_state_reasons(self):
        self.assertIsNotNone(self.openssl,'openssl not found')
        self.run_cli(['identity','--state-dir',str(self.root/'s'),'--node','forge','--openssl',self.openssl])
        req = dict(kind='forge',code=CODE,transport='loopback',state_dir=str(self.root/'s'),port=1,openssl=self.openssl)
        self.assertEqual(self.run_cli(['join'],req,expected=6)['reason'],'identity-kind-mismatch')
        self.run_cli(['identity','--state-dir',str(self.root/'w'),'--node','workstation','--openssl',self.openssl])
        from deep32net.window import PairingWindow
        with PairingWindow(self.root/'w','forge-joiner'):
            self.assertEqual(self.run_cli(['join'],dict(req,state_dir=str(self.root/'w')),expected=6)['reason'],'busy')

    def test_help_version_and_new_code(self):
        for helper,name in [(False,'deep32-pair'),(True,'pairing-helper')]:
            for option in ('--help','--version'):
                p = subprocess.run(self.command([option],helper),capture_output=True,text=True,timeout=10)
                self.assertEqual(p.returncode,0)
                self.clean(p.stdout+p.stderr)
                if option == '--version':
                    self.assertEqual(p.stdout,name+' 0.1.0\n')
        p = subprocess.run(self.command(['new-code']),capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,0)
        self.assertTrue(is_canonical(p.stdout.rstrip('\n')))
