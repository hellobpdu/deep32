#!/usr/bin/env python3
"""Run the guarded suite. Usage: run_all.py --tmp DIR"""
import argparse
import faulthandler
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
TESTS = Path(__file__).resolve().parent
sys.path[:0] = [str(TESTS),str(TESTS.parent),str(TESTS.parent/'tools')]
from guard import install

class Result(unittest.TestResult):
    def startTest(self, test):
        super().startTest(test)
        self.started = time.monotonic()
        faulthandler.dump_traceback_later(130,exit=True)

    def stopTest(self, test):
        faulthandler.cancel_dump_traceback_later()
        elapsed = time.monotonic()-self.started
        if elapsed > 120:
            try:
                raise AssertionError('test exceeded 120 seconds')
            except AssertionError:
                self.addFailure(test,sys.exc_info())
        print('%s SECONDS %.3f' % (test.id(),elapsed),flush=True)
        super().stopTest(test)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tmp',required=True,type=Path)
    ap.add_argument('--pattern',default='test_*.py')
    ap.add_argument('--report',type=Path)
    args = ap.parse_args()
    args.tmp.mkdir(parents=True,exist_ok=True)
    tmp = str(args.tmp.resolve())
    os.environ.update(TMP=tmp,TEMP=tmp,TMPDIR=tmp)
    tempfile.tempdir = tmp
    install()
    started = time.monotonic()
    suite = unittest.defaultTestLoader.discover(str(TESTS),pattern=args.pattern)
    result = Result()
    suite.run(result)
    seconds = time.monotonic()-started
    for test,trace in result.failures+result.errors:
        print(test.id()+'\n'+trace,flush=True)
    for test,reason in result.skipped:
        print('SKIP '+test.id()+' '+reason)
    line = 'RAN %d FAILED %d ERRORS %d SKIPPED %d SECONDS %.3f' % (result.testsRun,len(result.failures),len(result.errors),len(result.skipped),seconds)
    print(line,flush=True)
    data = dict(tests_run=result.testsRun,tests_failed=len(result.failures),tests_errors=len(result.errors),tests_skipped=[dict(name=t.id(),reason=r) for t,r in result.skipped],ran=line)
    if args.report:
        args.report.write_text(json.dumps(data,indent=2)+'\n',encoding='ascii')
    return int(not result.wasSuccessful())

if __name__ == '__main__':
    raise SystemExit(main())
