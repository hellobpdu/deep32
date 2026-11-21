import os
import unittest
from support import Case, TOKEN, PUBLIC
from deep32net import store, tokens
from deep32net.errors import Exists, StateError, NetError
from deep32net.files import atomic_write

class TestTokensStore(Case):
    def row(self, **kwargs):
        args = dict(kind='forge',side='joiner',peer_id='forge-synthetic',peer_role='forge',peer_pin='11'*32,own_pin='22'*32,transport='tailnet',paired_at=1700000000,peer_address='100.64.0.101',peer_dns_name='deep32-forge.tail0000.ts.net')
        args.update(kwargs)
        return store.record(**args)

    def test_token_create_refuse_overwrite_rotate_and_public(self):
        token = self.root/'token'
        tokens.write_secret(token,TOKEN)
        self.assertEqual(tokens.read_token(token),TOKEN)
        with self.assertRaises(Exists):
            tokens.write_secret(token,TOKEN)
        changed = tokens.make_token()
        self.assertNotEqual(changed,TOKEN)
        tokens.write_secret(token,changed,overwrite=True)
        self.assertEqual(tokens.read_token(token),changed)
        for raw in [b'x',b'A'*64,b'0'*63,b'0'*64+b'\r\n']:
            atomic_write(token,raw)
            with self.assertRaises(StateError):
                tokens.read_token(token)
        public = self.root/'public.pem'
        atomic_write(public,PUBLIC.encode())
        self.assertEqual(tokens.read_kb_public(public),PUBLIC)
        atomic_write(public,PUBLIC.replace('\n','\r\n').encode())
        with self.assertRaises(StateError):
            tokens.read_kb_public(public)

    def test_record_commit_and_received_cleanup(self):
        state = self.root/'state'
        from deep32net.files import state_dir
        state_dir(state)
        h = dict(kind='forge',forge_token=TOKEN,kb_public_key=PUBLIC,publish='serve-https')
        names = store.save_received(state,'forge','forge-synthetic',h)
        obj = self.row(received=names)
        store.save_record(state,obj)
        self.assertEqual(store.list_peers(state),[obj])
        text = store.record_path(state,'forge','joiner','forge-synthetic').read_text()
        self.assertNotIn(TOKEN,text)
        self.assertNotIn(PUBLIC,text)
        self.assertEqual(list(obj),store.RECORD_KEYS)
        with self.assertRaises(Exists):
            store.save_record(state,obj)
        store.remove_peer(state,'forge','joiner','forge-synthetic')
        self.assertEqual(store.list_peers(state),[])
        self.assertFalse(store.received_dir(state,'forge','forge-synthetic').exists())
        with self.assertRaises(NetError) as caught:
            store.remove_peer(state,'forge','joiner','forge-synthetic')
        self.assertEqual(caught.exception.reason,'not-paired')

    def test_hosts_preserve_outside_and_remove_empty(self):
        path = self.root/'hosts'
        outside = b'127.0.0.1 localhost\r\n# existing\n'
        atomic_write(path,outside,mode=0o640)
        mode = path.stat().st_mode
        store.update_hosts_file(path,[self.row()])
        self.assertEqual(path.read_bytes(),outside+store.render_hosts_block([self.row()]).encode())
        store.update_hosts_file(path,[])
        self.assertEqual(path.read_bytes(),outside)
        self.assertEqual(path.stat().st_mode,mode)
        self.assertEqual(store.hosts_entries([self.row(transport='loopback')]),[])
        self.assertEqual(store.hosts_entries([self.row(side='joined')]),[])
        self.assertEqual(len(store.hosts_entries([self.row(),self.row(peer_id='forge-second')])),2)

    def test_hosts_malformed_markers_untouched(self):
        path = self.root/'hosts'
        for raw in [store.BEGIN,store.BEGIN+store.BEGIN+store.END,store.END+store.BEGIN]:
            atomic_write(path,raw)
            with self.assertRaises(StateError):
                store.update_hosts_file(path,[self.row()])
            self.assertEqual(path.read_bytes(),raw)

    def test_edge_lines_sorted_and_empty(self):
        path = self.root/'edge-env.txt'
        atomic_write(path,b'# keep\r\nOTHER=value\nRAG_MCP_TOKENS=old\nTAIL=yes')
        tokens.update_edge_env(path,{'b':'22'*32,'a':'11'*32})
        self.assertEqual(path.read_bytes(),b'# keep\r\nOTHER=value\nRAG_MCP_TOKENS='+('11'*32+','+'22'*32).encode()+b'\nTAIL=yes')
        tokens.update_edge_env(path,{})
        self.assertIn(b'RAG_MCP_TOKENS=\n',path.read_bytes())
        path.unlink()
        tokens.update_edge_env(path,{})
        self.assertEqual(path.read_bytes(),b'RAG_MCP_TOKENS=\n')

    @unittest.skipUnless(os.name == 'posix','POSIX-only symlink and private modes')
    def test_symlinks_and_private_modes(self):
        target = self.root/'target'
        tokens.write_secret(target,TOKEN)
        self.assertEqual(target.stat().st_mode & 0o777,0o600)
        linked = self.root/'linked'
        linked.symlink_to(target)
        for action in [lambda:tokens.read_token(linked),lambda:tokens.write_secret(linked,TOKEN,True)]:
            with self.assertRaises(StateError):
                action()
