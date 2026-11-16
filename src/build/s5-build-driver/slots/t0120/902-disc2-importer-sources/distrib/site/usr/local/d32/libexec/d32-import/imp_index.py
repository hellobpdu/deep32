"""Regenerate the installed Library page with its unchanged shipped generator.

C5 installation and the deep32-import-report contract govern failures.
Public: regenerate and library_removed. Document text is never copied into
the adapter; it supplies public metadata, counts and installed file links.
"""

import json
import os
from pathlib import Path
import re
import stat
import sys

from imp_fs import (ImportFailure, Interrupted, atomic_write, check_signal, log,
                    open_regular, remove_tree, set_owner_mode)
from imp_paths import rp


MAX_METADATA_BYTES = 67108864
MAX_RECORD_BYTES = 268435456
MAX_METADATA_ROWS = 1000000
MAX_SOURCE_ENTRIES = 100000
MAX_LAYER_BYTES = 4194304


def _temporary(ctx, name):
    if (not isinstance(name, str) or
            re.fullmatch(r'library-index-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', name) is None):
        raise ValueError('library-index-temporary-name')
    if ctx.p.tmp.is_symlink():
        raise ValueError('library-index-temporary-root')
    path = ctx.p.tmp / name
    if path.is_symlink() or not path.resolve().is_relative_to(ctx.p.tmp.resolve()):
        raise ValueError('library-index-temporary-path')
    return path


def _recover_temporary(ctx):
    marker = ctx.p.journal / 'library-index-tmp.json'
    try:
        data = json.loads(_read(marker, 4096).decode('ascii'))
    except FileNotFoundError:
        return marker
    if (not isinstance(data, dict) or set(data) != {'format', 'format_version', 'folder'} or
            data['format'] != 'deep32-library-index-tmp' or
            type(data['format_version']) is not int or data['format_version'] != 1):
        raise ValueError('library-index-temporary-state')
    folder = _temporary(ctx, data['folder'])
    if folder.exists() and not _directory(folder):
        raise ValueError('library-index-temporary-type')
    remove_tree(folder)
    marker.unlink()
    return marker


def _relative(value):
    return (isinstance(value, str) and 1 <= len(value) <= 1024 and
            not value.startswith('/') and '\\' not in value and not re.match(r'^[A-Za-z]:', value) and
            all(part not in ('', '.', '..') for part in value.split('/')) and
            all(ord(char) >= 32 and ord(char) != 127 for char in value))


def _directory(path):
    try:
        return stat.S_ISDIR(path.stat().st_mode)
    except OSError:
        return False


def _regular(path):
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _read(path, limit=MAX_METADATA_BYTES):
    with open_regular(path) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('library-index-metadata-limit')
    return raw


def _within(ctx, path, boundary=None):
    root = (boundary or ctx.p.cr).resolve()
    if not path.resolve().is_relative_to(root):
        raise ValueError('library-index-source-outside-content')
    return path


def _state(ctx):
    path = ctx.p.journal / 'library-index.json'
    try:
        raw = _read(path, 16384)
    except FileNotFoundError:
        return path, None, {}
    data = json.loads(raw.decode('ascii'))
    if (not isinstance(data, dict) or set(data) not in
            ({'aliases', 'format', 'format_version'}, {'aliases', 'format', 'format_version', 'transition'}) or
            data['format'] != 'deep32-library-index' or type(data['format_version']) is not int or
            data['format_version'] != 1 or not isinstance(data['aliases'], dict) or
            set(data['aliases']) - {'ietf'} or
            any(not _relative(value) for value in data['aliases'].values())):
        raise ValueError('library-index-state')
    for value in data['aliases'].values():
        _within(ctx, ctx.p.cr.joinpath(*value.split('/')))
    if 'transition' in data:
        transition = data['transition']
        if (not isinstance(transition, dict) or set(transition) != {'aliases', 'new', 'old'} or
                not isinstance(transition['aliases'], dict) or set(transition['aliases']) - {'ietf'} or
                any(not _relative(value) for value in transition['aliases'].values()) or
                any(value is not None and not _relative(value) for value in (transition['old'], transition['new']))):
            raise ValueError('library-index-transition')
        expected_old = {} if transition['old'] is None else {'ietf': transition['old']}
        expected_new = {} if transition['new'] is None else {'ietf': transition['new']}
        if (transition['old'] == transition['new'] or data['aliases'] != expected_old or
                transition['aliases'] != expected_new):
            raise ValueError('library-index-transition-ownership')
        for value in list(transition['aliases'].values()) + [transition['old'], transition['new']]:
            if value is not None:
                _within(ctx, ctx.p.cr.joinpath(*value.split('/')))
        from imp_switch import read_pointer
        pointer = ctx.p.cr / 'ietf'
        actual = read_pointer(pointer)
        present = pointer.exists() or pointer.is_symlink()
        if actual is None and present:
            raise ValueError('library-index-foreign-alias')
        if actual == transition['new']:
            data['aliases'] = transition['aliases']
        elif actual != transition['old']:
            raise ValueError('library-index-foreign-alias')
        del data['transition']
        raw = (json.dumps(data, indent=2, sort_keys=True) + '\n').encode('ascii')
        atomic_write(path, raw, 0o600, group='wheel')
    return path, raw, data['aliases']


def _sources(ctx, owned):
    """Recognise source layouts only in active pack generations and legacy roots."""
    cisco, ietf, layers = {}, {}, {}

    def add_cisco(path, boundary=None):
        _within(ctx, path, boundary)
        if _regular(path / 'manifest.jsonl'):
            _within(ctx, path / 'manifest.jsonl', boundary)
            cisco[str(path.resolve())] = path

    def add_ietf(path, boundary=None):
        _within(ctx, path, boundary)
        _within(ctx, path / 'rfc' / 'txt', boundary)
        _within(ctx, path / 'drafts' / 'txt', boundary)
        if _directory(path / 'rfc' / 'txt') or _directory(path / 'drafts' / 'txt'):
            ietf.setdefault(str(path.resolve()), path)

    def add_layout(folder):
        for prefix in (folder / 'libsrc' / 'raw', folder / 'raw', folder):
            add_cisco(prefix / 'cisco', folder)
            add_ietf(prefix / 'ietf', folder)
        for candidate in (folder / 'layers.json', folder / 'encyclopedia' / 'layers.json'):
            _within(ctx, candidate, folder)
            if _regular(candidate):
                layers[str(candidate.resolve())] = candidate

    active = (ctx.registry.active or {}).get('pointers', {})
    targets = {value.split('/')[0] for value in active.values()}
    for record in ctx.registry.data.get('generations', []):
        if record['unit_kind'] != 'library' or record['target'] not in targets:
            continue
        folder = ctx.p.cr / record['target']
        if not _directory(folder) or folder.is_symlink():
            raise ValueError('library-index-generation')
        add_layout(folder)
        for unit in record.get('units', []):
            if (not isinstance(unit, dict) or not isinstance(unit.get('id'), str) or
                    re.fullmatch(r'lib-[a-z0-9-]{1,32}', unit['id']) is None or
                    not isinstance(unit.get('generation'), str) or
                    re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', unit['generation']) is None):
                raise ValueError('library-index-unit')
            if unit['id'] == 'lib-cisco':
                add_cisco(folder, folder)
            elif unit['id'] == 'lib-ietf':
                add_ietf(folder, folder)
            manifest = ctx.p.packs / unit['id'] / unit['generation'] / 'MANIFEST.tsv'
            if ctx.p.packs.is_symlink():
                raise ValueError('library-index-record-root')
            _within(ctx, manifest, ctx.p.packs)
            if not _regular(manifest):
                continue
            for index, line in enumerate(_read(manifest, MAX_RECORD_BYTES).decode('utf-8').splitlines(), 1):
                if index > MAX_METADATA_ROWS:
                    raise ValueError('library-index-row-limit')
                fields = line.split('\t')
                if len(fields) != 8:
                    raise ValueError('library-index-record')
                name = fields[0]
                prefix = 'd32/libcontent/' + record['target'] + '/'
                if not name.startswith(prefix):
                    continue
                relative = name[len('d32/libcontent/'):]
                if not _relative(relative):
                    raise ValueError('library-index-record-path')
                path = ctx.p.cr.joinpath(*relative.split('/'))
                _within(ctx, path, folder)
                if name.endswith('/cisco/manifest.jsonl'):
                    add_cisco(path.parent, folder)
                for suffix in ('/ietf/rfc/txt/', '/ietf/drafts/txt/'):
                    if suffix in name:
                        base = name.split(suffix, 1)[0] + '/ietf'
                        add_ietf(ctx.p.cr.joinpath(*base[len('d32/libcontent/'):].split('/')), folder)
                if name.endswith('/layers.json'):
                    layers[str(path.resolve())] = path
    # These are recognised installed layouts, not a scan of arbitrary folders.
    def legacy_allowed(path):
        resolved = path.resolve()
        for record in ctx.registry.data.get('generations', []):
            if resolved.is_relative_to((ctx.p.cr / record['target']).resolve()):
                return record['unit_kind'] == 'library' and record['target'] in targets
        return True

    if legacy_allowed(ctx.p.cr / 'cisco'):
        add_cisco(ctx.p.cr / 'cisco')
    from imp_switch import read_pointer
    if ((read_pointer(ctx.p.cr / 'ietf') != owned.get('ietf') or 'ietf' not in owned) and
            legacy_allowed(ctx.p.cr / 'ietf')):
        add_ietf(ctx.p.cr / 'ietf')
    if len(ietf) > 1 or len(layers) > 1:
        raise ValueError('library-index-ambiguous-sources')
    return ([cisco[key] for key in sorted(cisco)],
            next(iter(ietf.values()), None), next(iter(layers.values()), None))


def _adapt(ctx, folder, cisco, ietf):
    raw = folder / 'raw'
    for path in (raw / 'cisco', raw / 'ietf' / 'rfc' / 'txt', raw / 'ietf' / 'drafts' / 'txt'):
        path.mkdir(parents=True, exist_ok=True)
    rows, seen = [], set()
    metadata_bytes = 0
    metadata_rows = 0
    for source in cisco:
        metadata = _read(source / 'manifest.jsonl')
        metadata_bytes += len(metadata)
        if metadata_bytes > MAX_METADATA_BYTES:
            raise ValueError('library-index-metadata-limit')
        for line in metadata.decode('utf-8').splitlines():
            check_signal()
            metadata_rows += 1
            if metadata_rows > MAX_METADATA_ROWS:
                raise ValueError('library-index-row-limit')
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('library-index-cisco-row')
            member = row.get('file')
            if row.get('role') == 'hub' or not member:
                continue
            if not _relative(member):
                raise ValueError('library-index-cisco-path')
            installed = source.joinpath(*member.split('/'))
            _within(ctx, installed, source)
            if not _regular(installed):
                continue
            relative = installed.relative_to(ctx.p.cr).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            rows.append({key: row[key] for key in ('title', 'date', 'role') if key in row} |
                        {'file': '../' + relative})
    (raw / 'cisco' / 'manifest.jsonl').write_text(
        ''.join(json.dumps(row, ensure_ascii=True) + '\n' for row in rows),
        encoding='ascii', newline='\n')
    if ietf is not None:
        entries = 0
        for kind in ('rfc', 'drafts'):
            source = ietf / kind / 'txt'
            count = 0
            if _directory(source):
                with os.scandir(source) as members:
                    for member in members:
                        count += 1
                        entries += 1
                        if entries > MAX_SOURCE_ENTRIES:
                            raise ValueError('library-index-entry-limit')
            for index in range(count):
                check_signal()
                (raw / 'ietf' / kind / 'txt' / ('entry' + format(index, '08d'))).touch()
                if index % 1024 == 0:
                    ctx.report.heartbeat()


def _alias(ctx, owned, ietf):
    from imp_switch import read_pointer
    path = ctx.p.cr / 'ietf'
    old = read_pointer(path)
    new = None if ietf is None else ietf.relative_to(ctx.p.cr).as_posix()
    # A direct legacy directory already is the fixed path the generator uses.
    if ietf == path:
        return old, old, dict(owned)
    if new is not None and not _relative(new):
        raise ValueError('library-index-ietf-path')
    present = path.exists() or path.is_symlink()
    if new is not None:
        if old == new:
            return old, new, {'ietf': new} if owned.get('ietf') == old else {}
        if present and (old is None or owned.get('ietf') != old):
            raise ValueError('library-index-foreign-alias')
        return old, new, {'ietf': new}
    if old is not None and owned.get('ietf') == old:
        return old, None, {}
    return old, old, {}


def regenerate(ctx):
    """Generate first, then atomically publish aliases, ownership and the page."""
    if ctx.p.node != 'workstation':
        return
    folder = None
    created = False
    marker = None
    marked = False
    try:
        marker = _recover_temporary(ctx)
        state_path, old_state, owned = _state(ctx)
        cisco, ietf, layers = _sources(ctx, owned)
        old_alias, new_alias, aliases = _alias(ctx, owned, ietf)
        folder = _temporary(ctx, 'library-index-' + ctx.import_id)
        if folder.exists() or folder.is_symlink():
            raise ValueError('library-index-temporary-collision')
        temporary_state = {'format': 'deep32-library-index-tmp', 'format_version': 1, 'folder': folder.name}
        atomic_write(marker, (json.dumps(temporary_state, sort_keys=True) + '\n').encode('ascii'),
                     0o600, group='wheel')
        marked = True
        folder.mkdir(parents=True)
        created = True
        _adapt(ctx, folder, cisco, ietf)
        generator = Path(__file__).with_name('vendor') / 'mkindex.py'
        argv = [sys.executable, '-I', '-B', '-X', 'utf8', str(generator), str(folder)]
        if layers is not None:
            _read(layers, MAX_LAYER_BYTES)
            argv.extend(['--encyc', str(layers)])
        child = ctx.run_child(argv, timeout=600)
        if child.returncode:
            raise ValueError('library-index-generator-exit:' + str(child.returncode))
        page = rp('/usr/local/share/d32/library/index.html')
        try:
            previous_page = _read(page)
        except FileNotFoundError:
            previous_page = None
        parent_existed = page.parent.exists()
        page.parent.mkdir(parents=True, exist_ok=True)
        if not parent_existed:
            set_owner_mode(page.parent, 'root', 'wheel', 0o755)
        state = {'aliases': aliases, 'format': 'deep32-library-index', 'format_version': 1}
        raw_state = (json.dumps(state, indent=2, sort_keys=True) + '\n').encode('ascii')
        from imp_switch import move_pointer
        page_attempted = False
        alias_attempted = False
        try:
            if old_alias != new_alias:
                previous_aliases = {'ietf': old_alias} if old_alias is not None and owned.get('ietf') == old_alias else {}
                prepared = {'aliases': previous_aliases, 'format': 'deep32-library-index', 'format_version': 1,
                            'transition': {'aliases': aliases, 'new': new_alias, 'old': old_alias}}
                atomic_write(state_path, (json.dumps(prepared, indent=2, sort_keys=True) + '\n').encode('ascii'),
                             0o600, group='wheel')
                alias_attempted = True
                move_pointer(ctx.p.cr / 'ietf', new_alias, fault=False)
            atomic_write(state_path, raw_state, 0o600, group='wheel')
            page_attempted = True
            atomic_write(page, child.stdout.encode('utf-8'), 0o444, group='_d32users')
        except Exception:
            if alias_attempted:
                move_pointer(ctx.p.cr / 'ietf', old_alias, fault=False)
            if old_state is None:
                state_path.unlink(missing_ok=True)
            else:
                atomic_write(state_path, old_state, 0o600, group='wheel')
            if page_attempted:
                if previous_page is None:
                    page.unlink(missing_ok=True)
                else:
                    atomic_write(page, previous_page, 0o444, group='_d32users')
            raise
        ctx.journal.note('library-index:regenerated')
    except Interrupted:
        raise
    except Exception as exc:
        ctx.journal.note('library-index:' + type(exc).__name__ + ':' + str(exc))
        log(ctx.p, ctx.import_id, 'error', component='library-index', exception=type(exc).__name__)
        raise ImportFailure('IMPORT_INTERNAL', stage=ctx.report.data['stage']) from exc
    finally:
        if created:
            remove_tree(folder)
        if marked:
            marker.unlink(missing_ok=True)


def library_removed(ctx, record):
    """Refresh only after an installed library record has actually been removed."""
    if ctx.p.node == 'workstation' and record.get('unit_kind') == 'library':
        regenerate(ctx)
