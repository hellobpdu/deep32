#!/usr/bin/env python3
"""Measure offline owner gates for an unmerged image rebuild."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import keyword
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alpha2_rebuild as rebuild_tool
from provenance import canonical, digest, members
from runtime_stage import safe_tree

CHECKS = ('abi', 'imports', 'runtime_compilation', 'retained_inputs_absent',
          'native_dependency_files_equal')
FORMAT = 'deep32-alpha2-owner-evidence/1'
COMPILERS = ('cc', 'gcc', 'g++', 'c++', 'clang', 'clang++', 'ld', 'nvcc', 'tcc')
MODEL = 'jinaai/jina-reranker-v3.5'
DOCUMENTS = (
    'OSPF routers exchange link state information within an area.',
    'BGP exchanges routing information between autonomous systems.',
    'IPsec protects IP traffic using security associations.',
    'VLANs divide an Ethernet network into separate broadcast domains.',
    'DNS resolves domain names using resource records.',
)
QUERIES = (
    'How does OSPF share link state information?',
    'What is an OSPF area?',
    'Which protocol exchanges routes between autonomous systems?',
    'How are BGP routes exchanged?',
    'Which protocol protects IP packets?',
    'What is an IPsec security association?',
    'How can an Ethernet network separate broadcast domains?',
    'What does a VLAN divide?',
    'Which service resolves domain names?',
    'What information does DNS store?',
    'Compare routing information in OSPF and BGP.',
    'Compare VLAN separation with IPsec protection.',
)

# These programs run only inside the measured image, never on the owner host.
ABI_PROBE = r'''
import hashlib,json,os,pathlib,platform,site,sys,sysconfig
roots=set(site.getsitepackages())
roots.update(p for p in sys.path if pathlib.Path(p).name in ('site-packages','dist-packages'))
roots.update(sysconfig.get_paths()[k] for k in ('purelib','platlib'))
files={}
def fail(error):raise error
for root in sorted(roots):
    if not pathlib.Path(root).is_dir():continue
    for folder,dirs,names in os.walk(root,onerror=fail):
        dirs.sort()
        for name in sorted(names):
            if not (name.endswith('.so') or '.so.' in name):continue
            p=pathlib.Path(folder)/name
            with p.open('rb') as stream:
                if stream.read(4)!=b'\x7fELF':continue
                stream.seek(0)
                h=hashlib.file_digest(stream,'sha256').hexdigest()
            files[str(p)]=h
print(json.dumps(dict(version=list(sys.version_info[:3]),cache_tag=sys.implementation.cache_tag,
    soabi=sysconfig.get_config_var('SOABI'),ext_suffix=sysconfig.get_config_var('EXT_SUFFIX'),
    libc=list(platform.libc_ver()),machine=platform.machine(),
    shared_objects=[dict(path=p,sha256=h) for p,h in sorted(files.items())]),sort_keys=True))
'''.strip()

IMPORT_NAMES_PROBE = r'''
import importlib.metadata as metadata,json,keyword,pathlib
names=set()
for dist in metadata.distributions():
    top=dist.read_text('top_level.txt')
    if top is not None:
        candidates=top.splitlines()
    else:
        if dist.files is None:raise ValueError('distribution file manifest absent')
        candidates=[]
        for item in dist.files:
            parts=pathlib.PurePosixPath(str(item)).parts
            if not parts or parts[0] in ('.','..') or not (str(item).endswith(('.py','.so')) or '.so.' in parts[-1]):continue
            first=parts[0]
            candidates.append(first.split('.')[0] if len(parts)==1 else first)
    for name in candidates:
        name=name.strip()
        if name.isidentifier() and not keyword.iskeyword(name) and not name.startswith('_') and name not in ('tests','test','testing'):
            names.add(name)
print(json.dumps(sorted(names)))
'''.strip()

IMPORTS_PROBE = r'''
import json,re,subprocess,sys
outcomes={};children={}
for name in json.loads(sys.argv[1]):
    try:
        child=subprocess.run([sys.executable,'-I','-B','-c','import '+name],capture_output=True,text=True,timeout=120)
        children[name]=dict(returncode=child.returncode,stdout=child.stdout,stderr=child.stderr)
        if child.returncode==0:outcomes[name]='ok'
        else:
            matches=re.findall(r'^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt)|[A-Za-z_][\w.]*):(?: |$)',child.stderr,re.M)
            if not matches:raise ValueError('import failed without a measurable exception class: '+name)
            outcomes[name]=matches[-1].rsplit('.',1)[-1]
    except subprocess.TimeoutExpired as error:
        def text(value):return value.decode('utf-8',errors='replace') if isinstance(value,bytes) else value
        outcomes[name]='TimeoutExpired';children[name]=dict(timeout_seconds=120,stdout=text(error.stdout),stderr=text(error.stderr))
print(json.dumps(dict(outcomes=outcomes,children=children),sort_keys=True))
'''.strip()

COMPILER_PROBE = 'import json,shutil;print(json.dumps(sorted(n for n in '+repr(COMPILERS)+' if shutil.which(n))))'

RETAINED_PROBE = r'''
import hashlib,json,pathlib,subprocess,sys
inputs=json.loads(sys.argv[1]);application=set(json.loads(sys.argv[2]))
command=['find','/','-xdev','(','-path','/proc','-o','-path','/sys','-o','-path','/dev',')','-prune','-o','-type','f','-print0']
found=subprocess.run(command,check=True,capture_output=True).stdout
rows=[]
for raw in sorted(set(found.split(b'\0'))-{b''}):
    p=pathlib.Path(raw.decode('utf-8'));name=str(p);reasons=[]
    if name.endswith('.whl'):reasons.append('wheel')
    if any(name.startswith(root+'/') for root in ('/tmp','/root','/build','/src','/wheels','/context','/var/cache')) and name.endswith(('.tar.gz','.tgz','.zip')):reasons.append('build-archive')
    if name.startswith('/root/.cache/pip/'):reasons.append('pip-cache')
    if name.startswith('/var/cache/apt/archives/') and name.endswith('.deb'):reasons.append('apt-archive')
    # Compare every regular file, including files outside conventional caches.
    with p.open('rb') as stream:h=hashlib.file_digest(stream,'sha256').hexdigest()
    if h in inputs and name not in application:reasons.append('context-content')
    if reasons:rows.append(dict(path=name,sha256=h,reasons=sorted(reasons)))
print(json.dumps(rows,sort_keys=True))
'''.strip()

HEALTH_PROBE = r'''
import json,urllib.request
with urllib.request.urlopen('http://127.0.0.1:8082/health',timeout=5) as response:
    print(json.dumps(json.load(response),sort_keys=True,allow_nan=False))
'''.strip()

SEARCH_PROBE = r'''
import json,sys,urllib.request
responses=[]
for query in json.loads(sys.argv[1]):
    body=dict(model='jinaai/jina-reranker-v3.5',query=query,documents=json.loads(sys.argv[2]),top_n=5,return_documents=True)
    request=urllib.request.Request('http://127.0.0.1:8082/v1/rerank',data=json.dumps(body).encode('utf-8'),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=900) as response:responses.append(json.load(response))
print(json.dumps(responses,sort_keys=True,allow_nan=False,separators=(',',':')))
'''.strip()


def _json(value):
    def invalid(constant):
        raise ValueError('nonfinite JSON measurement: '+constant)
    def pairs(rows):
        result = {}
        for key, item in rows:
            if key in result:
                raise ValueError('duplicate measurement key')
            result[key] = item
        return result
    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('nonfinite JSON measurement')
        return result
    return json.loads(value, parse_constant=invalid, parse_float=number, object_pairs_hook=pairs)


class Commands:
    def __init__(self, runner):
        self.runner, self.rows = runner, []

    def run(self, command, **kwargs):
        command = list(command)
        environment = dict(os.environ, LC_ALL='C', TZ='UTC', PYTHONDONTWRITEBYTECODE='1')
        environment.update(kwargs.pop('env', {}))
        environment.update(LC_ALL='C', TZ='UTC')
        row = {'argv': command}
        self.rows.append(row)
        try:
            result = self.runner(command, check=True, capture_output=True, text=True,
                                 env=environment, **{k: v for k, v in kwargs.items()
                                                     if k not in ('check', 'capture_output', 'text')})
            row.update(stdout=result.stdout or '', stderr=getattr(result, 'stderr', '') or '',
                       returncode=getattr(result, 'returncode', 0))
            return result
        except (OSError, subprocess.SubprocessError) as exc:
            def printable(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value
            row.update(error=type(exc).__name__, stdout=printable(getattr(exc, 'stdout', None)),
                       stderr=printable(getattr(exc, 'stderr', None)), returncode=getattr(exc, 'returncode', None))
            raise

    def output(self, command, **kwargs):
        return self.run(command, **kwargs).stdout or ''


def container_command(image, code, arguments=(), tmp=False, read_all=False):
    command = ['docker', 'run', '--rm', '--network', 'none', '--pull=never',
               '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
               '--pids-limit=256', '--entrypoint', 'python']
    if read_all:
        # Inventory probes must read every file, including directories root reaches only through DAC
        # override (e.g. apt's 0700 _apt partial folder); read-search grants reading, nothing else.
        # As root: images that set a non-root USER would otherwise never hold the added capability.
        command += ['--user', '0:0', '--cap-add', 'DAC_READ_SEARCH']
    if tmp:
        command += ['--tmpfs', '/tmp:rw,nosuid,nodev,size=256m']
    command += ['--env', 'HOME=/nonexistent', '--env', 'PYTHONDONTWRITEBYTECODE=1',
                image, '-I', '-B', '-c', code, *arguments]
    return command


def _probe(commands, identity, code, arguments=(), tmp=False, read_all=False):
    return _json(commands.output(container_command(identity, code, arguments, tmp, read_all)))


def _names(value):
    if (not isinstance(value, list) or value != sorted(set(value))
            or any(not isinstance(n, str) or not n.isidentifier() or n.startswith('_')
                   or keyword.iskeyword(n) or n in ('test', 'tests', 'testing') for n in value)):
        raise ValueError('invalid measured import names')
    return value


def _outcomes(value, names):
    if (not isinstance(value, dict) or not isinstance(value.get('outcomes'), dict)
            or set(value['outcomes']) != set(names)
            or any(not isinstance(v, str) or not v.isidentifier() for v in value['outcomes'].values())):
        raise ValueError('incomplete import measurement')
    if 'TimeoutExpired' in value['outcomes'].values():
        raise ValueError('import timeout is unmeasurable')
    return value['outcomes']


def _both(measured, measure):
    errors = []
    for side in ('before', 'after'):
        try:
            measure(side)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            errors.append(side+': '+type(exc).__name__+': '+str(exc))
    if errors:
        raise ValueError('; '.join(errors))


def _abi(value):
    if (not isinstance(value, dict) or set(value) != {'version','cache_tag','soabi','ext_suffix','libc','machine','shared_objects'}
            or not isinstance(value['version'], list) or len(value['version']) != 3
            or any(type(n) is not int or n < 0 for n in value['version'])
            or any(not isinstance(value[k], str) or not value[k] for k in ('cache_tag','soabi','ext_suffix','machine'))
            or not isinstance(value['libc'], list) or len(value['libc']) != 2
            or any(not isinstance(n, str) for n in value['libc'])
            or not isinstance(value['shared_objects'], list)):
        raise ValueError('complete ABI measurement required')
    previous = ''
    for row in value['shared_objects']:
        if (not isinstance(row, dict) or set(row) != {'path','sha256'}
                or not isinstance(row['path'], str) or not row['path'].startswith('/') or row['path'] <= previous
                or not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            raise ValueError('sorted ELF shared-object measurements required')
        previous = row['path']
    return value


def _retained(value):
    if not isinstance(value, list):
        raise ValueError('retained input inventory required')
    previous = ''
    for row in value:
        if (not isinstance(row, dict) or set(row) != {'path','sha256','reasons'}
                or not isinstance(row['path'], str) or not row['path'].startswith('/') or row['path'] <= previous
                or not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])
                or not isinstance(row['reasons'], list) or not row['reasons']
                or any(not isinstance(r, str) for r in row['reasons'])
                or row['reasons'] != sorted(set(row['reasons']))
                or not set(row['reasons']) <= {'wheel','build-archive','pip-cache','apt-archive','context-content'}):
            raise ValueError('sorted retained input paths, hashes, and reasons required')
        previous = row['path']
    return value


def _regular_tree(path):
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError('linked qualification path')
    safe_tree(path)


def _receipt(stage, rebuild):
    receipt = rebuild_tool.read_json(rebuild / 'BUILD.json')
    if receipt.get('format') != rebuild_tool.FORMAT or receipt.get('status') != 'qualification-required':
        raise ValueError('unmerged qualification-required BUILD.json required')
    roles = receipt['roles']
    if (not isinstance(roles, list) or not roles or len(set(roles)) != len(roles)
            or any(r not in rebuild_tool.BASE_ROLES + rebuild_tool.FULL_ROLES for r in roles)):
        raise ValueError('invalid rebuild roles')
    mappings = []
    for key in ('before_images', 'images', 'dependency_equality'):
        rows = receipt[key]
        if not isinstance(rows, list) or [row['role'] for row in rows] != roles:
            raise ValueError('rebuild role bindings differ')
        mappings.append({row['role']: row for row in rows})
    if receipt['source'] != dict(pins_sha256=digest(stage/'pins.json'),
                                  payload_sha256=digest(stage/'PAYLOAD.json'),
                                  index_sha256=digest(stage/'payload/images/index.json')):
        raise ValueError('source stage seal differs')
    if receipt['context_files'] != members(rebuild/'contexts'):
        raise ValueError('build context seal differs')
    before, after, comparisons = mappings
    index_rows = rebuild_tool.read_json(stage/'payload/images/index.json')
    if roles != list(rebuild_tool.selected_roles(index_rows)):
        raise ValueError('rebuild roles differ from sealed stage')
    index = {row['role']: row for row in index_rows}
    if [before[r] for r in roles] != [index[r] for r in roles]:
        raise ValueError('baseline images differ from sealed stage index')
    for role in roles:
        for row, prefix in ((before[role], stage/'payload'), (after[role], rebuild)):
            if row['path'] != 'images/'+role+'.tar':
                raise ValueError('saved image path differs')
            actual = digest(prefix/row['path'])
            suffix = 'before' if prefix == stage/'payload' else 'after'
            if actual != row['sha256'] or actual != comparisons[role][suffix+'_archive_sha256']:
                raise ValueError('archive sha256 differs: '+role+' '+suffix)
    return receipt, before, after, comparisons


def _search(commands, identity, snapshot, refs, sleep, monotonic):
    cache = '/models/hub/models--jinaai--jina-reranker-v3.5'
    command = ['docker', 'run', '--detach', '--rm', '--network', 'none', '--pull=never',
               '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=256',
               '--mount', 'type=bind,src='+str(snapshot)+',dst='+cache+'/snapshots/'+snapshot.name+',readonly',
               '--mount', 'type=bind,src='+str(refs)+',dst='+cache+'/refs,readonly']
    for value in ('RERANKER_DEVICE=cpu', 'RERANKER_DTYPE=float32', 'OMP_NUM_THREADS=1',
                  'MKL_NUM_THREADS=1', 'HF_HOME=/models', 'HF_HUB_OFFLINE=1', 'TRANSFORMERS_OFFLINE=1'):
        command += ['--env', value]
    command += ['--entrypoint', 'python', identity, '-I', '-B', '-m', 'uvicorn',
                'reranker_api:app', '--app-dir', '/app', '--host', '0.0.0.0',
                '--port', '8082', '--workers', '1', '--no-access-log']
    container = commands.output(command).strip()
    if not re.fullmatch('[0-9a-f]{64}', container):
        raise ValueError('detached image did not return a container identity')
    try:
        deadline = monotonic()+900
        health = None
        while monotonic() < deadline:
            try:
                health = _json(commands.output(['docker', 'exec', container, 'python', '-I', '-B', '-c', HEALTH_PROBE],
                                               timeout=min(10, max(0.01, deadline-monotonic()))))
                if isinstance(health, dict) and health.get('status') == 'ok':
                    break
            except subprocess.SubprocessError:
                pass
            sleep(min(2, max(0, deadline-monotonic())))
        else:
            raise ValueError('reranker health deadline exceeded (900 seconds)')
        responses = _json(commands.output(['docker', 'exec', container, 'python', '-I', '-B', '-c', SEARCH_PROBE,
                                          json.dumps(QUERIES), json.dumps(DOCUMENTS)], timeout=12*905))
        if not isinstance(responses, list) or len(responses) != 12 or any(not isinstance(r, dict) for r in responses):
            raise ValueError('twelve measured rerank responses required')
        for response in responses:
            if response.get('model') != 'jina-reranker-v3.5' or not isinstance(response.get('results'), list) or len(response['results']) != 5:
                raise ValueError('complete five-document rerank response required')
            indices = []
            for result in response['results']:
                if not isinstance(result, dict):
                    raise ValueError('invalid rerank result')
                index, score = result.get('index'), result.get('relevance_score')
                if (type(index) is not int or index not in range(5) or type(score) not in (int, float)
                        or not math.isfinite(score) or result.get('document') != DOCUMENTS[index]):
                    raise ValueError('invalid rerank index, score, or document')
                indices.append(index)
            if sorted(indices) != list(range(5)):
                raise ValueError('rerank results must cover every document once')
        return {'health': health, 'responses': responses}
    finally:
        # --rm may have already removed an exited server. Keep cleanup failures
        # in the command ledger without discarding the original measurement.
        errors = []
        for action in (['docker', 'stop', '--time', '10', container], ['docker', 'rm', '--force', container]):
            try:
                commands.run(action)
            except subprocess.CalledProcessError as exc:
                if 'No such container' not in (exc.stderr or ''):
                    errors.append(exc)
        if errors:
            raise errors[0]


def qualify(stage, rebuild, owner, command_runner=subprocess.run, *, sleep=time.sleep, monotonic=time.monotonic, workers=None):
    stage, rebuild, owner = (Path(p).absolute() for p in (stage, rebuild, owner))
    for path in (stage, rebuild, owner):
        for parent in (path, *path.parents):
            if parent.is_symlink() or parent.is_junction():
                raise ValueError('linked qualification directory')
    stage, rebuild, owner = (p.resolve() for p in (stage, rebuild, owner))
    if stage.parent != rebuild.parent:
        raise ValueError('stage and rebuild must share their producer work directory')
    work = stage.parent
    if owner.exists() or owner.is_relative_to(work) or work.is_relative_to(owner):
        raise ValueError('new owner directory outside producer work required')
    for root in (stage, rebuild):
        _regular_tree(root)
    for parent in (owner, *owner.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError('linked owner directory')
    receipt, before_rows, after_rows, comparisons = _receipt(stage, rebuild)
    owner.mkdir(parents=True)
    (owner/'evidence').mkdir()
    gates = {'format': rebuild_tool.GATE_FORMAT, 'roles': {}}
    failures = []
    # Each role is measured independently (its own images, command log and evidence files), so roles run
    # concurrently; results are merged below in the receipt's role order, as a sequential run would write them.
    def run_role(role):
        role_failures = []
        commands = Commands(command_runner)
        identities = {'before': None, 'after': None}
        load_error = None
        for side, archive, row in (
                ('before', stage/'payload'/before_rows[role]['path'], before_rows[role]),
                ('after', rebuild/'images'/(role+'.tar'), after_rows[role])):
            try:
                identities[side] = rebuild_tool._loaded(commands.run, archive, row, receipt['source_date_epoch'])
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                load_error = type(exc).__name__+': '+str(exc)
        load_commands = list(commands.rows)
        checks = CHECKS + (('exact_search_equal',) if role == 'reranker' else ())
        gate = {key: comparisons[role][key] for key in ('before_archive_sha256', 'after_archive_sha256')}
        gate['checks'] = {}
        names, import_maps = None, {}
        for check in checks:
            start = len(commands.rows)
            measured = {'before': None, 'after': None}
            error, passed = None, False
            try:
                if load_error and check != 'native_dependency_files_equal':
                    raise ValueError('image identity unmeasurable: '+load_error)
                if check == 'abi':
                    def measure(side):
                        measured[side] = _probe(commands, identities[side], ABI_PROBE)
                        _abi(measured[side])
                    _both(measured, measure)
                    passed = measured['before'] == measured['after']
                elif check == 'imports':
                    names = _names(_probe(commands, identities['before'], IMPORT_NAMES_PROBE))
                    def measure(side):
                        measured[side] = _probe(commands, identities[side], IMPORTS_PROBE, (json.dumps(names),), tmp=True)
                        import_maps[side] = _outcomes(measured[side], names)
                    _both(measured, measure)
                    passed = import_maps['before'] == import_maps['after']
                elif check == 'runtime_compilation':
                    def measure(side):
                        compilers = _probe(commands, identities[side], COMPILER_PROBE)
                        if not isinstance(compilers, list) or compilers != sorted(set(compilers)) or not set(compilers) <= set(COMPILERS):
                            raise ValueError('compiler set measurement required')
                        measured[side] = {'compilers': compilers, 'imports': None, 'baseline_imports': import_maps.get(side)}
                        if names is None or side not in import_maps:
                            raise ValueError('baseline import measurement unavailable')
                        measured[side]['imports'] = _probe(commands, identities[side], IMPORTS_PROBE, (json.dumps(names),))
                        _outcomes(measured[side]['imports'], names)
                    _both(measured, measure)
                    for side in identities:
                        measured[side]['read_only_differs_from_tmp'] = sorted(
                            name for name in names
                            if measured[side]['imports']['outcomes'].get(name) != import_maps[side].get(name))
                    passed = (measured['before']['compilers'] == measured['after']['compilers']
                              and measured['before']['imports']['outcomes'] == measured['after']['imports']['outcomes'])
                elif check == 'retained_inputs_absent':
                    context = rebuild/'contexts'/role
                    if not context.is_dir() and role != 'model_bridge':
                        raise ValueError('role build context absent')
                    context_hashes = sorted({digest(p) for p in context.rglob('*') if p.is_file()})
                    application = rebuild_tool.read_json(rebuild/'qualification'/(role+'-after.json'))['application']
                    arguments = (json.dumps(context_hashes), json.dumps(sorted(row['path'] for row in application)))
                    def measure(side):
                        measured[side] = _probe(commands, identities[side], RETAINED_PROBE, arguments, read_all=True)
                        _retained(measured[side])
                    _both(measured, measure)
                    # A changed retained input is new evidence, even at an old path.
                    passed = {canonical(r) for r in measured['after']} <= {canonical(r) for r in measured['before']}
                elif check == 'native_dependency_files_equal':
                    manifests = {}
                    def measure(side):
                        path = rebuild/'qualification'/(role+'-'+side+'.json')
                        measured[side] = {'manifest_sha256': digest(path), 'manifest': rebuild_tool.read_json(path)}
                        if measured[side]['manifest_sha256'] != comparisons[role][side+'_manifest_sha256']:
                            raise ValueError('installed dependency manifest seal differs')
                        manifests[side] = measured[side]['manifest']
                    _both(measured, measure)
                    equality = rebuild_tool.dependency_equality(manifests['before'], manifests['after'])
                    measured['equality'] = equality
                    passed = equality == comparisons[role]['equality']
                else:
                    pin = rebuild_tool.read_json(stage/'pins.json')['models']['reranker']
                    revision = pin['revision']
                    if pin['id'] != MODEL or not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{40}', revision):
                        raise ValueError('immutable reranker model pin required')
                    snapshot = stage/'payload/models/reranker/models--jinaai--jina-reranker-v3.5/snapshots'/revision
                    if not snapshot.is_dir():
                        raise ValueError('reranker snapshot absent')
                    refs = owner/'reranker-refs'
                    refs.mkdir()
                    refs.chmod(0o755)
                    for name in ('main', revision):
                        (refs/name).write_bytes(revision.encode('ascii'))
                        (refs/name).chmod(0o644)
                    def measure(side):
                        measured[side] = _search(commands, identities[side], snapshot, refs, sleep, monotonic)
                    _both(measured, measure)
                    passed = canonical(measured['before']['responses']) == canonical(measured['after']['responses'])
                if not passed:
                    error = 'measured before/after values do not satisfy '+check
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                error = type(exc).__name__+': '+str(exc)
            evidence = dict(format=FORMAT, role=role, check=check, image_identities=identities,
                            before_archive_sha256=gate['before_archive_sha256'],
                            after_archive_sha256=gate['after_archive_sha256'],
                            commands=load_commands+commands.rows[start:], measured=measured,
                            result=passed, error=error)
            if check in ('imports', 'runtime_compilation'):
                evidence['import_names'] = names
            path = owner/'evidence'/(role+'-'+check+'.json')
            rebuild_tool.write_json(path, evidence)
            gate['checks'][check] = {'pass': passed, 'path': 'evidence/'+path.name, 'sha256': digest(path)}
            if not passed:
                role_failures.append(role+'/'+check+': '+str(error))
        return gate, role_failures

    with ThreadPoolExecutor(max_workers=workers or max(1, len(receipt['roles']))) as pool:
        results = list(pool.map(run_role, receipt['roles']))
    for role, (gate, role_failures) in zip(receipt['roles'], results):
        gates['roles'][role] = gate
        failures.extend(role_failures)
    gates_path = owner/'ALPHA2-REBUILD-GATES.json'
    if not failures:
        rebuild_tool.write_json(gates_path, gates)
    return {'pass': not failures, 'failures': failures, 'gates': str(gates_path) if not failures else None}


def main(argv=None, command_runner=subprocess.run):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('stage', 'rebuild', 'owner-dir'):
        parser.add_argument('--'+name, type=Path, required=True, help=name+' directory.')
    args = parser.parse_args(argv)
    try:
        result = qualify(args.stage, args.rebuild, args.owner_dir, command_runner)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print('Qualification refused: '+str(exc), file=sys.stderr)
        return 2
    for failure in result['failures']:
        print('Qualification failed: '+failure, file=sys.stderr)
    if result['pass']:
        print('All measured qualification checks passed: '+result['gates'])
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
