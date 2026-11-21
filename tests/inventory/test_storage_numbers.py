"""Offline symbolic storage-number regressions; facts only, no host or network probes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
BASH='C:/Program Files/Git/bin/bash.exe'
PS='C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
WORK=None


class StorageNumbers(unittest.TestCase):
    def run_case(self,terms,free,state,expected=None,code=None):
        rows=','.join('{"filesystem_id":"synthetic","needed_bytes":'+n+',"free_bytes":'+free+'}' for n in terms)
        fixture=WORK/'synthetic-storage.json'
        fixture.write_text('{"facts":{"os":{"family":"linux"},"storage_plan":['+rows+']}}',encoding='ascii')
        values=[]
        for implementation,prefix in [('sh',[BASH,str(ROOT/'install/deep32-inventory.sh')]),
                                      ('ps1',[PS,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'install/deep32-inventory.ps1')])]:
            with self.subTest(implementation=implementation,terms=terms,free=free):
                start=time.monotonic()
                result=subprocess.run(prefix+['--check-prereqs','--journey','J3','--role','gpu-host','--json','--facts',str(fixture)],
                                      cwd=WORK,env=os.environ.copy(),capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,10,result.stdout+result.stderr)
                self.assertEqual(result.stderr,'')
                self.assertEqual(len(result.stdout.splitlines()),1)
                doc=json.loads(result.stdout,parse_int=str,parse_float=str)
                row=next(row for row in doc['prerequisites'] if row['id']=='storage.plan')
                self.assertEqual(row['state'],state)
                self.assertEqual(row['code'],code or 'PR_STORAGE_PLAN_'+state.upper())
                if expected is not None:
                    self.assertEqual(doc['storage_plan'][0]['needed_bytes'],expected)
                else:
                    self.assertEqual(doc['storage_plan'],[])
                self.assertLess(time.monotonic()-start,15)
                del doc['impl'];values.append(doc)
        self.assertEqual(values[0],values[1])

    def test_singletons_and_equal_exponents_beyond_native_integer_ranges(self):
        self.run_case(['1e2147483648'],'1e2147483648','met','1e2147483648')
        self.run_case(['2e2147483648','3e2147483648'],'5e2147483648','met','5e2147483648')
        self.run_case(['9e2147483648','1e2147483648'],'1e2147483649','met','1e2147483649')
        self.run_case(['1e9007199254740993'],'1e9007199254740992','missing','1e9007199254740993')
        self.run_case(['1e123456789012345678901234567890'],'2e123456789012345678901234567890','met','1e123456789012345678901234567890')

    def test_exact_comparison_without_expanding_mantissas(self):
        self.run_case(['12e2147483648'],'119e2147483647','missing','12e2147483648')
        self.run_case(['12e2147483648'],'121e2147483647','met','12e2147483648')
        self.run_case(['1e2147483648','10e2147483647'],'2e2147483648','met','2e2147483648')
        self.run_case(['1e2147483648','1e2147483649'],'11e2147483648','met','11e2147483648')
        self.run_case(['1e9007199254740993','1e9007199254740992'],'11e9007199254740992','met','11e9007199254740992')

    def test_plain_exact_and_integral_decimal_numbers(self):
        self.run_case(['9007199254740993','1'],'9007199254740994','met','9007199254740994')
        self.run_case(['12000e-2'],'120.0','met','120')
        self.run_case(['-0.0e-999999999999999999999999'],'0e999999999999999999999999','met','0')
        self.run_case(['1.000000000000000000000000000000e30'],'1e30','met','1'+'0'*30)

    def test_nonintegral_or_negative_is_unknown_not_resource_unavailable(self):
        self.run_case(['1e-2147483648'],'10','unknown')
        self.run_case(['-1e2147483648'],'10','unknown')
        self.run_case(['0.9999999999999999999999999'],'10','unknown')

    def test_unrepresentable_large_gap_sum_is_distinct_and_bounded(self):
        self.run_case(['1e2147483648','1'],'2e2147483648','unknown',
                      code='PR_STORAGE_PLAN_RESOURCE_UNAVAILABLE')
        self.run_case(['1e65536','1'],'2e65536','unknown',
                      code='PR_STORAGE_PLAN_RESOURCE_UNAVAILABLE')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--work',type=Path,required=True)
    args,remaining=parser.parse_known_args();WORK=args.work;WORK.mkdir(parents=True,exist_ok=True)
    unittest.main(argv=[__file__,*remaining])
