"""Offline C10 prerequisite fixtures; no host probe, service or network command runs."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
BASH=r'C:/Program Files/Git/bin/bash.exe'
PS=r'C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
SPEC=json.loads((ROOT/'install/prerequisites.json').read_text())


def windows_split_bridge(work):
    """Use the real C10 evaluator with synthetic native and signed observations."""
    work.mkdir(parents=True)
    inventory = (ROOT / 'install/deep32-inventory.ps1').read_text(encoding='utf-8')
    locations = re.search(r'function Get-PrDesktopLocations\(.*?\n\}', inventory, re.S)[0]
    discovery = work / 'desktop-locations.ps1'
    discovery.write_text('''param([string]$Local,[string]$System)
$ErrorActionPreference='Stop'
'''+locations+'''
Get-PrDesktopLocations $Local $System | ConvertTo-Json -Depth 5 -Compress
''', encoding='ascii')
    local = work / 'local-app-data'
    system = work / 'program-files'
    layout = (local / 'Programs/DockerDesktop', system / 'Docker/Docker')
    shell = [PS,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File']

    def locate():
        result = subprocess.run(shell+[str(discovery),str(local),str(system)],
                                capture_output=True,text=True,check=True,timeout=30)
        return json.loads(result.stdout)

    assert locate() == {'present':False,'clients':[]}
    clients = []
    for root in layout:
        client = root / 'resources/bin/docker.exe'
        client.parent.mkdir(parents=True)
        client.touch()
        (root / 'Docker Desktop.exe').touch()
        clients.append(str(client))
        assert locate() == {'present':True,'clients':clients}
    entry = '\ntry {\n    if($CheckPrereqs){$rc=Invoke-Prerequisites}'
    replacement = '''
function Get-PrFacts {
    return Read-InventoryJson ([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'native.json')))
}
'''+entry
    assert inventory.count(entry) == 1
    test_script = work / 'inventory.ps1'
    test_script.write_text(inventory.replace(entry, replacement), encoding='utf-8')
    (work / 'prerequisites.json').write_bytes((ROOT / 'install/prerequisites.json').read_bytes())
    stage = work / ('.settings-' + 'a' * 32)
    stage.mkdir()
    proof_path = stage / 'forge-evidence.json'
    native_path = work / 'native.json'
    plan = stage / 'plan.json'
    plan_data = {'schema':'deep32.install-plan.v1','identity':{
        'journey':'J1','form':'docker','platform':'windows','docker_role':'forge',
        'tailnet_sha256':'b'*64,'media_components':[1,2,3],
        'desktop_isolation_confirmed':True,'docker_storage_path':str(work),
    }}
    native = {'os':{'family':'windows','edition':'pro','version':'24H2'},'prerequisites':{
        row['id']:({'state':'met','version':row['minimum_version']} if row.get('minimum_version') else True)
        for row in SPEC['checks']}}
    verified = {'gpu.driver':{'state':'met','version':'810.2'}, 'gpu.ports':True,
                'platform.recipe':'met', 'windows.docker-isolation':True}
    for key in verified:
        native['prerequisites'].pop(key)
    native_path.write_text(json.dumps(native), encoding='ascii')
    large = 9007199254740995
    proof = {'prerequisites':verified,'storage_plan':[
        {'filesystem_id':'synthetic-disk','needed_bytes':large,'free_bytes':large+2},
        {'filesystem_id':'synthetic-disk','needed_bytes':2,'free_bytes':large+3},
    ]}
    acl = work / 'protect.ps1'
    acl.write_text('''param([string]$Getter,[string]$Stage)
$ErrorActionPreference='Stop'
. $Getter
Set-Deep32PrivatePath -Path $Stage -Directory
foreach($name in @('plan.json','forge-evidence.json')) {
    $target=Join-Path $Stage $name
    $stream=[IO.File]::Open($target,'CreateNew','Write','None')
    $stream.Dispose()
    Set-Deep32PrivatePath -Path $target
}
''', encoding='ascii')
    subprocess.run(shell+[str(acl),str(ROOT/'src/release-tools/deep32-get.ps1'),str(stage)],
                   check=True,capture_output=True,timeout=30)
    plan.write_text(json.dumps(plan_data), encoding='ascii')
    proof_path.write_text(json.dumps(proof), encoding='ascii')

    def run(extra=(), expected=0, role='gpu-host', text=False):
        result = subprocess.run(shell+[str(test_script),'-Journey','J1','-Role',role,
                                '-CheckPrereqs',*(() if text else ('-Format','json')),*extra],
                                capture_output=True,text=True,timeout=30)
        assert result.returncode == expected,(result.returncode,result.stdout,result.stderr)
        if text:return result.stdout.splitlines()
        return json.loads(result.stdout) if result.stdout else None

    run(expected=10)
    args = ['-InternalForgeEvidence',str(proof_path)]
    accepted = run(args)
    assert accepted['storage_plan'] == [{'filesystem':'filesystem-1','needed_bytes':large+2,'free_bytes':large+2}]
    # An owner waiver adds one plain line for the person at the terminal and changes no check.
    notice = ('Note: this host type is accepted for 0.1.0-alpha.3 under an owner waiver dated 2026-11-18 '
              '(recipe synthetic-waived-recipe). I have not yet run a full install on it; '
              'FORGE-PREREQUISITES.json lists what was not run.')
    def prove(**changes):
        proof_path.write_text(json.dumps(dict(proof,**changes)), encoding='ascii')
    plain = run(args,text=True)
    assert plain[-1].startswith('Prerequisites passed for gpu-host') and 'platform.recipe=met: ' in '\n'.join(plain),plain
    prove(notice=notice)
    waived = run(args,text=True)
    assert waived == plain[:-1]+[notice,plain[-1]],waived
    assert run(args) == accepted
    prove(notice=notice,prerequisites=dict(verified,**{'gpu.ports':False}))
    refused = run(args,10,text=True)
    assert notice in refused and any(line.startswith('gpu.ports=missing: ') for line in refused),refused
    prove(notice=notice,prerequisites=dict(verified,**{'platform.recipe':'missing'}))
    assert notice not in run(args,10,text=True)
    prove(notice=notice,storage_plan=[{'filesystem_id':'synthetic-disk','needed_bytes':9,'free_bytes':8}])
    short = run(args,10,text=True)
    assert notice in short and any(line.startswith('storage.plan=missing: ') for line in short),short
    for unsafe in ('', 'x'*301, 'two\nlines', 'caf'+chr(233), 7, None, ['Note'], {'text':'Note'}):
        prove(notice=unsafe)
        assert run(args,10) is None,unsafe
    prove(note=notice)
    assert run(args,10) is None
    prove(notice=notice,reviewed=True)
    assert run(args,10) is None
    prove()
    assert run(args,text=True) == plain
    print('PASS an owner waiver prints one line on Windows and replaces no mandatory check')
    native['prerequisites']['host.reboot-clear'] = False
    native_path.write_text(json.dumps(native), encoding='ascii')
    refused = run(args,10)
    assert next(row for row in refused['prerequisites'] if row['id']=='host.reboot-clear')['state']=='missing'
    run(args,2,role='agent')
    run(args+['--facts',str(native_path)],2)
    verified['host.reboot-clear'] = True
    proof_path.write_text(json.dumps(proof), encoding='ascii')
    assert run(args,10) is None
    print('PASS protected native Windows Forge proof integrates without replacing other mandatory checks')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--work',type=Path,required=True);args=ap.parse_args()
    args.work.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='windows-split-bridge-', dir=args.work) as scratch:
        windows_split_bridge(Path(scratch) / 'case')
    passes=0
    def check(name,facts,journey='J2',role='gpu-host',expected=0,validate=None,extra=None):
        nonlocal passes
        path=args.work/(name+'.json');path.write_text(json.dumps({'facts':facts}),encoding='ascii')
        values=[]
        for impl,cmd in [('sh',[BASH,str(ROOT/'install/deep32-inventory.sh')]),('ps1',[PS,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'install/deep32-inventory.ps1')])]:
            argv=['--journey',journey,'--role',role,'--check-prereqs','--format','json','--facts',str(path)]+(extra or [])
            r=subprocess.run(cmd+argv,capture_output=True,text=True,timeout=30)
            assert r.returncode==expected,(name,impl,r.returncode,r.stdout,r.stderr)
            assert not r.stderr,(name,impl,r.stderr)
            doc=json.loads(r.stdout)
            assert doc['schema']=='deep32.inventory.v2'
            if validate:validate(doc)
            del doc['impl'];values.append(doc)
        assert values[0]==values[1],(name,'parity',values)
        passes+=2;print('PASS',name)
        return values[0]
    def fixture(family='linux',edition='ubuntu',version='24.04'):
        return {'os':{'family':family,'edition':edition,'version':version,'arch':'x64'},
                'prerequisites':{c['id']:({'state':'met','version':c['minimum_version']} if c.get('minimum_version') else True) for c in SPEC['checks']},
                'storage_plan':[{'filesystem_id':'synthetic-private-path-A','needed_bytes':50,'free_bytes':100}]}
    def state(doc,id,want,required=True):
        row=next(c for c in doc['prerequisites'] if c['id']==id)
        assert row['required']==required and row['state']==want,(id,row,want,required)
        if required and want!='met':assert row['fix'] and row['code'].endswith(want.upper())
    base=fixture()
    check('j2-linux-gpu',base)
    check('j1-windows-ws',fixture('windows','pro','24H2'),'J1','workstation-host')
    check('j1-windows-gpu',fixture('windows','home','24H2'),'J1','gpu-host',validate=lambda d:state(d,'windows.edition','unknown',False))
    # containerd is checked on Linux GPU hosts only (scope linux-gpu); Docker Desktop manages its own.
    check('j1-windows-gpu-no-containerd',fixture('windows','home','24H2'),'J1','gpu-host',validate=lambda d:state(d,'docker.containerd','unknown',False))
    check('j2-windows-ws',fixture('windows','pro','24H2'),'J2','workstation-host')
    check('j3-linux-all',fixture('linux','debian','13'),'J3','all',validate=lambda d:(assert_roles(d,['gpu-host'])))
    # Forge must not require an additional engine/gateway/importer payload.
    for journey, family in [('J1','windows'), ('J2','linux'), ('J4','linux')]:
        f=fixture(family,'home' if family=='windows' else 'ubuntu','24H2' if family=='windows' else '24.04')
        f['prerequisites']['full.payload']=False
        check(journey+'-forge-no-full-payload',f,journey,'gpu-host',validate=lambda d:state(d,'full.payload','unknown',False))
    for family in ('linux','windows'):
        f=fixture(family,'home' if family=='windows' else 'ubuntu','24H2' if family=='windows' else '24.04')
        f['prerequisites']['full.payload']=False
        check('j3-'+family+'-full-needs-full-payload',f,'J3','gpu-host',10,lambda d:state(d,'full.payload','missing'))
    check('j4-linux-all',base,'J4','all')
    check('j5-linux-ws',base,'J5','workstation-host',validate=lambda d:state(d,'gpu.driver','unknown',False))
    check('j5-windows-ws',fixture('windows','pro','24H2'),'J5','workstation-host')
    check('j3-remote-agent',fixture('windows','home','24H2'),'J3','agent',validate=lambda d:state(d,'full.payload','unknown',False))
    for id in ['download.gpg','download.python','host.reboot-clear','host.tailscale-daemon','host.tailscale-signed-in','gpu.release-envelope','gpu.driver','gpu.capability','gpu.memory','docker.server','docker.compose','docker.nvidia-runtime','docker.containerd','platform.recipe']:
        for value,want in [(False,'missing'),(None,'unknown')]:
            f=copy.deepcopy(base);f['prerequisites'][id]=value
            check(id.replace('.','-')+'-'+want,f,expected=10,validate=lambda d,id=id,want=want:state(d,id,want))
    for id,version in [('download.python','3.9.99'),('docker.server','19.2.99'),('docker.compose','2.29.99')]:
        f=copy.deepcopy(base);f['prerequisites'][id]={'state':'met','version':version}
        check(id+'-below',f,expected=10,validate=lambda d,id=id:state(d,id,'missing'))
    for id in ['download.python','docker.server','docker.compose']:
        f=copy.deepcopy(base);f['prerequisites'][id]=True
        check(id+'-no-version',f,expected=10,validate=lambda d,id=id:state(d,id,'unknown'))
    f=fixture('windows','home','24H2');check('home-no-hyperv',f,'J1','workstation-host',10,lambda d:state(d,'windows.edition','missing'))
    check('j3-no-workstation',fixture('linux','debian','13'),'J3','workstation-host',10)
    check('j5-no-gpu',base,'J5','gpu-host',10)
    check('generic-distro-docker',fixture('linux','debian','13'),'J2','gpu-host')
    check('unsupported-workstation-distro',fixture('linux','debian','13'),'J4','workstation-host',10)
    for id in ('windows.docker-desktop','windows.docker-wsl2','windows.docker-gpu','windows.docker-isolation','docker.server','docker.compose','docker.nvidia-runtime'):
        for value,want in ((False,'missing'),(None,'unknown')):
            f=fixture('windows','home','24H2');f['prerequisites'][id]=value
            check(id+'-windows-'+want,f,'J1','gpu-host',10,lambda d,id=id,want=want:state(d,id,want))
    # Linux engine/runtime evidence says nothing about unrelated Desktop distributions.
    f=fixture('windows','pro','24H2');del f['prerequisites']['windows.docker-isolation']
    check('desktop-engine-is-not-isolation',f,'J1','gpu-host',10,lambda d:state(d,'windows.docker-isolation','unknown'))
    check('desktop-isolation-not-needed-on-windows-vm',fixture('windows','pro','24H2'),'J2','workstation-host',validate=lambda d:state(d,'windows.docker-isolation','unknown',False))
    check('desktop-isolation-not-needed-on-linux',base,validate=lambda d:state(d,'windows.docker-isolation','unknown',False))
    # Measured available RAM, rather than installed RAM or a fallback VM size, bounds a plan.
    gib=1024**3
    memory={'total_bytes':16*gib,'available_bytes':14*gib,'vm_bytes':10*gib,'host_reserve_bytes':4*gib}
    def memory_check(name,plan,want,legacy=True,role='workstation-host'):
        f=fixture('windows','pro','24H2');f['workstation_memory_plan']=plan
        f['prerequisites']['workstation.memory']=legacy
        needed=role=='workstation-host'
        return check(name,f,'J1',role,0 if want=='met' or not needed else 10,
                     lambda d:state(d,'workstation.memory',want if needed else 'unknown',needed))
    memory_check('memory-exact-headroom',memory,'met')
    memory_check('memory-plan-overrides-legacy-missing',memory,'met',False)
    f=copy.deepcopy(memory);f['vm_bytes']=12*gib
    memory_check('memory-16g-installed-is-not-capacity',f,'missing')
    f=copy.deepcopy(memory);f['available_bytes']-=1
    memory_check('memory-one-byte-low',f,'missing')
    f=copy.deepcopy(memory);f['available_bytes']=0
    memory_check('memory-no-available-ram',f,'missing')
    for key,value in [('available_bytes',17*gib),('vm_bytes',0),('host_reserve_bytes',0),
                      ('total_bytes',0),('available_bytes',-1),('vm_bytes','10737418240'),
                      ('host_reserve_bytes',True),('host_reserve_bytes',1.5),
                      ('total_bytes',9007199254740992)]:
        f=copy.deepcopy(memory);f[key]=value
        memory_check('memory-bad-'+key+'-'+str(value),f,'unknown')
    for name,plan in [('null',None),('array',[]),('string','synthetic-private-plan'),
                      ('incomplete',{'total_bytes':16*gib})]:
        memory_check('memory-'+name,plan,'unknown')
    memory_check('memory-not-needed-for-gpu',None,'unknown',role='gpu-host')
    f=copy.deepcopy(memory);f['total_bytes']=9007199254740991;f['available_bytes']=9007199254740991
    f['vm_bytes']=9007199254740990;f['host_reserve_bytes']=2
    memory_check('memory-sum-outside-exact-range',f,'unknown')
    f=fixture('windows','pro','24H2');f['Workstation_memory_plan']=None
    check('memory-field-exact-case',f,'J1','workstation-host',validate=lambda d:state(d,'workstation.memory','met'))
    check('unknown-journey',base,'J6','gpu-host',10)
    f=copy.deepcopy(base);f['storage_plan']=[{'filesystem_id':'same','needed_bytes':50,'free_bytes':99},{'filesystem_id':'same','needed_bytes':50,'free_bytes':100}]
    check('shared-storage-low',f,expected=10,validate=lambda d:(state(d,'storage.plan','missing'),assert_storage(d,100,99)))
    f['storage_plan'][0]['free_bytes']=100;check('shared-storage-boundary',f,validate=lambda d:assert_storage(d,100,100))
    for value in [None,[],[{'filesystem_id':'synthetic','needed_bytes':'100','free_bytes':100}]]:
        f=copy.deepcopy(base);f['storage_plan']=value;check('bad-storage-'+str(passes),f,expected=10,validate=lambda d:state(d,'storage.plan','unknown'))
    f=copy.deepcopy(base);f['prerequisites']['host.tailscale-signed-in']={'state':'malformed','version':'Bearer SYNTHETIC_TEST_SECRET'};f['private']={'token':'SYNTHETIC_TEST_SECRET'}
    check('sanitised',f,expected=10,validate=lambda d:assert_clean(d))
    assert len({c['id'] for c in SPEC['checks']})==len(SPEC['checks'])
    # Execute only the pure daemon-info parser on synthetic data: no Docker/WSL command.
    pstext=(ROOT/'install/deep32-inventory.ps1').read_text()
    parser=re.search(r'function Get-PrDesktopFacts\(\$info\) \{.*?\n\}',pstext,re.S)[0]
    desktop={'OSType':'linux','OperatingSystem':'Docker Desktop','KernelVersion':'6.6.87-microsoft-standard-WSL2',
             'Runtimes':{'nvidia':{}},'Name':'SYNTHETIC_TEST_SECRET','private':'SYNTHETIC_TEST_SECRET'}
    cases=[desktop,dict(desktop,OSType='windows'),dict(desktop,KernelVersion='synthetic-linux'),None,[desktop]]
    casepath=args.work/'synthetic-desktop-info.json';casepath.write_text(json.dumps(cases),encoding='ascii')
    parserpath=args.work/'synthetic-desktop-parser.ps1'
    parserpath.write_text('param([string]$Cases)\n'+parser+'\n$result=New-Object System.Collections.ArrayList\n'
                          '$parsedCases=Get-Content -LiteralPath $Cases -Raw | ConvertFrom-Json\n'
                          'foreach($case in $parsedCases) { [void]$result.Add((Get-PrDesktopFacts $case)) }\n'
                          'ConvertTo-Json -InputObject @($result) -Depth 5 -Compress\n',encoding='ascii')
    r=subprocess.run([PS,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(parserpath),str(casepath)],capture_output=True,text=True,timeout=15)
    assert r.returncode==0 and not r.stderr,(r.returncode,r.stdout,r.stderr)
    parsed=json.loads(r.stdout);assert len(parsed)==5,(r.stdout,r.stderr)
    assert parsed[0]=={'windows.docker-wsl2':True,'windows.wsl-version':True,'docker.nvidia-runtime':True,
                       'windows.docker-gpu':None,'windows.docker-isolation':None}
    for row in parsed[1:3]:
        assert row['windows.docker-wsl2'] is False and row['windows.docker-gpu'] is False
        assert row['windows.docker-isolation'] is None
    assert parsed[3]=={} and parsed[4]=={} and 'SYNTHETIC_TEST_SECRET' not in r.stdout
    print('PASS synthetic Desktop advertisements do not qualify GPU or isolation')
    text=(ROOT/'install/deep32-inventory.sh').read_text()
    awk=re.search(r"VERDICT_AWK <<'AWK'\n(.*?)\nAWK",text,re.S)[1]
    awkpath=args.work/'synthetic-prereq-parser.awk';awkpath.write_text(awk,encoding='utf-8')
    for value,want in [({'BackendState':'Running','Self':{'Online':True,'DNSName':'SYNTHETIC_TEST_SECRET'}},True),({'BackendState':'NeedsLogin'},False)]:
        env=os.environ.copy();env['D32_TS_ONLY']='1'
        r=subprocess.run([r'C:/Program Files/Git/usr/bin/awk.exe','-f',str(awkpath)],input=json.dumps(value),env=env,capture_output=True,text=True,timeout=10)
        assert r.returncode==0 and 'SYNTHETIC_TEST_SECRET' not in r.stdout
        result=json.loads('{"dummy":null'+r.stdout+'}')
        assert result['host.tailscale-signed-in'] is want
    # The exact bounded wrapper is tested with a synthetic delay and no installed host tool.
    wrapper=re.search(r'pr_run\(\) \{.*?\n\}',text,re.S)[0]
    runner=re.search(r'run_to\(\) \(.*?\n\)',text,re.S)[0]
    started=time.monotonic()
    r=subprocess.run([BASH,'-c','have() { command -v "$1" >/dev/null 2>&1; }\n'+runner+'\n'+wrapper+'\npr_run sleep 30'],capture_output=True,text=True,timeout=10)
    assert r.returncode==124 and time.monotonic()-started<8
    for impl,cmd in [('sh',[BASH,str(ROOT/'install/deep32-inventory.sh')]),('ps1',[PS,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'install/deep32-inventory.ps1')])]:
        for argv in [[],['--journey','J1'],['--journey','J1','--role','gpu-host','--format','text','--json'],['--journey','J1','--role','gpu-host','--net'],['bench','quick','--journey','J1','--role','gpu-host']]:
            r=subprocess.run(cmd+argv+['--check-prereqs'],capture_output=True,text=True,timeout=15)
            assert r.returncode==2 and not r.stdout,(impl,argv,r.returncode,r.stdout,r.stderr)
        # Invalid selections must return before any host tool probe.
        r=subprocess.run(cmd+['--journey','J9','--role','gpu-host','--check-prereqs','--format','json'],capture_output=True,text=True,timeout=10)
        assert r.returncode==10 and json.loads(r.stdout)['prerequisites'][0]['code']=='PR_SELECTION_UNSUPPORTED'
    print('PASS',passes,'fixture implementation checks')

def assert_roles(doc,want):assert doc['selected_roles']==want
def assert_storage(doc,needed,free):assert doc['storage_plan']==[{'filesystem':'filesystem-1','needed_bytes':needed,'free_bytes':free}]
def assert_clean(doc):
    text=json.dumps(doc);assert 'SYNTHETIC_TEST_SECRET' not in text and 'synthetic-private-path' not in text

if __name__=='__main__':main()
