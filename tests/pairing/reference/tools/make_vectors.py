#!/usr/bin/env python3
"""Recompute public interoperability fixtures without a network."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deep32net import codes, wire
from deep32net.spake2 import password_scalar, vector, Spake2
from deep32net.p256 import Q

SHIP = Path(__file__).resolve().parents[1]
TEST_CERT_A_B64 = 'MIIBXzCCAQWgAwIBAgIUTPQGbnltD8nVSnHqNmFRwKQR3l0wCgYIKoZIzj0EAwIwFjEUMBIGA1UEAwwLRGVlcDMyIEQzMlUwHhcNMjYxMTE1MDIyNDE0WhcNMzYxMTEyMDIyNDE0WjAWMRQwEgYDVQQDDAtEZWVwMzIgRDMyVTBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABHlctG6i+Y/HNTuRnVC0GIz2nmC/9GriHIxDOBRlkXOdo0TsK4bypga9zFPbpQWwDIFKHY8rQE66dfP4S1M9WIOjMTAvMA4GA1UdDwEB/wQEAwIHgDAdBgNVHQ4EFgQUBoDB7vJpa+O6zjXH1LVc/x2kdQswCgYIKoZIzj0EAwIDSAAwRQIgEW+r/pvds5VIv+/RxXeh6g1cGnRCOcpXGOhN+pLm2eICIQCMJm3IwkaIwGz7YbGOPish45Zk3+U1jsGvMFHvCL9nDQ=='
TEST_CERT_B_B64 = 'MIIBXzCCAQWgAwIBAgIUQCwdMRdT67aIJ6DLJsSlbO5/3bkwCgYIKoZIzj0EAwIwFjEUMBIGA1UEAwwLRGVlcDMyIEQzMlUwHhcNMjYxMTE1MDIyNDE0WhcNMzYxMTEyMDIyNDE0WjAWMRQwEgYDVQQDDAtEZWVwMzIgRDMyVTBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABIrBPqyPL2vEK9UmtIrFqXBNiiakpWZnGrrPgPM+TPnpYyEbSfLAQCgCNceT1WK4bReKcoLOhzVJ1tXXQAqMzMyjMTAvMA4GA1UdDwEB/wQEAwIHgDAdBgNVHQ4EFgQUKhAwVfDvGyerba7GX+u33HktE5EwCgYIKoZIzj0EAwIDSAAwRQIhALUlkY4eoSjEc5LnLHZewMYRXj/W7Q+Fn/9ntFPw4RVkAiANw+Wu88Xo5Rs32okGvoTWJpY8PR0+8ZAxa+ism6ssdA=='
PUBLIC = '-----BEGIN PUBLIC KEY-----\n'+base64.b64encode(bytes.fromhex('302a300506032b6570032100')+bytes([17])*32).decode('ascii')+'\n-----END PUBLIC KEY-----\n'
TOKEN = '0123456789abcdef'*4

def dump(obj):
    return (json.dumps(obj,indent=2,ensure_ascii=True)+'\n').encode('ascii')

def fixed(id, field):
    label = 'D32 pairing code vector '+id+' '+field
    while True:
        value = hashlib.sha256(label.encode('ascii')).digest()
        if field not in ('x','y') or int.from_bytes(value,'big') < Q:
            return value.hex()
        label += ' again'

def negative_fields(base, row, code=True):
    from deep32net.spake2 import normalise_password
    pw = row['peer_password'].encode('ascii') if code else normalise_password(row['peer_password'])
    w = password_scalar(pw,*[bytes.fromhex(row['peer_'+k]) for k in ('relay_pin','node_pin','relay_nonce','node_nonce')])
    a = Spake2('A',bytes.fromhex(base['w']),bytes.fromhex(base['x']))
    b = Spake2('B',w,bytes.fromhex(row['peer_y']))
    try:
        a.finish(bytes.fromhex(base['id_a']),bytes.fromhex(base['id_b']),a.share,b.share)
        b.finish(bytes.fromhex(row['peer_relay_pin']),bytes.fromhex(row['peer_node_pin']),bytes.fromhex(row['b_transcript_pA']),b.share)
        if a.verify(b.own_mac) or b.verify(a.own_mac):
            raise AssertionError('confirmation')
        return dict(peer_w=w.hex(),a_sent_pA=a.share.hex(),peer_pB=b.share.hex(),a_expected_macB=a.peer_mac.hex(),b_sent_macB=b.own_mac.hex(),a_sent_macA=a.own_mac.hex(),b_expected_macA=b.peer_mac.hex())
    finally:
        a.erase()
        b.erase()

def code_vectors():
    base = json.loads((SHIP/'vectors/pairing-v2-vectors.json').read_bytes())
    inputs = ['ABCDEFGH23','abcdefgh23','abcde-fgh23','ABCDE FGH23',' ABCDE-FGH23 ','ABCDE\tFGH23','ABCDE--FGH23','0BCDEFGH23','1BCDEFGH23','8BCDEFGH23','abcdefgh2l','ABCDEFGHI0','9BCDEFGH23','ABCDEFGH2','ABCDEFGH234','','   ','ABCDE_FGH23','ABCDE.FGH23','ABCDEFGH23\n','\uff21BCDEFGH23',' '*65+'ABCDEFGH23',None,123,'ABC\0DEFGH23','ABCDEFGH23\r','ABCDEFGH23\x7f']
    normal = []
    for text in inputs:
        try:
            canonical,reason = codes.normalise_code(text).decode('ascii'),None
        except codes.CodeError as exc:
            canonical,reason = None,exc.reason
        normal.append(dict(input=text,canonical=canonical,reason=reason))
    rows = []
    for id,pw in [('code-relay-1','ABCDEFGH23'),('code-relay-2','ZYXWV76543'),('code-forge-1','KLMNP23456'),('code-forge-2','QRSTU45672'),('code-gateway-1','BCDFG34567'),('code-gateway-2','WXYZ234567')]:
        row = dict(id=id,password=pw,normalised_utf8=pw.encode('ascii').hex())
        row.update((k,fixed(id,k)) for k in ('relay_pin','node_pin','relay_nonce','node_nonce','x','y'))
        row.update(id_a=row['relay_pin'],id_b=row['node_pin'])
        row['w'] = password_scalar(pw.encode('ascii'),*[bytes.fromhex(row[k]) for k in ('relay_pin','node_pin','relay_nonce','node_nonce')]).hex()
        row.update(vector(*[bytes.fromhex(row[k]) for k in ('id_a','id_b','w','x','y')]))
        assert list(row) == list(base['d32_vectors'][0])
        rows.append(row)
    byid = {r['id']:r for r in rows}
    negatives = []
    for id,baseid in [('wrong-code','code-forge-1'),('swapped-pins','code-gateway-1'),('wrong-nonce','code-relay-1')]:
        r = byid[baseid]
        n = dict(id=id,base=baseid,peer_base=baseid,peer_password=r['password'])
        n.update(('peer_'+k,r[k]) for k in ('relay_pin','node_pin','relay_nonce','node_nonce','x','y'))
        if id == 'wrong-code':
            n['peer_password'] = 'KLMNP23457'
        if id == 'swapped-pins':
            n['peer_relay_pin'],n['peer_node_pin'] = r['node_pin'],r['relay_pin']
        if id == 'wrong-nonce':
            n['peer_node_nonce'] = byid['code-relay-2']['node_nonce']
        n['b_transcript_pA'] = r['pA']
        n.update(negative_fields(r,n))
        n['expect'] = 'key-confirmation-failed'
        negatives.append({k:n[k] for k in base['negative_vectors'][0]})
    return dict(version=1,profile='code-v1',ciphersuite=base['ciphersuite'],base=dict(file='pairing-v2-vectors.json',sha256=hashlib.sha256((SHIP/'vectors/pairing-v2-vectors.json').read_bytes()).hexdigest()),encoding=base['encoding'],M_compressed=base['M_compressed'],N_compressed=base['N_compressed'],code=dict(alphabet=codes.ALPHABET,length=10,separators=[' ','\t','-'],maps={'0':'O','1':'I','8':'B'},max_input=64,nfkc=False,password_rule_applies=False),code_normalisation=normal,code_vectors=rows,negative_vectors=negatives)

def wire_vectors():
    result = []
    for kind,code,a_id,b_id in [('relay','ABCDEFGH23','relay-0000000000a1','workstation-0000000000b1'),('forge','KLMNP23456','forge-0000000000a2','workstation-0000000000b1'),('gateway','BCDFG34567','workstation-0000000000a3','headend-0000000000b3')]:
        a_pin = hashlib.sha256(base64.b64decode(TEST_CERT_A_B64)).hexdigest()
        b_pin = hashlib.sha256(base64.b64decode(TEST_CERT_B_B64)).hexdigest()
        a_nonce,b_nonce,x,y = [fixed('wire-'+kind,k) for k in ('relay_nonce','node_nonce','x','y')]
        row = dict(kind=kind,code=code,a_id=a_id,b_id=b_id,a_pin=a_pin,b_pin=b_pin,a_nonce=a_nonce,b_nonce=b_nonce,x=x,y=y)
        if kind != 'relay':
            row['b_cert_b64'] = TEST_CERT_B_B64
            row['handover_json'] = wire.handover(kind,TOKEN,PUBLIC,'serve-https')
        w = password_scalar(code.encode('ascii'),*[bytes.fromhex(v) for v in (a_pin,b_pin,a_nonce,b_nonce)])
        v = vector(bytes.fromhex(a_pin),bytes.fromhex(b_pin),w,bytes.fromhex(x),bytes.fromhex(y))
        frames = []
        def add(side,type,payload=b''):
            frames.append({'from':side,'name':wire.NAMES[type],'hex':wire.encode_frame(type,payload,handover_allowed=kind != 'relay').hex()})
        add('A',1,wire.dumps_compact(wire.hello(kind,'A',a_id,a_nonce)))
        add('B',1,wire.dumps_compact(wire.hello(kind,'B',b_id,b_nonce,TEST_CERT_B_B64)))
        add('A',2,wire.dumps_compact(wire.ack(kind)))
        add('B',2,wire.dumps_compact(wire.ack(kind)))
        for side,type,field in [('A',14,'pA'),('B',14,'pB'),('A',12,'macA'),('B',12,'macB')]:
            add(side,type,bytes.fromhex(v[field]))
        if kind != 'relay':
            add('A',15,wire.dumps_compact(row['handover_json']))
        add('A',13)
        add('B',13)
        row['frames'] = frames
        result.append(row)
    return dict(version=1,note='Public synthetic complete pairing transcripts.',transcripts=result)

REFUSALS = [b'',b'D32PAIR2 0 0 0 0 0\n\n',b'D32PAIR2 -1 0 0 0 0',b'D32PAIR2 +0 0 0 0 0',b'D32PAIR2 0  0 0 0 0',b'D32PAIR2 0\t0 0 0 0',b'D32PAIR2 0 0 0 0 2',b'D32PAIR2 1 0 0 0 0',b'D32PAIR2 0 2 1 0 0',b'D32PAIR2 0 0 9223372036854774908 0 0',b'D32PAIR2 0 0 0 9223372036854775808 0',b'D32PAIR2 0 0 0 0 0\0',b'D32PAIR2 0 0 0 0 0\r\n',b'D32PAIR2 0 0 0 0 0 extra',b'x'*256]

def lock_vectors():
    cases = []
    for before,now,after,left in [([0,0,0,0,0],1000,[0,0,1000,0,0],0),([0,0,1000,0,0],1500,[0,1000,1500,0,0],0),([0,1000,1500,0,0],2000,[1000,1500,2000,2900,0],900),([1000,1500,2000,2900,0],4000,[0,0,4000,2900,0],0),([0,0,5000,0,0],4000,[0,5000,5000,0,0],0),([3000,3500,4000,4900,0],4100,[3500,4000,4100,5000,0],900)]:
        cases.append(dict(before=before,operation='failed',time=now,after=after,remaining=left,file_text='D32PAIR2 '+' '.join(map(str,after))+'\n'))
    return dict(version=1,cases=cases,load_refusals=[x.hex() for x in REFUSALS])

def running(peers):
    return dict(BackendState='Running',Self=dict(HostName='deep32-test-self',DNSName='deep32-test-self.tail0000.ts.net.',TailscaleIPs=['100.64.0.10','fd7a:115c:a1e0::10']),Peer=peers)

def peer(nn,host,label,os,online,active,cur,relay,**extra):
    p = dict(ID='n'+str(nn).zfill(10)+'CNTRL',HostName=host,DNSName=label+'.tail0000.ts.net.',OS=os,TailscaleIPs=['100.64.0.'+str(100+nn),'fd7a:115c:a1e0::'+str(100+nn)],Online=online,Active=active,CurAddr=cur,Relay=relay)
    p.update(extra)
    return p

def fixtures():
    specs = [(1,'deep32-forge','deep32-forge','linux',True,True,'100.64.0.201:41641','tst',{}),(2,'deep32-workstation','deep32-workstation','openbsd',True,False,'','tst',{}),(3,'deep32-headend','deep32-headend','windows',True,True,'','tst',{}),(4,'Deep32-Forge2','deep32-forge2','linux',True,True,'100.64.0.204:41641','tst',{}),(5,'localhost','deep32-forge-3','linux',True,False,'','',{}),(6,'laptop-1','laptop-1','windows',True,True,'100.64.0.206:41641','tst',{}),(7,'deep32-old','deep32-old','linux',False,False,'','',{'Expired':True}),(8,'deep32-shared','deep32-shared','linux',True,True,'','tst',{'ShareeNode':True}),(9,'deep32-offline','deep32-offline','linux',False,False,'','',{})]
    peers = {'nodekey:'+'0'*62+str(s[0]).zfill(2):peer(*s[:-1],**s[-1]) for s in specs}
    p10 = peer(10,'deep32-forge','deep32-forge','linux',True,True,'','tst')
    p10['TailscaleIPs'] = ['fd7a:115c:a1e0::21']
    return {'status-mixed.json':dump(running(peers)),'status-nopeers.json':dump(running(None)),'status-noipv4.json':dump(running({'nodekey:'+'0'*62+'10':p10})),'status-needslogin.json':dump(dict(BackendState='NeedsLogin',AuthURL='https://login.tailscale.com/a/0000000000',Self=dict(HostName='localhost',TailscaleIPs=[]),Peer=None)),'status-malformed.json':b'not json\n','status-notobject.json':b'[]\n'}

def discovery_vectors():
    fx = fixtures()
    cases = []
    for name in fx:
        for kind in ('relay','forge','gateway'):
            if name == 'status-mixed.json':
                ps = list(json.loads(fx[name])['Peer'].values())
                selected = [(ps[i-1],path) for i,path in [(1,'direct'),(4,'direct'),(3,'relayed'),(2,'unknown'),(5,'unknown'),(9,'unknown')]]
            elif name == 'status-noipv4.json':
                selected = [(next(iter(json.loads(fx[name])['Peer'].values())),'relayed')]
            elif name == 'status-nopeers.json':
                selected = []
            else:
                expect = dict(exit=9,reason='not-signed-in',backend_state='NeedsLogin') if name == 'status-needslogin.json' else dict(exit=9,reason='status-unreadable')
                cases.append(dict(fixture=name,want=kind,expect=expect))
                continue
            expected = [dict(peer_id=p['ID'],host_name=p['HostName'],dns_name=p['DNSName'][:-1],os=p['OS'],ipv4=None if name == 'status-noipv4.json' else p['TailscaleIPs'][0],port={'relay':7452,'forge':7453,'gateway':7454}[kind],online=p['Online'],path=path,probe=None) for p,path in selected]
            cases.append(dict(fixture=name,want=kind,expect=expected))
    return dict(version=1,cases=cases)

def outputs():
    result = {'pairing-v2-code-vectors.json':dump(code_vectors()),'pairing-wire-v1-vectors.json':dump(wire_vectors()),'pairlock-v1-vectors.json':dump(lock_vectors()),'discovery-v1-vectors.json':dump(discovery_vectors())}
    return result

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out',type=Path,default=SHIP/'vectors')
    ap.add_argument('--check',action='store_true')
    args = ap.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to((SHIP/'vectors').resolve()):
        return 1
    failed = False
    for name,data in outputs().items():
        p = out/name
        if args.check:
            failed |= not p.exists() or p.read_bytes() != data
        else:
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(data)
    return int(failed)

if __name__ == '__main__':
    raise SystemExit(main())
