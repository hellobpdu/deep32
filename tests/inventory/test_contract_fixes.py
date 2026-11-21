"""Offline contract regression tests; all generated files live in --work."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[2]
BASH='C:/Program Files/Git/bin/bash.exe'
PS='C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
WORK=None
PSTEXT=(ROOT/'install/deep32-inventory.ps1').read_text()
SHTEXT=(ROOT/'install/deep32-inventory.sh').read_text()

def ps_function(name):
    return re.search(r'^function '+re.escape(name)+r'\b.*?^}\n',PSTEXT,re.M|re.S)[0]

def powershell(script):
    path=WORK/'synthetic-powershell-test.ps1'
    path.write_text("$ProgressPreference='SilentlyContinue'\n"+script,encoding='utf-8')
    result=subprocess.run([PS,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(path)],capture_output=True,text=True,timeout=35)
    assert result.returncode==0 and not result.stderr,(result.returncode,result.stdout,result.stderr)
    return json.loads(result.stdout)

def invoke(impl,args):
    prefix=[BASH,str(ROOT/'install/deep32-inventory.sh')] if impl=='sh' else [PS,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'install/deep32-inventory.ps1')]
    return subprocess.run(prefix+args,capture_output=True,text=True,timeout=25)

class ContractFixes(unittest.TestCase):
    def fixture(self,text,name='synthetic-facts.json'):
        path=WORK/name;path.write_text(text,encoding='ascii');return str(path)

    def test_C10_696_strict_numeric_grammar(self):
        for token in ['01','1e','1.','-01','1e+','NaN','Infinity','1.2.3']:
            path=self.fixture('{"facts":{"cpu":{"threads":'+token+'}}}')
            for impl in ['sh','ps1']:
                result=invoke(impl,['report','--json','--facts',path])
                self.assertEqual((result.returncode,result.stdout),(1,''),(impl,token,result.stderr))

    def test_C10_599_exact_unbounded_storage(self):
        for needed,free,want,number in [('1.5e20','150000000000000000001','met',150000000000000000000),
                                         ('9007199254740993','9007199254740992','missing',9007199254740993),
                                         ('1e100','1e100','met',10**100),
                                         ('12000e-2','120.0','met',120),('-0.0','0e100','met',0),
                                         ('0.999999999999999999999999999999999999','10','unknown',None),
                                         ('-1','10','unknown',None),('1.01','10','unknown',None)]:
            text='{"facts":{"os":{"family":"linux"},"storage_plan":[{"filesystem_id":"synthetic","needed_bytes":'+needed+',"free_bytes":'+free+'}]}}'
            path=self.fixture(text)
            for impl in ['sh','ps1']:
                result=invoke(impl,['--check-prereqs','--journey','J3','--role','gpu-host','--json','--facts',path])
                doc=json.loads(result.stdout)
                row=next(x for x in doc['prerequisites'] if x['id']=='storage.plan')
                self.assertEqual(row['state'],want,(impl,needed,result.stderr))
                if number is not None:self.assertEqual(doc['storage_plan'][0]['needed_bytes'],number)
        # Sum and minimum are exact across a common filesystem, including carries above 2^53.
        path=self.fixture('{"facts":{"os":{"family":"linux"},"storage_plan":[{"filesystem_id":"synthetic","needed_bytes":99999999999999999999,"free_bytes":100000000000000000000},{"filesystem_id":"synthetic","needed_bytes":1.0,"free_bytes":100000000000000000001}]}}')
        for impl in ['sh','ps1']:
            doc=json.loads(invoke(impl,['--check-prereqs','--journey','J3','--role','gpu-host','--json','--facts',path]).stdout)
            self.assertEqual(doc['storage_plan'],[{'filesystem':'filesystem-1','needed_bytes':10**20,'free_bytes':10**20}])

    def test_C10_050_json_stdout_and_error_exceptions(self):
        path=self.fixture('{"facts":{}}')
        for impl in ['sh','ps1']:
            for extra,code in [([],0),(['--matrix',str(WORK/'absent-matrix')],1)]:
                result=invoke(impl,['report','--json','--facts',path]+extra)
                self.assertEqual(result.returncode,code)
                self.assertEqual(len(result.stdout.splitlines()),1)
                self.assertIsInstance(json.loads(result.stdout),dict)
                self.assertNotRegex(result.stdout,r'[\r\n].+\S')
            for args in [['--json','--unknown'],['--unknown','--json'],['--json','--facts'],['--json','report','extra']]:
                result=invoke(impl,args)
                self.assertEqual((result.returncode,result.stdout),(2,''),(impl,args,result.stderr))

    def test_C10_025_026_http_statuses_and_deadline(self):
        functions=ps_function('Get-InventoryHttp')
        script=functions+'''
function New-InventoryHttpRequest($uri) {
    $value=[pscustomobject]@{Method='';UserAgent='';AllowAutoRedirect=$true;Timeout=0;ReadWriteTimeout=0;Status=[int]$uri;Aborted=$false}
    return $value
}
function Start-InventoryHttpRequest($request) {
    $value=[pscustomobject]@{Result=$request.Status}
    $value|Add-Member ScriptMethod Wait {param($timeout) if($this.Result -eq 0){Start-Sleep -Milliseconds $timeout;return $false};return $true}
    return $value
}
function Stop-InventoryHttpRequest($request) {$request.Aborted=$true}
$UserAgent='synthetic';$result=@()
foreach($status in @(200,301,401,403,404,500,503,0)) {$result+=Get-InventoryHttp ([string]$status) 80}
ConvertTo-Json -InputObject $result -Compress
'''
        rows=powershell(script)
        for row in rows[:-1]:self.assertTrue(row['ok']);self.assertGreaterEqual(row['code'],200)
        self.assertFalse(rows[-1]['ok']);self.assertLess(rows[-1]['ms'],160)
        shell=re.search(r'probe_net\(\) \{.*?\n}',SHTEXT,re.S)[0]
        for status in [200,302,401,403,404,500,503]:
            # No executable or socket is used: shell functions emulate wget and its HTTP-error exit.
            script='''have() { [ "$1" = wget ]; }
run_to() { [ "$1" = 8 ] || exit 90; shift; "$@"; }
wget() { printf '  HTTP/1.1 %s Synthetic\\n' "$SYNTHETIC_STATUS" >&2; return 8; }
'''+shell+'\nprobe_net; printf "%s\\n" "$NET_JSON"'
            r=subprocess.run([BASH,'-c',script],env=dict(os.environ,SYNTHETIC_STATUS=str(status)),capture_output=True,text=True,timeout=8)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertTrue(all(row['ok'] and row['code']==status for key,row in json.loads(r.stdout).items() if key!='gh_signed_in'))
        self.assertIn('Get-InventoryHttp $sites[$k] 8000',PSTEXT)
        self.assertIn('run_to 8 curl',SHTEXT)

    def test_C10_027_067_declared_time_limits(self):
        script=ps_function('Get-Ver')+ps_function('Get-ToolVersion')+ps_function('Get-NetFacts')+'''
$script:Limits=@()
function Find-Tool {return 'synthetic-tool.exe'}
function Invoke-Tool {param($Path,$Arguments,$TimeoutMs) $script:Limits+=$TimeoutMs;return @{code=0;out='synthetic version 1.2.3';err=''}}
function Get-InventoryHttp {param($uri,$TimeoutMs) $script:Limits+=$TimeoutMs;return @{ok=$true;code=403;ms=0}}
$version=Get-ToolVersion 'synthetic' @() '--version';$network=Get-NetFacts
@{version=$version;limits=$script:Limits;signed=$network.gh_signed_in}|ConvertTo-Json -Compress
'''
        result=powershell(script)
        self.assertEqual(result['limits'],[15000]+[8000]*5+[20000])
        self.assertEqual(result['version'],'1.2.3');self.assertTrue(result['signed'])
        self.assertIn('Invoke-Tool $gh \'auth status --hostname github.com\' 20000',PSTEXT)
        self.assertIn('Invoke-Tool $path $Arguments 15000',PSTEXT)
        self.assertIn('run_to 15 "$_p"',SHTEXT)
        self.assertIn('run_to 20 gh auth status',SHTEXT)

    def test_C10_069_shell_containment_fails_closed(self):
        runner=re.search(r'run_to\(\) \(.*?\n\)',SHTEXT,re.S)[0]
        script='have() { return 1; }\n'+runner+'\nrequested_tool() { echo LAUNCHED; }; run_to 1 requested_tool'
        result=subprocess.run([BASH,'-c',script],capture_output=True,text=True,timeout=5)
        self.assertEqual((result.returncode,result.stdout),(124,''))
        self.assertNotIn('--map-root-user',runner)
        self.assertIn('--map-current-user --pid --fork --kill-child=KILL',runner)

    def test_C10_069_windows_detached_descendants_and_bystander(self):
        # A detached child closes redirected output and starts a grandchild; the job still owns both.
        marker=WORK/'synthetic-job-descendants.json'
        child=WORK/'synthetic-job-child.ps1'
        child.write_text('''$p=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList '-NoProfile -Command Start-Sleep -Seconds 60' -WindowStyle Hidden -PassThru
@{child=$PID;grandchild=$p.Id}|ConvertTo-Json -Compress|Set-Content -LiteralPath $env:D32_JOB_MARK
Start-Sleep -Seconds 60
''',encoding='ascii')
        launcher=WORK/'synthetic-job-launcher.ps1'
        launcher.write_text("Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File \"'+$env:D32_JOB_CHILD+'\"') -WindowStyle Hidden | Out-Null\nStart-Sleep -Seconds 60\n",encoding='ascii')
        quote=lambda value:"'"+str(value).replace("'","''")+"'"
        script=ps_function('Initialize-InventoryRuntime')+ps_function('Invoke-Tool')+'''
Initialize-InventoryRuntime
$by=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList '-NoProfile -Command Start-Sleep -Seconds 60' -WindowStyle Hidden -PassThru
'''+f"$env:D32_JOB_MARK={quote(marker)};$env:D32_JOB_CHILD={quote(child)}\n"+f"$clock=[Diagnostics.Stopwatch]::StartNew();$value=Invoke-Tool (Join-Path $PSHOME 'powershell.exe') ('-NoProfile -ExecutionPolicy Bypass -File \"'+{quote(launcher)}+'\"') 2200\n"+'''
$elapsed=$clock.ElapsedMilliseconds;$alive=@();$started=$false
if(Test-Path -LiteralPath $env:D32_JOB_MARK){$started=$true;$ids=Get-Content -LiteralPath $env:D32_JOB_MARK -Raw|ConvertFrom-Json;foreach($id in @($ids.child,$ids.grandchild)){try{$p=[Diagnostics.Process]::GetProcessById($id);if(-not $p.WaitForExit(1000)){$alive+=$id;$p.Kill()}}catch{}}}
$byAlive=-not $by.HasExited;$by.Kill()
@{unavailable=($null -eq $value);ms=$elapsed;started=$started;alive=$alive;bystander=$byAlive}|ConvertTo-Json -Compress
'''
        result=powershell(script)
        self.assertTrue(result['unavailable']);self.assertTrue(result['started'],result)
        self.assertEqual(result['alive'],[]);self.assertTrue(result['bystander'])
        self.assertLess(result['ms'],2450,result)

    def test_C10_205_206_disks(self):
        # Execute the exact disk block with synthetic CIM rows only.
        block=PSTEXT[PSTEXT.index('    # disks\n'):PSTEXT.index('    # graphics\n')]
        script=ps_function('Get-GB')+'''
function Get-CimInstance {param($Namespace,$ClassName,$Filter,$ErrorAction)
 if($ClassName -ne 'Win32_LogicalDisk'){return @()}
 if($script:Fail){throw 'synthetic unreadable'}
 foreach($i in @(2,7,1,8,5,4,3,6)){[pscustomobject]@{DeviceID=('V'+$i);Size=([long]$i*1073741824);FreeSpace=1073741824}}
}
'''+block+"\n$first=@($disks);$script:Fail=$true\n"+block+"\n@{disks=$first;unreadable=($null -eq $disks)}|ConvertTo-Json -Depth 5 -Compress\n"
        result=powershell(script)
        self.assertEqual([d['size_gb'] for d in result['disks']],[8,7,6,5,4,3]);self.assertTrue(result['unreadable'])
        probe=re.search(r'probe_linux_disks\(\) \{.*?\n}',SHTEXT,re.S)[0]
        script='have() { return 0; }; df() { return 1; }; R="";DISKS_JSON=null\n'+probe+'\nprobe_linux_disks; printf "%s" "$DISKS_JSON"'
        r=subprocess.run([BASH,'-c',script],capture_output=True,text=True,timeout=5)
        self.assertEqual((r.returncode,r.stdout),(0,'null'))
        root=WORK/'synthetic-diskroot';(root/'proc').mkdir(parents=True,exist_ok=True)
        (root/'proc/mounts').write_text(''.join(f'/dev/synthetic{i} /d{i} ext4 rw 0 0\n' for i in range(1,9)),encoding='ascii')
        preamble='''have() { command -v "$1" >/dev/null 2>&1; }
js() { printf '"%s"' "$1"; }; jn() { printf '%s' "$1"; }; jb() { printf null; }
sanitize_mount() { printf '%s' "$1"; }
TAB=$(printf '\\t');DISKS_JSON=null
df() { printf 'Filesystem Blocks Used Available Capacity Mounted\\n'; for i in 1 2 3 4 5 6 7 8; do printf '/dev/synthetic%s %s 0 100 1%% /d%s\\n' "$i" "$((1048576+i))" "$i"; done; }
'''
        script=preamble+'\nR="$1"\n'+probe+'\nprobe_linux_disks; printf "%s" "$DISKS_JSON"'
        r=subprocess.run([BASH,'-c',script,'synthetic',str(root).replace('\\','/')],capture_output=True,text=True,timeout=8)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual([d['mount'] for d in json.loads(r.stdout)],['/d8','/d7','/d6','/d5','/d4','/d3'])
        # Distinct macOS devices can have equal free space; all still compete for the top six.
        start=SHTEXT.index('    # Distinct local device identities;')
        mac=SHTEXT[start:SHTEXT.index('    # graphics',start)]
        r=subprocess.run([BASH,'-c',preamble+mac+'\nprintf "%s" "$DISKS_JSON"'],capture_output=True,text=True,timeout=8)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual([d['mount'] for d in json.loads(r.stdout)],['/d8','/d7','/d6','/d5','/d4','/d3'])

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--work',required=True,type=Path);args=parser.parse_args()
    WORK=args.work;WORK.mkdir(parents=True,exist_ok=True)
    unittest.main(argv=['inventory-contract-fixes'],verbosity=2)
