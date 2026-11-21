"""Accept the C2 signature and the ordered content-manifest schema.

Public: run_signature, validate_manifest, build_units, desired_units and
member_unit. No member path is used before the signature has passed.
"""

import sys
sys.dont_write_bytecode = True

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from imp_fs import ImportFailure, atomic_write, crash, open_regular, signal_point
from imp_paths import hooks, test_mode


VERSION = r'[0-9A-Za-z][0-9A-Za-z._+-]{0,63}'
HEX64 = r'[0-9a-f]{64}'
KEYS = {
    'top': 'format format_version disc accepts_system signer kb blocks library cisco_public code root_files members counts',
    'disc': 'role version volume_id label created build_option',
    'signer': 'type fingerprint',
    'kb': 'path generation fingerprint created bundle_json_sha256 manifest_sha256 documents chunks points by_collection',
    'block': 'name version path block_json_sha256 documents archive_files',
    'file': 'path size sha256',
    'library': 'pack generation release program_pack pointer install_prefix kb_generation kb_fingerprint has_cite_map stream_bytes stream_sha256 part_size parts',
    'part': 'index path size sha256',
    'cisco': 'path size sha256 catalogue_generation scope kb_generation kb_fingerprint program_path',
    'code': 'path files bytes',
    'counts': 'files bytes',
}


def _form(condition):
    if not condition:
        raise ImportFailure('IMPORT_MANIFEST', stage='signature')


def _keys(value, kind):
    _form(isinstance(value, dict) and list(value) == KEYS[kind].split())


def _integer(value, minimum=0, maximum=None):
    return type(value) is int and value >= minimum and (maximum is None or value <= maximum)


def _match(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def safe_version(value):
    return _match(VERSION, value) and not re.search(r'[0-9a-fA-F]{32,}', value)


def _pairs(items):
    result = {}
    for key, value in items:
        _form(key not in result)
        result[key] = value
    return result


def safe_member(path):
    return (isinstance(path, str) and len(path.encode('utf-8')) <= 1024 and
            not path.startswith('/') and all(_match(r'(?!\.{1,2}$)[A-Za-z0-9._+@=,-]{1,200}', p)
                                               for p in path.split('/')))


def group_pointer(entry):
    if entry['pointer'] is not None:
        return entry['pointer'].rsplit('/', 1)[-1]
    match = re.fullmatch(r'\.([a-z][a-z0-9]{0,31})-[a-z0-9][a-z0-9._-]*',
                         entry['install_prefix'].rsplit('/', 1)[-1])
    derived = match.group(1) if match else None
    return None if derived in ('kb', 'cisco') else derived


def _compatible_versions(path):
    try:
        with open_regular(path) as stream:
            raw = stream.read(4097)
        versions = raw.decode('ascii').splitlines()
    except (OSError, UnicodeError):
        return set()
    if len(raw) > 4096 or not versions or not all(safe_version(x) for x in versions):
        return set()
    return set(versions)


def validate_manifest(raw, fingerprint, source=None):
    """Check M1 to M14, preserving both canonical bytes and object order."""
    try:
        obj = json.loads(raw.decode('ascii'), object_pairs_hook=_pairs)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ImportFailure('IMPORT_MANIFEST', stage='signature') from exc
    if (isinstance(obj, dict) and isinstance(obj.get('disc'), dict) and
            safe_version(obj['disc'].get('version')) and source is not None):
        source['disc_version'] = obj['disc']['version']
    _form((json.dumps(obj, indent=2) + '\n').encode('ascii') == raw)
    _keys(obj, 'top')
    _form(obj['format'] == 'deep32-content-manifest' and type(obj['format_version']) is int and obj['format_version'] == 1)
    disc = obj['disc']
    _keys(disc, 'disc')
    _form(disc['role'] == 'content' and safe_version(disc['version']) and
          _match(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', disc['created']) and
          disc['build_option'] in ('standard', 'disc2-without-cisco') and
          isinstance(disc['volume_id'], str) and isinstance(disc['label'], str))
    accepts = obj['accepts_system']
    _form(isinstance(accepts, list) and bool(accepts) and all(safe_version(x) for x in accepts) and
          accepts == sorted(set(accepts)))
    _keys(obj['signer'], 'signer')
    if obj['signer']['type'] != 'openpgp' or obj['signer']['fingerprint'] != fingerprint:
        if source is not None:
            source.update(signature='bad', signer=None, disc_version=None, disc_bytes=None)
        raise ImportFailure('IMPORT_SIGNATURE', stage='signature')
    _form(isinstance(obj['members'], list))
    members = {}
    previous = b''
    for item in obj['members']:
        _keys(item, 'file')
        name = item['path']
        _form(safe_member(name) and name not in ('MANIFEST.json', 'MANIFEST.json.asc') and
              _integer(item['size']) and _match(HEX64, item['sha256']) and name.encode('utf-8') > previous)
        previous = name.encode('utf-8')
        members[name] = item
    _keys(obj['counts'], 'counts')
    _form(_integer(obj['counts']['files']) and _integer(obj['counts']['bytes']) and
          obj['counts']['files'] == len(members) and obj['counts']['bytes'] == sum(x['size'] for x in members.values()))
    if source is not None:
        source['disc_bytes'] = obj['counts']['bytes']
    claimed = set()

    def claim(item):
        _form(item['path'] in members and item['path'] not in claimed and
              all(members[item['path']][k] == item[k] for k in ('size', 'sha256')))
        claimed.add(item['path'])

    kb = obj['kb']
    _keys(kb, 'kb')
    _form(_match(r'[a-z0-9][a-z0-9._-]{0,39}', kb['generation']) and kb['path'] == 'kb/' + kb['generation'] and
          _match(r'[0-9a-f]{16}', kb['fingerprint']) and _match(HEX64, kb['manifest_sha256']) and
          _match(HEX64, kb['bundle_json_sha256']) and isinstance(kb['created'], str) and
          all(_integer(kb[x]) for x in ('documents', 'chunks', 'points')) and
          isinstance(kb['by_collection'], dict) and all(isinstance(x, dict) for x in kb['by_collection'].values()))
    _form(kb['path'] + '/BUNDLE.json' in members and kb['path'] + '/MANIFEST.json' in members and
          members[kb['path'] + '/BUNDLE.json']['sha256'] == kb['bundle_json_sha256'])
    for name in members:
        if name.startswith('kb/'):
            _form(name.startswith(kb['path'] + '/'))
            claimed.add(name)
    _form(isinstance(obj['blocks'], list))
    names = []
    for block in obj['blocks']:
        _keys(block, 'block')
        name = block['name']
        _form(name in ('rfc', 'cisco', 'powershell', 'system') and name not in names and
              block['path'] == 'corpus/' + name + '/block.json' and _match(HEX64, block['block_json_sha256']) and
              isinstance(block['version'], str) and _integer(block['documents']) and isinstance(block['archive_files'], list))
        names.append(name)
        _form(block['path'] in members and members[block['path']]['sha256'] == block['block_json_sha256'])
        claim(members[block['path']])
        for archive in block['archive_files']:
            _keys(archive, 'file')
            _form(safe_member(archive['path']) and archive['path'].startswith('corpus/' + name + '/') and
                  _integer(archive['size']) and _match(HEX64, archive['sha256']))
            claim(archive)
    _form(names == sorted(names))
    _form(isinstance(obj['library'], list))
    packs = set()
    groups = {}
    pointers = {}
    for entry in obj['library']:
        _keys(entry, 'library')
        pack, generation = entry['pack'], entry['generation']
        _form(_match(r'lib-[a-z0-9-]{1,32}', pack) and pack not in packs and
              _match(r'[a-z0-9][a-z0-9._-]{0,127}', generation) and isinstance(entry['release'], str) and bool(entry['release']) and
              (entry['program_pack'] is None or isinstance(entry['program_pack'], str)) and
              (entry['pointer'] is None or _match(r'/d32/libcontent/[a-z][a-z0-9-]{0,31}', entry['pointer'])) and
              _match(r'd32/libcontent/\.[a-z0-9][a-z0-9._-]{0,199}', entry['install_prefix']) and
              ((entry['kb_generation'] is None and entry['kb_fingerprint'] is None) or
               (isinstance(entry['kb_generation'], str) and isinstance(entry['kb_fingerprint'], str))) and
              type(entry['has_cite_map']) is bool and _integer(entry['stream_bytes'], 1) and
              _match(HEX64, entry['stream_sha256']) and _integer(entry['part_size'], 512, 1992294400) and
              isinstance(entry['parts'], list) and 1 <= len(entry['parts']) <= 999)
        packs.add(pack)
        for index, part in enumerate(entry['parts'], 1):
            _keys(part, 'part')
            _form(type(part['index']) is int and part['index'] == index and
                  part['path'] == 'library/' + pack + '/' + pack + '-' + generation + '.tar.part' + format(index, '03d') and
                  _integer(part['size'], 1, entry['part_size']) and _match(HEX64, part['sha256']) and
                  (index == len(entry['parts']) or part['size'] == entry['part_size']))
            claim(part)
        _form(sum(x['size'] for x in entry['parts']) == entry['stream_bytes'])
        group = entry['install_prefix']
        identity = tuple(entry[k] for k in ('generation', 'program_pack', 'pointer', 'kb_generation', 'kb_fingerprint'))
        _form(group not in groups or groups[group] == identity)
        groups[group] = identity
        pointer = group_pointer(entry)
        if pointer is not None:
            _form(pointer not in ('kb', 'cisco') and not pointer.startswith(('block-', '.')) and
                  (pointer not in pointers or pointers[pointer] == group))
            pointers[pointer] = group
    cisco = obj['cisco_public']
    if cisco is not None:
        _keys(cisco, 'cisco')
        _form(cisco['path'] == 'library/cisco-public.sqlite' and _integer(cisco['size']) and
              _match(HEX64, cisco['sha256']) and all(isinstance(cisco[k], str) for k in
              ('catalogue_generation', 'scope', 'kb_generation', 'kb_fingerprint', 'program_path')))
        claim(cisco)
    code = obj['code']
    if code is not None:
        _keys(code, 'code')
        code_members = [x for x in members.values() if x['path'].startswith('code/')]
        _form(code['path'] == 'code' and _integer(code['files']) and _integer(code['bytes']) and
              code['files'] == len(code_members) and code['bytes'] == sum(x['size'] for x in code_members))
        for item in code_members:
            claim(item)
    _form(isinstance(obj['root_files'], list))
    for item in obj['root_files']:
        _keys(item, 'file')
        _form(isinstance(item['path'], str) and '/' not in item['path'])
        claim(item)
    _form(claimed == set(members))
    return obj


def _unit_generation(value, journal):
    if not _match(r'[A-Za-z0-9._+-]{1,64}', value) or re.search(r'[0-9a-fA-F]{32,}', value):
        journal.note('unit generation omitted')
        return None
    return value


def member_unit(manifest, path):
    if path.startswith(manifest['kb']['path'] + '/'):
        return 'kb'
    for entry in manifest['library']:
        if path in [x['path'] for x in entry['parts']]:
            return entry['pack']
    if manifest['cisco_public'] and path == manifest['cisco_public']['path']:
        return 'cisco-public'
    for block in manifest['blocks']:
        if path.startswith('corpus/' + block['name'] + '/'):
            return 'block-' + block['name']
    return None


def build_units(ctx):
    m = ctx.manifest
    kb = m['kb']
    units = [dict(id='kb', kind='kb', generation=kb['generation'],
                  bytes=sum(x['size'] for x in m['members'] if x['path'].startswith(kb['path'] + '/')),
                  sha256=kb['manifest_sha256'], parts=1, parts_checked=0, state='pending', kb_fingerprint=None, error=None)]
    for entry in m['library']:
        units.append(dict(id=entry['pack'], kind='library', generation=_unit_generation(entry['release'], ctx.journal),
                          bytes=entry['stream_bytes'], sha256=entry['stream_sha256'], parts=len(entry['parts']), parts_checked=0,
                          state='pending' if ctx.p.node == 'workstation' else 'skipped', kb_fingerprint=entry['kb_fingerprint'], error=None))
    if m['cisco_public']:
        entry = m['cisco_public']
        units.append(dict(id='cisco-public', kind='index', generation=None, bytes=entry['size'], sha256=entry['sha256'],
                          parts=1, parts_checked=0, state='pending' if ctx.p.node == 'workstation' else 'skipped',
                          kb_fingerprint=entry['kb_fingerprint'], error=None))
    for block in sorted(m['blocks'], key=lambda x: ('rfc', 'cisco', 'powershell', 'system').index(x['name'])):
        total = sum(x['size'] for x in m['members'] if x['path'].startswith('corpus/' + block['name'] + '/'))
        units.append(dict(id='block-' + block['name'], kind='block', generation=_unit_generation(block['version'], ctx.journal),
                          bytes=total, sha256=block['block_json_sha256'], parts=len(block['archive_files']), parts_checked=0,
                          state='pending' if ctx.p.node == 'forge' else 'skipped', kb_fingerprint=None, error=None))
    ctx.report.data['units'] = units
    return units


def desired_units(ctx):
    m, kb = ctx.manifest, ctx.manifest['kb']
    sizes = {x['id']: x['bytes'] for x in ctx.report.data['units']}
    generation = kb['generation']
    desired = [dict(target='.kb-' + generation, pointer='kb', value='.kb-' + generation + '/' + generation,
                    content_key=kb['manifest_sha256'], unit_kind='kb', bytes=sizes['kb'], generation=generation,
                    fingerprint=kb['fingerprint'], units=[dict(id='kb', generation=generation, release=None)])]
    if ctx.p.node == 'workstation':
        groups = {}
        for entry in m['library']:
            groups.setdefault(entry['install_prefix'], []).append(entry)
        for prefix, entries in groups.items():
            first = entries[0]
            target = prefix.rsplit('/', 1)[-1]
            lines = sorted(x['pack'] + '\t' + x['stream_sha256'] for x in entries)
            desired.append(dict(target=target, pointer=group_pointer(first), value=target,
                                content_key=hashlib.sha256(('\n'.join(lines) + '\n').encode('ascii')).hexdigest(),
                                unit_kind='library', bytes=sum(sizes[x['pack']] for x in entries), generation=first['generation'],
                                fingerprint=None, streams=entries,
                                units=[dict(id=x['pack'], generation=x['generation'], release=x['release']) for x in entries]))
        if m['cisco_public']:
            entry = m['cisco_public']
            target = '.cisco-' + entry['sha256'][:16]
            desired.append(dict(target=target, pointer='cisco', value=target, content_key=entry['sha256'],
                                unit_kind='cisco', bytes=entry['size'], generation=entry['catalogue_generation'], fingerprint=None,
                                units=[dict(id='cisco-public', generation=None, release=None)]))
    else:
        for block in m['blocks']:
            target = '.block-' + block['name'] + '-' + block['version']
            desired.append(dict(target=target, pointer='block-' + block['name'], value=target + '/' + block['name'],
                                content_key=block['block_json_sha256'], unit_kind='block', bytes=sizes['block-' + block['name']],
                                generation=block['version'], fingerprint=None, block=block,
                                units=[dict(id='block-' + block['name'], generation=block['version'], release=None)]))
    old_desired = ctx.journal.data.get('desired', [])
    for entry in desired:
        old = next((x for x in old_desired if x['target'] == entry['target'] and
                    x['content_key'] == entry['content_key']), None)
        if old:
            for key in ('records', 'verification', 'installed_bytes'):
                if key in old:
                    entry[key] = old[key]
    ctx.desired = desired
    ctx.journal.data['desired'] = desired
    return desired


def run_signature(ctx):
    disc, p = ctx.disc, ctx.p
    source = ctx.report.data['source']
    source['manifest_sha256'] = hashlib.sha256(disc.manifest_bytes).hexdigest()
    if disc.signature_error:
        if disc.signature_error == 'IMPORT_SIGNATURE':
            source['signature'] = 'bad'
        raise ImportFailure(disc.signature_error, stage='signature')
    group = 'wheel' if p.node == 'workstation' else 'root'
    atomic_write(p.journal / 'manifest.json', disc.manifest_bytes, 0o600, group=group)
    atomic_write(p.journal / 'manifest.json.asc', disc.signature_bytes, 0o600, group=group)
    try:
        mode = os.lstat(p.release_key).st_mode
        fingerprint = p.release_fingerprint.read_text(encoding='ascii').splitlines()[0]
        valid = stat.S_ISREG(mode) and _match(r'[0-9A-F]{40}', fingerprint)
    except (OSError, UnicodeError, IndexError):
        valid = False
    if not valid:
        source['signature'] = 'bad'
        ctx.journal.note('release key not installed')
        raise ImportFailure('IMPORT_SIGNATURE', stage='signature')
    gpgv = Path(hooks()['D32_STUBS']) / 'gpgv.py' if test_mode() else Path(p.gpgv)
    try:
        valid = stat.S_ISREG(os.lstat(gpgv).st_mode)
    except OSError:
        valid = False
    if not valid:
        raise ImportFailure('IMPORT_GPGV_MISSING', stage='signature')
    argv = p.command('python') + [str(Path(__file__).with_name('vendor') / 'openpgp_verify.py'),
             '--keyring', str(p.release_key), '--fingerprint', fingerprint,
             '--gpgv-argv', json.dumps(p.command('gpgv')), '--tmp-dir', str(p.tmp),
             str(p.journal / 'manifest.json'), str(p.journal / 'manifest.json.asc')]
    result = ctx.run_child(argv, timeout=1800)
    if result.returncode:
        code = {3: 'IMPORT_GPGV_MISSING', 2: 'IMPORT_SIGNATURE', 4: 'IMPORT_SIGNATURE'}.get(result.returncode, 'IMPORT_INTERNAL')
        if code == 'IMPORT_SIGNATURE':
            source['signature'] = 'bad'
        raise ImportFailure(code, stage='signature')
    source.update(signature='good', signer=fingerprint)
    try:
        ctx.manifest = validate_manifest(disc.manifest_bytes, fingerprint, source)
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise ImportFailure('IMPORT_MANIFEST', stage='signature') from exc
    m = ctx.manifest
    if p.node == 'workstation':
        system_valid = False
        try:
            system = p.release_file.read_text(encoding='ascii').splitlines()[0].split()[0]
            system_valid = safe_version(system)
            if not system_valid:
                system = 'unknown'
        except (OSError, UnicodeError, IndexError):
            system = 'unknown'
        versions = {system}
        if system_valid:
            versions.update(_compatible_versions(p.content_compatible_versions))
        if not versions.intersection(m['accepts_system']):
            wanted = ':'.join(m['accepts_system'])
            if len(wanted) > 128:
                selected = []
                for item in m['accepts_system']:
                    if len(':'.join(selected + [item])) > 124:
                        break
                    selected.append(item)
                wanted = ':'.join(selected) + ':...'
            raise ImportFailure('IMPORT_WRONG_SYSTEM', stage='signature', args={'system_version': system, 'wanted': wanted})
    else:
        ctx.journal.note('accepts_system applies to the Workstation only')
    kb = m['kb']
    ctx.report.data['kb'].update(generation=kb['generation'], fingerprint=kb['fingerprint'], created=kb['created'],
                                 counts={x: kb[x] for x in ('documents', 'chunks', 'points')})
    build_units(ctx)
    desired_units(ctx)
    ctx.journal.data['manifest'] = m
    ctx.journal.data['label'] = disc.label
    ctx.journal.save()
    ctx.report.write()
    return m
