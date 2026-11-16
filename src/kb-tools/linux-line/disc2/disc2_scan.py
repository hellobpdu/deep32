"""Construct a verified logical archive view for unchanged release gates.

The original stage and image still require their own gate scans. The view
preserves ordinary files and replaces only C11-declared C5 parts and validated
C4 parts with complete byte-identical tar streams. Exact checksum and fragment
allowances are proposals tied to successful checks; they are never exclusions.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True
import argparse
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HEX64 = re.compile(r'[0-9a-f]{64}')
IMAGE_NAME = re.compile(r'[A-Za-z0-9._+@=,-]{1,200}')
BUFFER = 8 * 1024 * 1024


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        while data := handle.read(BUFFER):
            digest.update(data)
    return digest.hexdigest()


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                    separators=(',', ':')).encode('ascii')).hexdigest()


def _modules(kit_dir):
    directory = Path(kit_dir).resolve()
    if not (directory / 'd32_disc2.py').is_file():
        directory /= 'disc2'
    if not (directory / 'd32_disc2.py').is_file():
        raise ValueError('The kit must contain disc2/d32_disc2.py')
    for name in ('d2common', 'c5pack', 'citemap'):
        current = sys.modules.get(name)
        if current is not None and Path(current.__file__).resolve().parent != directory:
            raise RuntimeError('A different disc kit is already imported: ' + name)
    sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location('_disc2_scan_builder', directory / 'd32_disc2.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return directory, builder, importlib.import_module('c5pack')


def _relative(path):
    if not isinstance(path, str) or not path or path.startswith('/') or '\\' in path:
        raise ValueError('Invalid relative scan path')
    if any(part in ('', '.', '..') for part in path.split('/')):
        raise ValueError('Invalid relative scan path component')
    return path


def _fresh_output(source, target):
    if target.exists():
        raise FileExistsError(target)
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('The scan view must be separate from its source')
    current = target.parent
    while current != current.parent:
        if current.exists() and (current.is_symlink() or current.is_junction()):
            raise ValueError('The scan view parent must not be a link')
        current = current.parent


def _link_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def _join(source, target, parts, expected_size, expected_hash):
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with target.open('xb') as output:
        for part in parts:
            path = source / _relative(part['path'])
            if path.stat().st_size != part['size'] or _hash(path) != part['sha256']:
                raise ValueError('A source part changed before joining')
            with path.open('rb') as handle:
                while data := handle.read(BUFFER):
                    output.write(data)
                    digest.update(data)
                    size += len(data)
    if size != expected_size or digest.hexdigest() != expected_hash:
        raise ValueError('The complete joined stream differs from its manifest')
    if target.stat().st_size != size or _hash(target) != expected_hash:
        raise ValueError('The materialised joined stream differs')


def _checksum_rows(streams, manifests, date):
    """Accept only the exact C5 outer schema or recognised inner schema."""
    view_rows, raw_rows = [], []
    for stream in streams:
        c11 = manifests[stream['name']]
        reader = stream['_reader']
        members = stream['_members']
        header_offsets = {member['header_offset'] for member in members}
        part_ranges = []
        position = 0
        for part in c11['parts']:
            part_ranges.append((part['path'], position, position + part['size']))
            position += part['size']
        outer = 'var/db/d32/packs/' + stream['name'] + '/MANIFEST.tsv'
        prefix = stream.get('prefix', '')
        generation_members = {}
        for sibling in streams:
            if sibling['generation'] != stream['generation']:
                continue
            for member in sibling['_members'][:-2]:
                if member['name'] in generation_members:
                    raise ValueError('Repeated member in one Library generation')
                generation_members[member['name']] = member
        for member in members:
            if member['name'] not in (outer, prefix + 'MANIFEST.tsv'):
                continue
            data = reader.read_at(member['data_offset'], member['size'])
            text = data.decode('utf-8')
            if '\r' in text or not text.endswith('\n'):
                raise ValueError('A checked manifest is not LF terminated')
            lines = text.splitlines()
            inner = member['name'] != outer
            if inner:
                if lines[:2] != ['# e26-content-manifest\t1', '# path\tbytes\tsha256\tmode\towner\tgroup']:
                    continue
                start, columns, hash_column, size_column = 2, 6, 2, 1
                targets = generation_members
            else:
                start, columns, hash_column, size_column = 0, 8, 6, 5
                targets = {value['name']: value for value in members if value['name'] != outer}
            for index, line in enumerate(lines[start:], start + 1):
                fields = line.split('\t')
                if len(fields) != columns:
                    raise ValueError('Manifest row does not match its verified schema')
                token = fields[hash_column]
                if not HEX64.fullmatch(token):
                    raise ValueError('Manifest digest cell is not SHA-256')
                target_name = prefix + fields[0] if inner else fields[0]
                target = targets.get(target_name)
                if target is None or fields[size_column] != str(target['size']) or token != target['sha256']:
                    raise ValueError('Manifest digest cell does not match the checked member')
                match = 'sha256:' + hashlib.sha256(token.encode('ascii')).hexdigest()
                reason = ('Verified C5 manifest SHA-256 cell; stream ' + c11['stream_sha256']
                          + '; manifest ' + member['sha256'] + '; member ' + target['sha256'])
                common = {'line': str(index), 'text': match, 'reason': reason, 'date': date}
                view_rows.append(dict(common, path=c11['_view_path'] + '!/' + member['name']))
                # A later fragment can also begin on a real tar header. Only
                # propose a path when the checked member header lies inside
                # that fragment and the fragment starts on a checked header.
                for part_path, start_offset, end_offset in part_ranges:
                    if start_offset in header_offsets and start_offset <= member['header_offset'] < end_offset:
                        raw_rows.append(dict(common, path=part_path + '!/' + member['name']))
    key = lambda row: (row['path'], int(row['line']), row['text'])
    return sorted(view_rows, key=key), sorted(raw_rows, key=key)


def build_view(stage_disc, work_view, kit_dir):
    """Return JSON-safe coverage for a new, separately located scan tree.

    This calls the existing C11, C5 and C4 checkers and does not verify the
    stage signature. The caller separately verifies signature trust. The
    returned report must be retained outside the view.
    """
    source, view = Path(stage_disc).resolve(), Path(work_view).resolve()
    _fresh_output(source, view)
    directory, builder, c5 = _modules(kit_dir)
    manifest = builder.check_disc_tree(source, signature=False)
    source_files = {path.relative_to(source).as_posix(): path for path in builder.safe_walk(source)}
    source_facts = {name: {'path': name, 'size': path.stat().st_size, 'sha256': _hash(path)}
                    for name, path in sorted(source_files.items())}
    folder_names = sorted({str((source / record['parts'][0]['path']).parent)
                           for record in manifest['library']})
    checked = c5.check_packs([Path(path) for path in folder_names], program_checks=True)
    if checked['findings']:
        raise ValueError('The C5 check refused the source streams: ' + json.dumps(checked['findings']))
    by_name = {record['pack']: dict(record) for record in manifest['library']}
    if set(by_name) != {stream['name'] for stream in checked['streams']}:
        raise ValueError('C11 and C5 stream sets differ')
    joins, replaced = [], set()
    for stream in checked['streams']:
        record = by_name[stream['name']]
        declared = [(source / part['path']).resolve() for part in record['parts']]
        if declared != [Path(part).resolve() for part in stream['_parts']]:
            raise ValueError('C11 and C5 part ordering differs')
        if record['stream_bytes'] != stream['stream_bytes'] or record['stream_sha256'] != stream['stream_sha256']:
            raise ValueError('C11 and C5 logical stream identity differs')
        first = record['parts'][0]['path']
        stem = first.rsplit('.part', 1)[0] if re.search(r'\.tar\.part[0-9]{3}$', first) else first
        if not stem.endswith('.tar'):
            raise ValueError('The Library stream has no canonical tar name')
        record['_view_path'] = stem
        stream['_reader'] = c5.PartsReader(stream['_parts'])
        parts = [{key: part[key] for key in ('path', 'size', 'sha256')} for part in record['parts']]
        joins.append({'kind': 'C5', 'id': stream['name'], 'view_path': stem,
                      'source_parts': parts, 'stream_bytes': record['stream_bytes'],
                      'stream_sha256': record['stream_sha256'], 'members': len(stream['_members']),
                      'verified': True})
        replaced.update(part['path'] for part in parts)
    # C4's accepted validator checks the original archive sequence, including
    # member bytes, before any optional split archive is replaced in the view.
    process = subprocess.run([sys.executable, '-I', '-B', str(directory / 'vendor/t0106/validate_block.py'),
                              str(source / 'corpus'), '--set'], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=7200)
    if process.returncode:
        raise ValueError('The C4 check refused the source blocks: exit ' + str(process.returncode))
    for block in manifest['blocks']:
        block_path = source / block['path']
        obj = json.loads(block_path.read_bytes())
        archive = obj['archive']
        if not archive.get('parts'):
            continue
        parent = Path(block['path']).parent.as_posix()
        parts = []
        for item in archive['parts']:
            name = item.get('file', item.get('name'))
            path = _relative(parent + '/' + name)
            parts.append({'path': path, 'size': item['size'], 'sha256': item['sha256']})
        c11_parts = [{key: item[key] for key in ('path', 'size', 'sha256')}
                     for item in block['archive_files']]
        if parts != c11_parts:
            raise ValueError('C11 and C4 part identities differ')
        target = _relative(parent + '/' + archive['file'])
        joins.append({'kind': 'C4', 'id': block['name'], 'view_path': target,
                      'source_parts': parts, 'stream_bytes': archive['size'],
                      'stream_sha256': archive['sha256'], 'verified': True})
        replaced.update(part['path'] for part in parts)
    names = [item['view_path'] for item in joins]
    if len(names) != len(set(names)) or set(names) & (set(source_files) - replaced):
        raise ValueError('Joined archive names overlap another payload')
    date = manifest['disc']['created'][:10]
    view_checksums, raw_checksums = _checksum_rows(checked['streams'], by_name, date)
    blind_rows = []
    for joined in joins:
        if len(joined['source_parts']) > 1:
            proof = _json_hash(joined)
            for part in joined['source_parts']:
                # A part boundary can land on any tar member header. The
                # exact <blind> match permits only that fragment's container
                # finding, and never a token, member, or nested blind finding.
                blind_rows.append({'path': part['path'], 'line': 'any', 'text': '<blind>',
                                   'reason': 'Complete ' + joined['kind'] + ' stream independently checked and scanned; '
                                             + 'part SHA-256 ' + part['sha256'] + '; stream SHA-256 '
                                             + joined['stream_sha256'] + '; join proof SHA-256 ' + proof,
                                   'date': date})
    view.mkdir(parents=True)
    entries = []
    try:
        for name, fact in source_facts.items():
            item = dict(fact, action='joined-part' if name in replaced else 'ordinary')
            if name not in replaced:
                _link_copy(source_files[name], view / name)
                item['view_path'] = name
            entries.append(item)
        for joined in joins:
            _join(source, view / joined['view_path'], joined['source_parts'],
                  joined['stream_bytes'], joined['stream_sha256'])
        # Fail on concurrent source changes instead of binding scan allowances
        # to an earlier version of a mutable stage.
        if set(source_files) != {path.relative_to(source).as_posix() for path in builder.safe_walk(source)}:
            raise ValueError('The source stage file set changed during view creation')
        for name, fact in source_facts.items():
            path = source_files[name]
            if path.stat().st_size != fact['size'] or _hash(path) != fact['sha256']:
                raise ValueError('A source stage file changed during view creation')
    except Exception:
        # The caller may inspect this owned failed view; it never receives a
        # successful coverage result or allowance set for incomplete output.
        raise
    coverage = {'format': 'deep32-disc2-scan-coverage', 'format_version': 1,
                'view': view.as_posix(), 'source_stage': source.as_posix(),
                'source_manifest_sha256': source_facts['MANIFEST.json']['sha256'],
                'entries': entries, 'joins': joins, 'source_files': len(entries),
                'source_bytes': sum(item['size'] for item in entries),
                'c11_check': 'pass', 'c5_check': 'pass', 'c4_check': 'pass',
                'view_checksum_allowances': view_checksums, 'raw_checksum_allowances': raw_checksums,
                'raw_blind_allowances': sorted(blind_rows, key=lambda row: row['path']),
                'checksum_allowances': view_checksums,
                'tools': {'d32_disc2.py': _hash(directory / 'd32_disc2.py'),
                          'c5pack.py': _hash(directory / 'c5pack.py'),
                          'validate_block.py': _hash(directory / 'vendor/t0106/validate_block.py')}}
    coverage['coverage_sha256'] = _json_hash(coverage)
    return coverage


def allowances(coverage, image_basename=None):
    """Return proposed exact rows for the view, raw stage, or raw image.

    None selects the complete view. An empty string selects the raw stage.
    An image basename selects raw paths prefixed with that basename and !/.
    The caller may use raw allowances only after the complete view gate report
    passed and its identity was bound to this coverage record.
    """
    if coverage.get('format') != 'deep32-disc2-scan-coverage' or coverage.get('format_version') != 1:
        raise ValueError('Unknown scan coverage format')
    expected = coverage.get('coverage_sha256')
    if expected != _json_hash({key: value for key, value in coverage.items() if key != 'coverage_sha256'}):
        raise ValueError('The scan coverage record changed')
    if image_basename is None:
        rows = coverage['view_checksum_allowances']
        prefix = ''
    else:
        if image_basename and not IMAGE_NAME.fullmatch(image_basename):
            raise ValueError('Expected a plain image basename')
        rows = coverage['raw_checksum_allowances'] + coverage['raw_blind_allowances']
        prefix = image_basename + '!/' if image_basename else ''
    return [dict(row, path=prefix + row['path']) for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-disc', required=True, type=Path)
    parser.add_argument('--view', required=True, type=Path)
    parser.add_argument('--kit', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    result = build_view(args.stage_disc, args.view, args.kit)
    args.out.write_text(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2) + '\n',
                        encoding='ascii', newline='\n')
    print(json.dumps({'view': result['view'], 'joins': len(result['joins']),
                      'source_files': result['source_files'], 'coverage_sha256': result['coverage_sha256']},
                     sort_keys=True))


if __name__ == '__main__':
    main()
