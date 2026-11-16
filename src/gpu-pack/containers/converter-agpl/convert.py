# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jeffrey A. Cummings
"""Separate converter CLI. Production adapter ABI: adapter.py convert --input SRC --output DIR --job JOB.json [--lexicon DIR].
A production job exits 0, or 10 plus the adapter's exit code, so that the job script's own exit codes 2, 3 and 4 keep their meaning.
"""
import argparse
import multiprocessing as mp
from pathlib import Path
import subprocess
import sys

ADAPTER = '/app/production-converter/adapter.py'


def main(argv=None, jobs_root=Path('/jobs')):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--backend', choices=['pymupdf', 'production', 'mineru'], required=True)
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--tier', choices=['standard', 'advanced'], default='advanced')
    ap.add_argument('--page-range')
    ap.add_argument('--job')
    ap.add_argument('--lexicon')
    args = ap.parse_args(argv)
    for name in (args.input, args.output, args.job, args.lexicon):
        if name is not None and not Path(name).resolve().is_relative_to(jobs_root.resolve()):
            ap.error('input, output, job and lexicon must be under /jobs')
    if args.backend != 'production' and (args.job or args.lexicon):
        ap.error('--job and --lexicon belong to the production backend')
    if args.backend == 'production' and not args.job:
        ap.error('the production backend needs --job')
    if args.backend == 'mineru':
        from worker import convert
        convert(args.input, args.output, args.tier, args.page_range)
    elif args.backend == 'production':
        result = subprocess.run([sys.executable, '-I', '-B', ADAPTER, 'convert', '--input', args.input, '--output', args.output, '--job', args.job] + (['--lexicon', args.lexicon] if args.lexicon else []))
        return 0 if result.returncode == 0 else 10 + result.returncode if 1 <= result.returncode <= 9 else 19
    else:
        from chain import convert
        convert(args.input, args.output)
    return 0


if __name__ == '__main__':
    mp.freeze_support()
    raise SystemExit(main())
