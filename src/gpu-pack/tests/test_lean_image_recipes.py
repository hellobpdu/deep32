#!/usr/bin/env python3
"""Offline recipe contracts; these checks do not claim built-image equivalence."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import unittest

KIT = Path(__file__).resolve().parents[1]
ROLES = ('reranker', 'converter-agpl', 'builder', 'full')


def instructions(text):
    """Read this recipe subset, joining Dockerfile continuations."""
    pending = ''
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        pending += stripped[:-1] + ' ' if stripped.endswith('\\') else stripped
        if stripped.endswith('\\'):
            continue
        opcode, _, body = pending.partition(' ')
        yield opcode.upper(), body.strip()
        pending = ''
    if pending:
        raise ValueError('Unfinished Dockerfile instruction')


def run_parts(body):
    tokens = shlex.split(body)
    options = []
    while tokens and tokens[0].startswith('--'):
        options.append(tokens.pop(0))
    return options, tokens


def copied_layers(text, context):
    """Model committed COPY bytes, including layers later hidden by deletion.

    The context holds tiny synthetic archive markers. RUN bind mounts are not
    COPY instructions, so their inputs are not committed by this model. Actual
    layer, package/file, ABI and GPU equality remain build-host gates.
    """
    layers = []
    for opcode, body in instructions(text):
        if opcode != 'COPY':
            continue
        tokens = shlex.split(body)
        if any(token.startswith('--') for token in tokens):
            raise ValueError('Review new COPY semantics before extending this model')
        sources, destination = tokens[:-1], tokens[-1]
        layer = {}
        for source in sources:
            source = source.rstrip('/')
            for name, data in context.items():
                if source == '.':
                    relative = name
                elif name == source:
                    relative = PurePosixPath(name).name
                elif name.startswith(source + '/'):
                    relative = name[len(source) + 1:]
                else:
                    continue
                layer[str(PurePosixPath(destination) / relative)] = data
        layers.append(layer)
    return layers


def recipe(role):
    return (KIT / 'containers' / role / 'Dockerfile').read_text(encoding='utf-8')


def archive_bytes(layers):
    sentinels = {b'SYNTHETIC WHEEL', b'SYNTHETIC DEB', b'SYNTHETIC SDIST'}
    return [data for layer in layers for data in layer.values() if data in sentinels]


class LeanImageRecipes(unittest.TestCase):
    def test_no_archives_committed_in_any_copy_layer(self):
        context = {'wheelhouse/pinned.whl': b'SYNTHETIC WHEEL',
                   'wheelhouse/jieba.tar.gz': b'SYNTHETIC SDIST',
                   'debs/pinned.deb': b'SYNTHETIC DEB'}
        for role in ROLES:
            with self.subTest(role=role):
                self.assertEqual(archive_bytes(copied_layers(recipe(role), context)), [])

    def test_later_delete_does_not_pass_hidden_wheel_layer(self):
        mutation = 'COPY wheelhouse/ /wheelhouse/\nRUN rm -rf /wheelhouse\n'
        self.assertEqual(archive_bytes(copied_layers(mutation,
            {'wheelhouse/pinned.whl': b'SYNTHETIC WHEEL'})), [b'SYNTHETIC WHEEL'])

    def test_later_delete_does_not_pass_hidden_deb_layer(self):
        mutation = 'COPY debs /debs\nRUN rm -rf /debs\n'
        self.assertEqual(archive_bytes(copied_layers(mutation,
            {'debs/pinned.deb': b'SYNTHETIC DEB'})), [b'SYNTHETIC DEB'])

    def test_broad_context_copy_is_detected(self):
        self.assertEqual(archive_bytes(copied_layers('COPY . /app/\n',
            {'wheelhouse/pinned.whl': b'SYNTHETIC WHEEL'})), [b'SYNTHETIC WHEEL'])

    def test_mount_parser_refuses_unreviewed_copy_flags(self):
        with self.assertRaises(ValueError):
            copied_layers('COPY --from=other /wheels /wheels\n', {})

    def test_package_steps_have_read_only_offline_inputs(self):
        counts = {'reranker': 1, 'converter-agpl': 3, 'builder': 2, 'full': 1}
        for role, count in counts.items():
            steps = [(options, tokens) for op, body in instructions(recipe(role))
                     if op == 'RUN' for options, tokens in [run_parts(body)]
                     if 'install' in tokens or 'dpkg' in tokens]
            self.assertEqual(len(steps), count, role)
            for options, tokens in steps:
                with self.subTest(role=role, command=tokens):
                    self.assertIn('--network=none', options)
                    mounts = [dict(part.split('=', 1) if '=' in part else (part, True)
                                   for part in option[len('--mount='):].split(','))
                              for option in options if option.startswith('--mount=')]
                    archive = 'debs' if 'dpkg' in tokens else 'wheelhouse'
                    mounted = [m for m in mounts if m.get('source') == archive]
                    self.assertEqual(len(mounted), 1)
                    self.assertEqual(mounted[0].get('type'), 'bind')
                    self.assertIs(mounted[0].get('readonly'), True)
                    self.assertNotIn('rw', mounted[0])
                    self.assertNotIn('readwrite', mounted[0])

    def test_pip_dependency_contracts_are_unchanged(self):
        expected = {
            'reranker': [['python', '-m', 'pip', 'install', '--no-compile', '--no-index',
                          '--find-links=/wheelhouse', '--require-hashes', '-r', '/requirements.lock']],
            'converter-agpl': [
                ['python', '-m', 'pip', 'install', '--no-compile', '--no-index',
                 '--find-links=/wheelhouse', '--require-hashes', '--no-deps',
                 '--only-binary=:all:', '-r', '/build-backend.lock'],
                ['python', '-m', 'pip', 'install', '--no-compile', '--no-index',
                 '--find-links=/wheelhouse', '--require-hashes', '--no-build-isolation',
                 '--only-binary=:all:', '--no-binary=jieba', '-r', '/requirements.lock']],
            'builder': [['/opt/deep32/engine/.venv/bin/pip', 'install', '--no-index',
                         '--find-links', '/wheels', '--require-hashes', '-r', '/tmp/engine.lock']],
            'full': [['/opt/deep32/engine/.venv/bin/pip', 'install', '--no-index',
                      '--only-binary=:all:', '--find-links', '/wheels', '--require-hashes',
                      '-r', '/tmp/gateway.lock']],
        }
        for role in ROLES:
            actual = []
            for opcode, body in instructions(recipe(role)):
                if opcode != 'RUN':
                    continue
                _, tokens = run_parts(body)
                if 'install' not in tokens:
                    continue
                if '&&' in tokens:
                    tokens = tokens[tokens.index('&&') + 1:]
                self.assertIn('--no-cache-dir', tokens, role)
                actual.append([t for t in tokens if t != '--no-cache-dir'])
            self.assertEqual(actual, expected[role], role)

    def test_builder_keeps_complete_native_install_and_tools(self):
        steps = [run_parts(body)[1] for op, body in instructions(recipe('builder')) if op == 'RUN']
        self.assertIn(shlex.split('dpkg -i /debs/*.deb && git --version && age --version && gpg --version && gpgv --version'), steps)
        self.assertIn(('USER', 'deep32'), list(instructions(recipe('builder'))))
        self.assertIn('python -m venv /opt/deep32/engine/.venv', recipe('builder'))

    def test_build_backend_source_and_licences_are_kept(self):
        text = recipe('converter-agpl')
        self.assertIn('COPY build-backend.lock /build-backend.lock', text)
        for copy in ('chain.py worker.py convert.py LICENSE AGPL-3.0.txt /app/',
                     'production-converter/ /app/production-converter/',
                     'document-styles/ /app/document-styles/'):
            self.assertIn(('COPY', copy), list(instructions(text)))
        for role in ROLES:
            self.assertNotIn('pip uninstall', recipe(role))
            self.assertNotIn('apt-get purge', recipe(role))

    def test_deep32_images_carry_the_license_and_notice(self):
        # Full and model-bridge derive from the builder image and keep its copy.
        # The converter stays under its own AGPL LICENSE and NOTICE.
        for role, destination in (('door', '/app/'), ('reranker', '/app/'),
                                  ('builder', '/opt/deep32/builder/')):
            copies = [shlex.split(body) for opcode, body in instructions(recipe(role)) if opcode == 'COPY']
            for name in ('LICENSE', 'NOTICE'):
                with self.subTest(role=role, name=name):
                    self.assertEqual((KIT / 'containers' / role / name).read_bytes(), (KIT / name).read_bytes())
                    self.assertTrue(any(name in tokens[:-1] and tokens[-1] == destination for tokens in copies))
        self.assertIn('FROM ${BASE_IMAGE}', recipe('full'))

    def test_runtime_import_and_compiler_contracts_are_kept(self):
        self.assertIn('CC=/usr/bin/gcc', recipe('reranker'))
        self.assertIn('reranker_api:app', recipe('reranker'))
        self.assertIn("('uvicorn','rag_core.api','mcp.server.mcpserver','mcp_types','starlette','httpx')", recipe('full'))
        self.assertIn('&& /usr/bin/gpgv --version && openssl version', recipe('full'))
        for role in ROLES:
            self.assertIn('FROM ${BASE_IMAGE}', recipe(role))

    def test_build_time_checks_leave_no_bytecode(self):
        # A check step that writes __pycache__ changes the installed files, and the
        # reproducible rebuild then refuses the image (pip check did this once).
        for path in sorted((KIT / 'containers').glob('*/Dockerfile')):
            for opcode, body in instructions(path.read_text(encoding='utf-8')):
                if opcode != 'RUN':
                    continue
                for step in body.split('&&'):
                    words = step.split()
                    if (any(word == 'python' or word.endswith('/python') for word in words)
                            and ('-c' in words or 'check' in words)):
                        self.assertIn('-B', words, f'{path.parent.name}: {step.strip()}')

    def test_no_remote_frontend_or_input_download(self):
        for role in ROLES:
            self.assertFalse(any(line.lstrip().startswith('# syntax=')
                                 for line in recipe(role).splitlines()), role)
            for opcode, body in instructions(recipe(role)):
                if opcode in ('ADD', 'COPY'):
                    self.assertNotIn('https://', body)
                    self.assertNotIn('http://', body)

    def test_build_entry_points_require_buildkit_without_pulls(self):
        for name in ('build-images.sh', 'build-builder.sh', 'build-full.sh'):
            text = (KIT / 'build' / name).read_text(encoding='utf-8')
            builds = [line.strip() for line in text.splitlines()
                      if line.lstrip().startswith(('docker build ', 'DOCKER_BUILDKIT='))]
            self.assertEqual(len(builds), 1, name)
            self.assertTrue(builds[0].startswith('DOCKER_BUILDKIT=1 docker build '), name)
            self.assertIn('--network none --pull=false', builds[0])

    def test_build_entry_points_parse_and_dry_run_offline(self):
        executable = os.environ.get('D32_GIT_BASH')
        if not executable:
            self.skipTest('D32_GIT_BASH must name an authorised absolute Git Bash executable')
        self.assertTrue(Path(executable).is_absolute())
        for name in ('build-images.sh', 'build-builder.sh', 'build-full.sh'):
            script = KIT / 'build' / name
            for argv in ([executable, '-n', str(script)], [executable, str(script), '--dry-run']):
                proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                if '--dry-run' in argv:
                    self.assertIn('Downloads: none.', proc.stdout)


if __name__ == '__main__':
    unittest.main()
