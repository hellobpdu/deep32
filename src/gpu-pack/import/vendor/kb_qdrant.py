"""Load flat vectors into an empty collection and check exact search."""
from __future__ import annotations
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import argparse
import array
import hashlib
import ipaddress
import json
import math
import os
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import kb_bundle as kb
import f32_sse as f32

def body_bytes(obj):
    return b'' if obj is None else json.dumps(obj, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')

def score_bits(value):
    return struct.pack('<f', value).hex()

class Refused(Exception):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(detail)

def validate_url(url, allow_remote=False):
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('', '/'):
            raise ValueError('url')
        port = p.port
        loopback = p.hostname.lower() == 'localhost'
        if not loopback:
            loopback = ipaddress.ip_address(p.hostname).is_loopback
        if not loopback and not allow_remote:
            raise ValueError('remote')
        return loopback
    except (ValueError, TypeError):
        raise Refused(2, 'invalid or non-loopback URL') from None

def cpu_flags(path='/proc/cpuinfo'):
    names = ['sse', 'sse4_1', 'avx', 'avx2', 'fma']
    try:
        lines = Path(path).read_text('ascii').splitlines()
        line = next(l.split(':', 1)[1] for l in lines if l.split(':', 1)[0].strip() == 'flags')
        values = set(line.split())
        return {**{k:k in values for k in names}, 'source':'/proc/cpuinfo'}
    except (OSError, UnicodeError, StopIteration):
        return {**{k:None for k in names}, 'source':'unavailable'}

def expected_identity(flags, loopback):
    if not loopback or flags['source']=='unavailable' or not flags['sse']:
        return None
    return not (flags['avx'] and flags['fma'])

def payload_for_chunk(chunk):
    # Same payload fields and conversions as rag_core.dense.payload_for_chunk.
    return {'chunk_id':chunk['chunk_id'],'chunk_sha256':chunk['chunk_sha256'],'doc_id':chunk['doc_id'],
            'section_id':chunk['section_id'],'ordinal':int(chunk['ordinal']),
            'heading_path':json.loads(chunk['heading_path_json'] or '[]'),
            'page_start':chunk['page_start'],'page_end':chunk['page_end']}

class Bundle:
    def __init__(self, directory, allow_unsigned=False, verify_cmd=None):
        self.directory=Path(directory)
        if not self.directory.is_dir():raise Refused(3,'bundle missing')
        check=kb.check_bundle(self.directory,allow_unsigned=allow_unsigned,verify_cmd=verify_cmd)
        if check.code:raise Refused(4,'bundle check refused')
        self.bundle=kb.read_json(self.directory/'BUNDLE.json')
        self.dense=kb.read_json(self.directory/'dense/EXPORT.json')
        self.rows=kb.point_rows(self.directory/'dense/points.bin')
        self.n,self.dim=self.dense['points_count'],self.dense['dim']
        self.vectors=self.directory/'dense/vectors.f32'
        docs,chunks,_=kb.document_data(self.directory)
        self.chunks={c['chunk_id']:c for c in chunks}
        self.opt_in=sorted(d['doc_id'] for d in docs if d['collection'] in self.bundle['opt_in'])
        self.identity={k:self.bundle[k] for k in ('generation','fingerprint','manifest_sha256')}

    def vector(self,index):
        return f32.read_rows(self.vectors,self.dim,index,1)[0]

    def queries(self,probes,query_file=None):
        order=sorted(range(self.n),key=lambda i:hashlib.sha256(('deep32-kb-probe/1:'+self.rows[i]['point_id']).encode()).hexdigest())[:probes]
        queries=[]
        for number,row in enumerate(order,1):
            for name,filter_ in [('all',None),('default',{'must_not_doc_ids':self.opt_in})]:
                queries.append({'id':f'probe-{number:03d}/'+name,'vector':self.vector(row),'filter':filter_})
        if query_file:
            try:
                for line in Path(query_file).read_text('utf-8').splitlines():
                    if not line.strip():continue
                    q=json.loads(line)
                    if set(q)!={'id','vector','filter'} or not isinstance(q['id'],str) or not q['id'] or not isinstance(q['vector'],list) or len(q['vector'])!=self.dim or any(type(x) not in (int,float) for x in q['vector']):raise ValueError('query')
                    q['vector']=[f32.f32(x) for x in q['vector']]
                    if not all(map(math.isfinite,q['vector'])):raise ValueError('finite')
                    validate_filter(q['filter'])
                    queries.append(q)
            except (OSError,ValueError,KeyError,TypeError,OverflowError):raise Refused(5,'invalid query file') from None
        if len({q['id'] for q in queries})!=len(queries):raise Refused(5,'duplicate query id')
        return queries

    def reference_scores(self,q):
        scores=f32.scores_sse(self.vectors,self.n,self.dim,f32.cosine_preprocess(q['vector']))
        filter_=q['filter'] or {}
        mask=[('must_doc_ids' not in filter_ or p['doc_id'] in filter_['must_doc_ids']) and p['doc_id'] not in filter_.get('must_not_doc_ids',[]) for p in self.rows]
        return [{'point_id':self.rows[i]['point_id'],'chunk_id':self.rows[i]['chunk_id'],'score':s,'score_bits':score_bits(s)} for i,s in f32.top_rows(scores,mask,self.n)]

def validate_filter(value):
    if value is not None and (not isinstance(value,dict) or set(value)-{'must_doc_ids','must_not_doc_ids'} or any(not isinstance(v,list) or any(not isinstance(x,str) for x in v) for v in value.values())):
        raise ValueError('filter')

def qdrant_filter(value):
    if value is None:return None
    result={}
    for name,key in [('must_doc_ids','must'),('must_not_doc_ids','must_not')]:
        if name in value:result[key]=[{'key':'doc_id','match':{'any':sorted(value[name])}}]
    return result

def query_body(q,limit):
    obj={'query':q['vector'],'limit':limit,'with_payload':['chunk_id'],'with_vector':False,'params':{'exact':True}}
    if q['filter'] is not None:obj['filter']=qdrant_filter(q['filter'])
    return obj

def compare_ranked(reference_scores,engine_hits,limit,tol):
    reference={h['point_id']:h for h in reference_scores}
    k=min(limit,len(reference_scores));top=reference_scores[:k]
    cutoff=top[-1]['score'] if top else math.inf
    failed={};diffs=[]
    if len(engine_hits)!=k:failed['T1']=min(len(engine_hits),k)
    seen=set()
    for i,h in enumerate(engine_hits):
        pid=h['point_id']
        if pid in seen or pid not in reference:failed.setdefault('T2',i)
        seen.add(pid)
        if pid not in reference:
            failed.setdefault('T4',i);continue
        ref=reference[pid]['score'];diff=abs(h['score']-ref);diffs.append(diff)
        if not math.isfinite(h['score']) or diff>tol:failed.setdefault('T3',i)
        if ref<cutoff-tol:failed.setdefault('T4',i)
    for i,h in enumerate(top):
        if h['score']>cutoff+tol and h['point_id'] not in seen:failed.setdefault('T5',i)
    for j in range(len(engine_hits)):
        right=reference.get(engine_hits[j]['point_id'])
        if right is None:continue
        for i in range(j):
            left=reference.get(engine_hits[i]['point_id'])
            if left is not None and left['score']<right['score']-tol:failed.setdefault('T6',j)
    rules=[r for r in ('T1','T2','T3','T4','T5','T6') if r in failed]
    exact=sum(a['point_id']==b['point_id'] and score_bits(a['score'])==score_bits(b['score']) for a,b in zip(top,engine_hits))
    return {'status':'pass' if not rules else 'fail','failed_rules':rules,'failed_rule':rules[0] if rules else None,'position':failed[rules[0]] if rules else None,'identical':len(top)==len(engine_hits)==exact,'exact_positions':exact,'max_abs_diff':max(diffs,default=0.0)}

class Transport:
    def __init__(self,url,collection,api_key=None,readonly=False):
        self.url,self.collection,self.key,self.readonly=url.rstrip('/'),collection,api_key,readonly
        self.index=0
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self,method,path,body=None,absent=False,root=False):
        if method not in ('GET','PUT','POST'):raise Refused(2,'method refused')
        if self.readonly and not (method=='GET' and path in ('/','/collections/'+self.collection) or method=='POST' and path in ('/collections/'+self.collection+'/points/scroll','/collections/'+self.collection+'/points/query')):raise Refused(2,'read-only request refused')
        self.index+=1;raw_body=body_bytes(body)
        headers={'Content-Type':'application/json','User-Agent':'deep32-kb-qdrant/1'}
        if self.key:headers['api-key']=self.key
        request=urllib.request.Request(self.url+path,data=raw_body if body is not None else None,headers=headers,method=method)
        status=0
        try:
            with self.opener.open(request,timeout=120) as response:
                status=response.status;raw=response.read()
        except urllib.error.HTTPError as e:
            status=e.code;raw=e.read()
            if absent and status==404:return None,b''
        except (OSError,urllib.error.URLError):
            raw=b''
        detail=f'request {self.index} {method} {path} HTTP {status}'
        if status!=200:raise Refused(10,detail)
        try:obj=json.loads(raw)
        except (ValueError,UnicodeError):raise Refused(10,detail) from None
        if not isinstance(obj,dict) or (root and 'version' not in obj) or (not root and obj.get('status')!='ok') or (method=='PUT' and '?wait=true' in path and (not isinstance(obj.get('result'),dict) or obj['result'].get('status')!='completed')):raise Refused(10,detail)
        return obj,raw

def request_plan(bundle,collection,batch,queries,limit,wait_seconds=1200):
    prefix='/collections/'+collection
    requests=[('GET','/',None,'version'),('GET',prefix,None,'must be absent'),('PUT',prefix,{'vectors':{'size':bundle.dim,'distance':'Cosine'}},'create collection')]
    for field in ('doc_id','chunk_id'):requests.append(('PUT',prefix+'/index?wait=true',{'field_name':field,'field_schema':'keyword'},'index '+field))
    for start in range(0,bundle.n,batch):
        rows=bundle.rows[start:start+batch];vectors=f32.read_rows(bundle.vectors,bundle.dim,start,len(rows))
        points=[{'id':p['point_id'],'vector':v,'payload':payload_for_chunk(bundle.chunks[p['chunk_id']])} for p,v in zip(rows,vectors)]
        requests.append(('PUT',prefix+'/points?wait=true',{'points':points},f"points={len(rows)} first={rows[0]['point_id']} last={rows[-1]['point_id']}"))
    requests.append(('GET',prefix,None,f'(repeat every 2 s until not yellow, at most {wait_seconds} s)'))
    for start in range(0,bundle.n,2048):
        offset=None if start==0 else bundle.rows[start]['point_id']
        requests.append(('POST',prefix+'/points/scroll',{'limit':2048,'offset':offset,'with_payload':True,'with_vector':True},'read back'))
    for q in queries:requests.append(('POST',prefix+'/points/query',query_body(q,limit),'exact '+q['id']))
    return [{'index':i,'method':m,'path':p,'body_bytes':len(body_bytes(b)),'body_sha256':hashlib.sha256(body_bytes(b)).hexdigest(),'summary':s,'body':b} for i,(m,p,b,s) in enumerate(requests,1)]

def configuration_good(info,dim):
    try:
        config=info['config'];params=config['params'];v=params['vectors'];schema=info['payload_schema']
        return (v['size']==dim and v['distance']=='Cosine' and v.get('datatype','float32') in ('float32',None) and config.get('quantization_config') is None and not params.get('sparse_vectors') and all(schema[k]['data_type']=='keyword' for k in ('doc_id','chunk_id')))
    except (KeyError,TypeError):return False

def poll(transport,path,wait_seconds,sleep=time.sleep,clock=time.monotonic):
    started=clock();obj,_=transport.request('GET',path)
    while obj['result']['status']=='yellow' and clock()-started<wait_seconds:
        sleep(2);obj,_=transport.request('GET',path)
    return obj['result'],max(0.0,clock()-started)

def readback(transport,bundle,collection):
    offset=None;points=[];seen=set()
    while True:
        obj,raw=transport.request('POST','/collections/'+collection+'/points/scroll',{'limit':2048,'offset':offset,'with_payload':True,'with_vector':True})
        try:
            text=json.loads(raw,parse_float=str,parse_int=str)
            for p,t in zip(obj['result']['points'],text['result']['points']):
                points.append((p['id'],f32.parse_f32_texts(t['vector']),p['payload']))
            offset=obj['result']['next_page_offset']
            if offset is None:break
            if str(offset) in seen:raise ValueError('offset cycle')
            seen.add(str(offset))
        except (ValueError,TypeError,KeyError,OverflowError):raise Refused(12,'invalid read-back page') from None
    counts={'points':len(points),'id_mismatches':0,'vector_mismatches':0,'payload_mismatches':0,'rows':[]}
    for i in range(max(len(points),bundle.n)):
        if i>=len(points) or i>=bundle.n:
            counts['id_mismatches']+=1
            if len(counts['rows'])<20:counts['rows'].append({'row':i,'point_id':points[i][0] if i<len(points) else bundle.rows[i]['point_id']})
            continue
        p=bundle.rows[i];pid,vec,payload=points[i];bad=False
        if pid!=p['point_id']:counts['id_mismatches']+=1;bad=True
        actual=struct.pack('<'+'f'*len(vec),*vec)
        expected=struct.pack('<'+'f'*bundle.dim,*bundle.vector(i))
        if actual!=expected:counts['vector_mismatches']+=1;bad=True
        if payload!=payload_for_chunk(bundle.chunks[p['chunk_id']]):counts['payload_mismatches']+=1;bad=True
        if bad and len(counts['rows'])<20:counts['rows'].append({'row':i,'point_id':pid})
    return counts

def tolerance(transport,bundle,collection,queries,limit,tol):
    results=[];ranked=[]
    for q in queries:
        obj,raw=transport.request('POST','/collections/'+collection+'/points/query',query_body(q,limit))
        try:
            text=json.loads(raw,parse_float=str,parse_int=str)
            values=f32.parse_f32_texts([h['score'] for h in text['result']['points']])
            hits=[{'point_id':h['id'],'chunk_id':h['payload']['chunk_id'],'score':s,'score_bits':score_bits(s)} for h,s in zip(obj['result']['points'],values)]
        except (ValueError,TypeError,KeyError,OverflowError):raise Refused(10,'invalid query response') from None
        result=compare_ranked(bundle.reference_scores(q),hits,limit,tol);result['id']=q['id'];results.append(result)
        ranked.append({'id':q['id'],'filter':q['filter'],'hits':hits})
    return {'value':tol,'limit':limit,'queries':len(results),'identical':sum(r['identical'] for r in results),'passed':sum(r['status']=='pass' for r in results),'failed':sum(r['status']=='fail' for r in results),'max_abs_diff':max((r['max_abs_diff'] for r in results),default=0.0),'results':results},ranked

def ranked_file(bundle,queries,limit):
    return {'format':'deep32-ranked-hits','format_version':1,'engine':'reference-sse-python','bundle':bundle.identity,'limit':limit,'cpu_flags':cpu_flags(),'queries':[{'id':q['id'],'filter':q['filter'],'hits':bundle.reference_scores(q)[:limit+64]} for q in queries]}

def validate_ranked(obj):
    try:
        if obj['format']!='deep32-ranked-hits' or obj['format_version']!=1 or type(obj['limit']) is not int or obj['limit']<=0 or not isinstance(obj['bundle'],dict):raise ValueError('format')
        ids=set()
        for q in obj['queries']:
            if not isinstance(q['id'],str) or q['id'] in ids:raise ValueError('query')
            ids.add(q['id']);validate_filter(q['filter'])
            for h in q['hits']:
                if str(uuid.UUID(h['point_id']))!=h['point_id'] or not re.fullmatch('chk_[0-9a-f]{64}',h['chunk_id']) or not math.isfinite(h['score']) or score_bits(h['score'])!=h['score_bits']:raise ValueError('hit')
    except (KeyError,TypeError,ValueError,OverflowError):raise Refused(5,'invalid ranked-hits file') from None

def compare_files(reference,engine,tol):
    try:a,b=kb.read_json(reference),kb.read_json(engine)
    except (OSError,ValueError):raise Refused(5,'ranked-hits file unreadable') from None
    validate_ranked(a);validate_ranked(b)
    if a['bundle']!=b['bundle'] or a['limit']!=b['limit'] or [(q['id'],q['filter']) for q in a['queries']]!=[(q['id'],q['filter']) for q in b['queries']]:raise Refused(5,'ranked-hits inputs differ')
    results=[]
    for ref,eng in zip(a['queries'],b['queries']):
        result=compare_ranked(ref['hits'],eng['hits'][:a['limit']],a['limit'],tol);result['id']=ref['id']
        known={h['point_id'] for h in ref['hits']};unknown=any(h['point_id'] not in known for h in eng['hits'][:a['limit']])
        cut=ref['hits'][min(a['limit'],len(ref['hits']))-1]['score'] if ref['hits'] else math.inf
        if unknown and ref['hits'] and ref['hits'][-1]['score']>=cut-tol:
            result.update(status='fail',failed_rule='reference-too-short',failed_rules=['reference-too-short'])
        elif unknown:
            # A truncated offline reference cannot establish bundle membership.
            # It can establish that an omitted hit fails the cutoff rule.
            duplicates=len({h['point_id'] for h in eng['hits'][:a['limit']]})!=len(eng['hits'][:a['limit']])
            if not duplicates and 'T2' in result['failed_rules']:
                result['failed_rules'].remove('T2')
                result['failed_rule']=result['failed_rules'][0]
                result['position']=next(i for i,h in enumerate(eng['hits'][:a['limit']]) if h['point_id'] not in known)
        results.append(result)
    return {'schema':'deep32.kb-qdrant.v1','verb':'compare','status':'ok' if all(r['status']=='pass' for r in results) else 'refused','exit':0 if all(r['status']=='pass' for r in results) else 13,'tolerance':{'value':tol,'limit':a['limit'],'queries':len(results),'identical':sum(r['identical'] for r in results),'passed':sum(r['status']=='pass' for r in results),'failed':sum(r['status']=='fail' for r in results),'max_abs_diff':max((r['max_abs_diff'] for r in results),default=0.0),'results':results}}

def parser():
    p=kb.Parser();sub=p.add_subparsers(dest='verb',required=True)
    for verb in ('plan','load','check','reference','compare'):
        v=sub.add_parser(verb);v.add_argument('--json',action='store_true');v.add_argument('--report')
        if verb=='compare':
            v.add_argument('--reference',required=True);v.add_argument('--engine',required=True);v.add_argument('--tolerance',type=float,default=1e-5);continue
        v.add_argument('--bundle',required=True);v.add_argument('--probes',type=int,default=8);v.add_argument('--queries');v.add_argument('--limit',type=int,default=50)
        v.add_argument('--allow-unsigned',action='store_true');v.add_argument('--verify-cmd')
        if verb=='reference':v.add_argument('--out',required=True);continue
        v.add_argument('--qdrant-url',required=True);v.add_argument('--collection');v.add_argument('--allow-remote',action='store_true')
        if verb in ('plan','load'):v.add_argument('--batch',type=int,default=256);v.add_argument('--wait-seconds',type=float,default=1200)
        if verb in ('load','check'):v.add_argument('--api-key-env');v.add_argument('--tolerance',type=float,default=1e-5)
    return p

def execute(a,sleep=time.sleep,clock=time.monotonic):
    if hasattr(a,'tolerance') and (not math.isfinite(a.tolerance) or a.tolerance<0):raise Refused(2,'invalid tolerance')
    if a.verb=='compare':return compare_files(a.reference,a.engine,a.tolerance)
    if a.probes<0 or a.limit<=0 or getattr(a,'batch',1)<=0 or getattr(a,'wait_seconds',0)<0:raise Refused(2,'invalid numeric argument')
    if not a.allow_unsigned and not a.verify_cmd:raise Refused(2,'signature option required')
    try:verify=kb.json_argv(a.verify_cmd) if a.verify_cmd else None
    except ValueError:raise Refused(2,'bad JSON-ARGV') from None
    bundle=Bundle(a.bundle,a.allow_unsigned,verify)
    queries=bundle.queries(a.probes,a.queries)
    if a.verb=='reference':
        obj=ranked_file(bundle,queries,a.limit);kb.write_json(a.out,obj)
        return {'schema':'deep32.kb-qdrant.v1','verb':'reference','status':'ok','exit':0,'queries':len(queries)}
    loopback=validate_url(a.qdrant_url,a.allow_remote)
    collection=a.collection or bundle.dense['collection']
    if not re.fullmatch('[A-Za-z0-9_-]{1,255}',collection) or a.verb=='load' and collection!=bundle.dense['collection']:raise Refused(2,'invalid collection')
    key=None
    if getattr(a,'api_key_env',None):
        key=os.environ.get(a.api_key_env)
        if not key:raise Refused(2,'API key environment variable empty')
    if a.verb=='plan':
        plan=request_plan(bundle,collection,a.batch,queries,a.limit,a.wait_seconds)
        return {'schema':'deep32.kb-qdrant.v1','verb':'plan','status':'ok','exit':0,'requests':[{k:v for k,v in r.items() if k!='body'} for r in plan]}
    flags=cpu_flags();expected=expected_identity(flags,loopback)
    report={'schema':'deep32.kb-qdrant.v1','verb':a.verb,'status':'ok','exit':0,'qdrant':{'url_is_loopback':loopback,'version':None},'collection':collection,'bundle':bundle.identity,'cpu_flags':flags,'expected_identical':expected,'load':{'batches':0,'points':0,'status_wait':{'status':None,'seconds':0.0}},'readback':None,'tolerance':None,'warnings':[]}
    transport=Transport(a.qdrant_url,collection,key,readonly=a.verb=='check');prefix='/collections/'+collection
    try:
        obj,_=transport.request('GET','/',root=True);report['qdrant']['version']=obj['version']
        obj,_=transport.request('GET',prefix,absent=a.verb=='load')
        if a.verb=='load':
            if obj is not None:raise Refused(11,'collection already exists')
            plan=request_plan(bundle,collection,a.batch,queries,a.limit,a.wait_seconds)
            for request in plan[2:]:
                if request['method']=='GET':break
                transport.request(request['method'],request['path'],request['body'])
                if '/points?' in request['path']:report['load']['batches']+=1;report['load']['points']+=len(request['body']['points'])
            info,seconds=poll(transport,prefix,a.wait_seconds,sleep,clock)
            report['load']['status_wait']={'status':info['status'],'seconds':seconds}
            if info['status']!='green':report['warnings'].append({'id':'collection-not-green','detail':info['status']})
        else:info=obj['result']
        if not configuration_good(info,bundle.dim):raise Refused(14,'collection configuration differs')
        report['readback']=readback(transport,bundle,collection)
        if any(report['readback'][k] for k in ('id_mismatches','vector_mismatches','payload_mismatches')):raise Refused(12,'read-back differs')
        report['tolerance'],_=tolerance(transport,bundle,collection,queries,a.limit,a.tolerance)
        if expected is True and report['tolerance']['identical']!=len(queries):report['warnings'].append({'id':'not-identical-on-sse-host','detail':'at least one exact result differs'})
        if report['tolerance']['failed']:raise Refused(13,'tolerance check failed')
    except Refused as e:
        report.update(status='refused',exit=e.code,detail=e.detail)
    return report

def main(argv=None):
    a=None
    try:a=parser().parse_args(argv);report=execute(a)
    except ValueError:report={'schema':'deep32.kb-qdrant.v1','verb':getattr(a,'verb','unknown'),'status':'refused','exit':2,'detail':'invalid arguments'}
    except Refused as e:report={'schema':'deep32.kb-qdrant.v1','verb':getattr(a,'verb','unknown'),'status':'refused','exit':e.code,'detail':e.detail}
    except Exception:
        print('kb_qdrant: internal error',file=sys.stderr);return 1
    if getattr(a,'report',None):kb.write_json(a.report,report)
    stream=sys.stderr if getattr(a,'json',False) else sys.stdout
    if report.get('verb')=='plan' and not report['exit']:
        for r in report['requests']:print(f"{r['index']} {r['method']} {r['path']} body_bytes={r['body_bytes']} body_sha256={r['body_sha256']} {r['summary']}",file=stream)
    print(f"kb_qdrant: {report['verb']} {'OK' if report['exit']==0 else 'REFUSED'} (exit {report['exit']})",file=stream)
    if getattr(a,'json',False):print(json.dumps(report,separators=(',',':'),ensure_ascii=True))
    return report['exit']

if __name__=='__main__':raise SystemExit(main())
