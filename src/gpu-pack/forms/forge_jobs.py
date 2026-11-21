#!/usr/bin/env python3
"""Run and recover portable Forge jobs on the recorded Docker installation.

Usage: forge_jobs.py --data-root ROOT convert JOB pymupdf|production|mineru [OPTIONS]
Usage: forge_jobs.py --data-root ROOT recover
Usage: forge_jobs.py --data-root ROOT build|bundle BUILDER_ARGUMENTS...
Job inputs and output live below ROOT/jobs and ROOT/builder. Builder arguments
refer to container paths below /var/lib/deep32-forge, as in builder-job.sh.
"""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

_spec = importlib.util.spec_from_file_location('forge_job_lifecycle', Path(__file__).with_name('forge_docker_role.py'))
forge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(forge)
common = forge.common
JOB = re.compile(r'[a-z0-9][a-z0-9_-]{0,39}')
MODELS = ('embeddings', 'reranker')
RECOVERY_SCHEMA = 'deep32.forge-conversion-recovery/1'


class Interrupted(BaseException):
    def __init__(self, code):
        self.code = code


class Jobs:
    def __init__(self, lifecycle):
        self.lifecycle = lifecycle
        self.root = lifecycle.root
        self.record = self.root / 'conversion-recovery.json'

    def runtime(self, allow_load=False, required_roles=()):
        """Validate the retained input and Compose identity before a job write."""
        life = self.lifecycle
        data = life.state()
        release = self.root / 'releases' / data['release']
        life.kit = release / 'kit'
        if hasattr(life, 'runtime_inputs'):
            payload, config, images = life.runtime_inputs(data, allow_load=allow_load)
        else:
            # Compatibility for legacy lifecycle implementations. A failure in
            # installed-input verification never falls back to external media.
            media = common.read_json(release / 'media.json')
            payload = common.absolute_path(media['payload'])
            provision = common.module('forge_job_provision', life.kit / 'forms/provision.py')
            with common.progress('recorded job payload verification', life.emit):
                identity = provision.verified_payload(payload)
            if (identity != media['identity'] or hashlib.sha256(
                    (identity + provision.digest(life.kit / 'pins.json')).encode('ascii')).hexdigest() != data['release']):
                raise common.Refusal('Restore the recorded verified GPU media and runtime pins before running a job.')
            config = common.read_json(life.kit / 'pins.json')
            forge.identities(self.root, life.kit, committed=True)
            images = life.images(payload, config, allow_load=allow_load)
        if any(role not in images for role in required_roles):
            # optional ingestion refusal; no prompt or UI choice.
            raise common.Refusal('Use signed Forge media containing the ingestion add-on before running this job.', 2)
        expected = forge.prepare_config(life.kit, payload, self.root, images, config, data['settings'])
        if common.read_json(release / 'compose.json') != expected:
            raise common.Refusal('Restore the retained Forge Compose configuration before running a job.')
        life.compose(data, 'config', '--quiet')
        return data, config

    def pending(self):
        common.safe_path(self.record)
        return self.record.exists()

    def unchanged(self, data):
        if self.lifecycle.state() != data:
            raise common.Refusal('The Forge installation changed during job preflight; retry the same action.', 11)

    def recovery(self, data):
        value = common.read_json(self.record)
        if (not isinstance(value, dict) or set(value) != {'schema', 'install_id', 'job', 'services'} or
                value['schema'] != RECOVERY_SCHEMA or value['install_id'] != data['install_id'] or
                not isinstance(value['job'], str) or JOB.fullmatch(value['job']) is None or
                not isinstance(value['services'], list) or any(not isinstance(item, str) for item in value['services']) or
                value['services'] != sorted(set(value['services'])) or
                any(item not in MODELS for item in value['services'])):
            raise common.Refusal('Restore the recorded converter recovery state before running another job.', 3)
        return value

    def restore(self, data):
        """Remove only our named converter, then restore exactly the saved models."""
        saved = self.recovery(data)
        life = self.lifecycle
        name = 'deep32-convert-' + data['install_id'] + '-' + saved['job']
        life.call(['docker', 'info'], 'converter recovery', 'restore Docker and run recover before another job.', 30)
        found = life.call(['docker', 'container', 'ls', '--all', '--filter', 'name=^/' + name + '$',
                           '--format', '{{.Names}}'], 'converter ownership', 'restore Docker and run recover.', 30).strip()
        if found:
            if found != name:
                raise common.Refusal('An unexpected container name conflicts with this converter; inspect it before recovery.', 3)
            owner = life.call(['docker', 'container', 'inspect', '--format',
                              '{{index .Config.Labels "com.docker.compose.project"}}', name],
                             'converter ownership', 'inspect the conflicting container before recovery.', 30).strip()
            if owner != 'deep32-' + data['install_id']:
                raise common.Refusal('A container from another project conflicts with this converter; do not remove it.', 3)
            life.call(['docker', 'container', 'rm', '-f', name], 'converter shutdown',
                      'stop the owned converter and run recover before another job.', 30)
        if saved['services']:
            life.compose(data, 'up', '-d', '--pull', 'never', '--no-build', *saved['services'])
        self.record.unlink()

    def convert(self, argv):
        parser = argparse.ArgumentParser(prog='forge_jobs.py convert', allow_abbrev=False)
        parser.add_argument('job')
        parser.add_argument('backend', choices=('pymupdf', 'production', 'mineru'))
        parser.add_argument('--tier', choices=('standard', 'advanced'))
        parser.add_argument('--page-range')
        args = parser.parse_args(argv)
        if JOB.fullmatch(args.job) is None or args.backend != 'mineru' and (args.tier is not None or args.page_range is not None):
            raise common.Refusal('Select a valid job identifier and backend-specific conversion options.', 2)
        if args.page_range is not None and re.fullmatch(r'\d+(?:-\d+)?', args.page_range) is None:
            raise common.Refusal('Select a page or inclusive page range for the MinerU job.', 2)
        job = self.root / 'jobs' / args.job
        common.safe_path(job)
        common.safe_path(job / 'output')
        if not job.is_dir() or (job / 'output').exists():
            raise common.Refusal('Supply a new private job directory without an output directory.', 2)
        inputs = []
        for name in ('input.pdf', 'input.html', 'input.epub', 'input.mib.json'):
            path = job / name
            common.safe_path(path)
            if path.is_file():
                inputs.append(name)
        if len(inputs) != 1 or args.backend != 'production' and inputs[0] != 'input.pdf':
            raise common.Refusal('Supply exactly one supported input in the private job directory.', 2)
        extra = []
        if args.backend == 'production':
            common.safe_path(job / 'job.json')
            if not (job / 'job.json').is_file():
                raise common.Refusal('Supply the production converter job.json in the same job directory.', 2)
            extra += ['--job', '/jobs/' + args.job + '/job.json']
            lexicon = job / 'lexicon'
            common.safe_path(lexicon)
            if lexicon.exists():
                if not lexicon.is_dir():
                    raise common.Refusal('Use a private lexicon directory for this job.', 2)
                for path in sorted(lexicon.rglob('*')):
                    common.safe_path(path)
                extra += ['--lexicon', '/jobs/' + args.job + '/lexicon']
        if args.backend == 'mineru':
            extra += ['--tier', args.tier or 'advanced']
            if args.page_range is not None:
                extra += ['--page-range', args.page_range]
        data, _ = self.runtime(required_roles=('CONVERTER_IMAGE',))
        with common.installation_lock(self.root):
            self.unchanged(data)
            if self.pending():
                raise common.Refusal('Run recover for the interrupted converter before starting another job.', 3)
            hold = self.root / 'status/gpu-hold'
            common.safe_path(hold)
            if hold.exists():
                raise common.Refusal('Release the operator GPU hold before running a converter job.', 4)
            raw = self.lifecycle.compose(data, 'ps', '--status', 'running', '--services')
            running = sorted(set(raw.splitlines()) & set(MODELS))
            common.atomic_json(self.record, dict(schema=RECOVERY_SCHEMA, install_id=data['install_id'],
                                                job=args.job, services=running))
            try:
                self.lifecycle.compose(data, 'stop', *MODELS)
                name = 'deep32-convert-' + data['install_id'] + '-' + args.job
                self.lifecycle.compose(data, '--profile', 'conversion', 'run', '--rm', '--no-deps', '--pull',
                    'never', '--name', name, 'converter', '--backend', args.backend,
                    '--input', '/jobs/' + args.job + '/' + inputs[0], '--output', '/jobs/' + args.job + '/output',
                    *extra, timeout=3600)
            finally:
                self.restore(data)

    def recover(self, argv):
        if argv:
            raise common.Refusal('Run recover without job arguments.', 2)
        # Establish ownership before creating the lock file, then revalidate all
        # state under that same lock before a signed image can be repaired.
        initial = self.lifecycle.state()
        with common.installation_lock(self.root):
            self.unchanged(initial)
            if not self.pending():
                raise common.Refusal('No interrupted converter recovery record was found.', 2)
            self.recovery(initial)
            data, _ = self.runtime(allow_load=True)
            if data != initial:
                raise common.Refusal('The Forge installation changed during recovery; retry the same action.', 11)
            self.unchanged(data)
            self.restore(data)

    def builder(self, verb, argv):
        if not argv:
            raise common.Refusal('Supply the existing builder ' + verb + ' arguments using container paths.', 2)
        data, config = self.runtime(required_roles=('BUILDER_IMAGE', 'QDRANT_IMAGE'))
        if 'builder' not in config:
            raise common.Refusal('Use signed Forge media containing the builder and Qdrant images.', 2)
        with common.installation_lock(self.root):
            self.unchanged(data)
            if self.pending():
                raise common.Refusal('Recover the interrupted converter before running a builder job.', 3)
            if verb == 'build':
                hold = self.root / 'status/gpu-hold'
                common.safe_path(hold)
                if hold.exists():
                    raise common.Refusal('Release the operator GPU hold before starting a builder job.', 4)
                self.lifecycle.compose(data, '--profile', 'builder', 'up', '-d', '--pull', 'never',
                                       '--no-build', 'qdrant', *MODELS)
                self.lifecycle.compose(data, '--profile', 'builder', 'run', '--rm', '--no-deps', '--pull',
                                       'never', '--entrypoint', 'python', 'builder', '-I', '-B',
                                       '/opt/deep32/builder/wait_services.py', timeout=600)
            self.lifecycle.compose(data, '--profile', 'builder', 'run', '--rm', '--no-deps', '--pull',
                                   'never', 'builder', verb, *argv, timeout=3600)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--data-root', required=True)
    parser.add_argument('action', choices=('convert', 'recover', 'build', 'bundle'))
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if (os.name == 'posix' and os.geteuid() != 0 or
            os.name == 'nt' and not ctypes.windll.shell32.IsUserAnAdmin()):
        parser.exit(10, 'Run Forge jobs as the installation administrator on its Docker host.\n')
    try:
        root = common.absolute_path(args.data_root)
        life = forge.Lifecycle(root, Path(__file__).resolve().parents[1])
        jobs = Jobs(life)
        def interrupted(signum, frame):
            raise Interrupted(130 if signum == signal.SIGINT else 143)
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, interrupted)
        if args.action in ('build', 'bundle'):
            jobs.builder(args.action, args.arguments)
        else:
            getattr(jobs, args.action)(args.arguments)
    except common.Refusal as exc:
        parser.exit(exc.code, str(exc) + '\n')
    except Interrupted as exc:
        parser.exit(exc.code, 'Forge job interrupted; run recover if its recovery record remains.\n')
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        parser.exit(1, 'The Forge job failed; restore its verified inputs and recover the recorded converter state before retrying.\n')


if __name__ == '__main__':
    main()
