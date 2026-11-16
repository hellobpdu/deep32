"""validate_block.py: Check corpus block records and archive streams independently.

Usage:
  python3 -I -B validate_block.py DIR [--set] [--manifest FILE] [--xref-manifest FILE] [--overrides FILE] [--families FILE] [--vocabulary FILE] [--cut-keys FILE] [--meta-patterns FILE] [--marking-patterns FILE]
Exit codes:
  0 Valid block or set.
  1 Usage error.
  2 Missing, unreadable or malformed input option.
  3 Invalid block or set.
Checks:
  V01 Canonical ASCII JSON with LF line endings.
  V02 Supported format and format version.
  V03 Top-level fields and their order.
  V04 Block name, folder name and default search setting.
  V05 Version and UTC creation time.
  V06 Folder contents and part layout.
  V07 Part and joined archive sizes and hashes.
  V08 Ustar headers, padding and end markers.
  V09 Safe, unique and ordered member names.
  V10 Document records, member hashes and totals.
  V11 Member size limit.
  V12 Excluded keys absent throughout the block.
  V13 Agreement with the input manifest and overrides.
  V14 Family lists and counts.
  V15 Metadata patterns absent from string values.
  V16 Handling patterns absent from member bytes.
  V17 Record value types.
  V18 Converter fields and types.
  V19 Set contents, block validity and unique keys.
Output:
  Standard output is exactly one JSON object on one line, with per-block
  check results, counts and sorted findings. Input and usage errors instead
  give an error object and a numeric exit code. Human messages go to standard
  error. Findings contain identifiers and offsets, never member bytes.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
import re
import sys

BLOCKS = ('rfc', 'cisco', 'powershell', 'system')
LIMIT = 64000000
PART_LIMIT = 1992294400
SHA_RE = re.compile(r'[0-9a-f]{64}\Z')
KEY_RE = re.compile(r'[a-z0-9][a-z0-9-]{0,89}\Z')
DOC_FIELDS = ['doc_key', 'title', 'doc_kind', 'platform', 'release', 'release_note', 'path', 'bytes', 'sha256',
              'license', 'terms_url', 'source_url', 'page_url', 'redistribute', 'near_duplicate_of', 'page_map']
USAGE = __doc__.split('Usage:\n', 1)[1].split('Exit codes:', 1)[0].rstrip()


class InputError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def encode_json(value):
    return (json.dumps(value, indent=2, ensure_ascii=True, separators=(',', ': ')) + '\n').encode('ascii')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def no_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def decode_json(data):
    return json.loads(data.decode('utf-8'), object_pairs_hook=no_duplicates,
                      parse_constant=lambda text: (_ for _ in ()).throw(ValueError()))


def read_option(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except OSError:
        raise InputError(2, 'input missing or unreadable: ' + str(path)) from None


def text_option(data, label, ascii_only=False):
    try:
        text = data.decode('ascii' if ascii_only else 'utf-8')
    except UnicodeError:
        raise InputError(2, 'invalid encoding: ' + label) from None
    if text.startswith('\ufeff') or '\r' in text:
        raise InputError(2, 'byte-order mark or CR in ' + label)
    return text


def patterns(data, label, binary=False):
    text = text_option(data, label)
    result = []
    for line in text.split('\n'):
        if not line or line.startswith('#'):
            continue
        if '\t' not in line:
            raise InputError(2, 'malformed pattern line: ' + label)
        pattern, reason = line.split('\t', 1)
        if not reason.startswith('# '):
            raise InputError(2, 'malformed pattern reason: ' + label)
        try:
            result.append((re.compile(pattern.encode('utf-8') if binary else pattern), reason[2:]))
        except re.error:
            raise InputError(2, 'pattern does not compile: ' + label) from None
    return result


def parse_overrides(data):
    text = text_option(data, 'overrides')
    lines = text.splitlines()
    if not lines or lines[0] != 'doc_key\tfield\told_sha256\tnew_value' or not text.endswith('\n'):
        raise InputError(2, 'malformed overrides header or line ending')
    rows, seen = [], set()
    for line in lines[1:]:
        row = line.split('\t')
        if len(row) != 4:
            raise InputError(2, 'malformed override row')
        key, field, old, new = row
        if not KEY_RE.fullmatch(key) or field not in ('title', 'platform', 'release_note') or not SHA_RE.fullmatch(old) or not new or any(ord(c) < 32 or ord(c) > 126 for c in new) or (key, field) in seen:
            raise InputError(2, 'invalid or duplicate override row')
        rows.append(dict(doc_key=key, field=field, old_sha256=old, new_value=new))
        seen.add((key, field))
    return rows


def load_options(a):
    values = {}
    for name in ('manifest', 'xref_manifest', 'cut_keys', 'overrides', 'families', 'vocabulary', 'meta_patterns', 'marking_patterns'):
        path = getattr(a, name)
        if not path:
            values[name] = None
            continue
        data = read_option(path)
        if name in ('manifest', 'xref_manifest'):
            try:
                value = decode_json(data)
                if not isinstance(value, list) or any(not isinstance(e, dict) or not isinstance(e.get('doc_key'), str) or not isinstance(e.get('doc_type'), str) for e in value):
                    raise ValueError()
            except (ValueError, UnicodeError):
                raise InputError(2, 'manifest must be a UTF-8 JSON list of document objects') from None
        elif name == 'cut_keys':
            text = text_option(data, 'cut keys', True)
            value = {line.strip(' \t') for line in text.splitlines() if line.strip(' \t') and not line.strip(' \t').startswith('#')}
        elif name == 'overrides':
            value = parse_overrides(data)
        elif name == 'vocabulary':
            text = text_option(data, 'vocabulary', True)
            value = text.splitlines()
            if not text.endswith('\n') or text.endswith('\n\n') or not value or len(set(value)) != len(value) or any(not s or s != s.strip() for s in value):
                raise InputError(2, 'malformed vocabulary')
        elif name == 'families':
            # The csv reader accepts LF or CRLF rows, as the csv module writes either.
            try:
                text = data.decode('utf-8')
            except UnicodeError:
                raise InputError(2, 'invalid encoding: families') from None
            if text.startswith('\ufeff'):
                raise InputError(2, 'byte-order mark in families')
            try:
                reader = csv.reader(io.StringIO(text, newline=''), delimiter='\t', strict=True)
                header = next(reader)
                if header.count('doc_id') != 1 or header.count('families') != 1:
                    raise ValueError()
                ki, fi = header.index('doc_id'), header.index('families')
                value = {}
                for row in reader:
                    if len(row) != len(header) or row[ki] in value or not row[fi]:
                        raise ValueError()
                    value[row[ki]] = row[fi].split('; ')
            except (ValueError, StopIteration, csv.Error):
                raise InputError(2, 'malformed families file') from None
        else:
            value = patterns(data, name, name == 'marking_patterns')
        values[name] = value
    return values


def epoch(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', value):
        raise ValueError()
    return int(datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp())


def integer(value, low=0, high=None):
    return type(value) is int and value >= low and (high is None or value <= high)


def hash_string(value):
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def refs_valid(value):
    return isinstance(value, list) and all(
        isinstance(r, dict) and list(r) == ['doc_key', 'overlap', 'lines'] and isinstance(r['doc_key'], str)
        and type(r['overlap']) in (int, float) and math.isfinite(r['overlap']) and 0 <= r['overlap'] <= 1
        and integer(r['lines']) for r in value)


class Stream:
    """Read named parts as one stream without creating a joined file."""
    def __init__(self, paths):
        self.paths = iter(paths)
        self.file = None
        self.offset = 0

    def read(self, size):
        chunks = []
        while size:
            if self.file is None:
                try:
                    self.file = open(next(self.paths), 'rb')
                except StopIteration:
                    break
            data = self.file.read(size)
            if not data:
                self.file.close()
                self.file = None
                continue
            chunks.append(data)
            size -= len(data)
            self.offset += len(data)
        return b''.join(chunks)

    def close(self):
        if self.file:
            self.file.close()


def validate(folder, options):
    checks = {'V%02d' % n: 'pass' for n in range(1, 19)}
    findings = []
    count = dict(documents=0, members=0, page_maps=0, text_bytes=0, redistribute_true=0, redistribute_false=0, near_duplicate_entries=0)
    result = dict(block=None, folder=folder, valid=False, checks=checks, findings=findings, counts=count)

    def fail(check, detail, key=None, member=None, field=None, offset=None):
        checks[check] = 'fail'
        findings.append(dict(check=check, doc_key=key, member=member, field=field, offset=offset, detail=detail))

    def early():
        for n in range(2 if checks['V01'] == 'fail' else 3, 19):
            checks['V%02d' % n] = 'not-run'
        return result, set()

    try:
        raw = read_option(os.path.join(folder, 'block.json'))
        obj = decode_json(raw)
        if not isinstance(obj, dict):
            raise ValueError()
    except (InputError, ValueError, UnicodeError):
        fail('V01', 'record is not readable JSON')
        return early()
    block = obj.get('block')
    result['block'] = block if isinstance(block, str) else None
    if any(b > 127 for b in raw) or b'\r' in raw or not raw.endswith(b'\n') or raw.endswith(b'\n\n') or encode_json(obj) != raw:
        fail('V01', 'record is not canonical ASCII JSON')
    if obj.get('format') != 'deep32-corpus-block' or type(obj.get('format_version')) is not int or obj.get('format_version') != 1:
        fail('V02', 'unsupported format or format version')
        for n in range(3, 19):
            checks['V%02d' % n] = 'not-run'
        return result, set()
    top = ['format', 'format_version', 'block', 'version', 'created', 'searched_by_default', 'archive', 'documents', 'bytes', 'converter']
    if block == 'cisco':
        top.append('families')
    top.append('docs')
    if list(obj) != top:
        fail('V03', 'top-level fields or order differ')
    if block not in BLOCKS or block != os.path.basename(os.path.normpath(folder)) or type(obj.get('searched_by_default')) is not bool or obj.get('searched_by_default') != (block in ('rfc', 'cisco')):
        fail('V04', 'block, folder or default search setting differs')
    mtime = None
    try:
        if not isinstance(obj.get('version'), str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', obj['version']):
            raise ValueError()
        mtime = epoch(obj.get('created'))
    except (ValueError, OverflowError, OSError):
        fail('V05', 'invalid version or UTC creation time')
    docs = obj.get('docs', [])
    if not isinstance(docs, list):
        docs = []
        fail('V17', 'docs must be a list', field='docs')
    if not integer(obj.get('documents')) or not integer(obj.get('bytes')):
        fail('V17', 'document count and bytes must be non-negative integers')
    archive = obj.get('archive')
    paths = []
    if not isinstance(archive, dict):
        archive = {}
        fail('V17', 'archive must be an object', field='archive')
    if list(archive) != ['file', 'size', 'sha256', 'part_size', 'parts'] or not integer(archive.get('size')) or not hash_string(archive.get('sha256')) or not integer(archive.get('part_size'), 512, PART_LIMIT) or not isinstance(archive.get('parts'), list):
        fail('V17', 'invalid archive fields or types', field='archive')
    filename = archive.get('file')
    expected_filename = str(block) + '-' + str(obj.get('version')) + '.tar'
    safe_file = isinstance(filename, str) and filename == expected_filename and re.fullmatch(r'(rfc|cisco|powershell|system)-[0-9]+\.[0-9]+\.[0-9]+\.tar', filename)
    parts = archive.get('parts', [])
    if not isinstance(parts, list):
        parts = []
    allowed = ['block.json']
    part_size, archive_size = archive.get('part_size'), archive.get('size')
    layout_ok = bool(safe_file) and integer(part_size, 512, PART_LIMIT) and integer(archive_size)
    if layout_ok and bool(parts) != (archive_size > part_size):
        layout_ok = False
    if parts:
        if len(parts) > 999:
            layout_ok = False
        for i, p in enumerate(parts, 1):
            if not isinstance(p, dict):
                layout_ok = False
                fail('V17', 'invalid part record', field='archive.parts')
                continue
            name = str(filename) + '.part%03d' % i
            if list(p) != ['index', 'name', 'size', 'sha256'] or not integer(p.get('index'), 1) or not integer(p.get('size'), 1) or not hash_string(p.get('sha256')):
                fail('V17', 'invalid part fields or types', field='archive.parts')
            if p.get('name') != name or p.get('index') != i or not integer(p.get('size'), 1, part_size if integer(part_size, 512, PART_LIMIT) else 0) or (i < len(parts) and p.get('size') != part_size):
                layout_ok = False
            if safe_file:
                allowed.append(name)
                paths.append(os.path.join(folder, name))
    elif safe_file:
        allowed.append(filename)
        paths.append(os.path.join(folder, filename))
    try:
        entries = sorted(os.listdir(folder))
        if entries != sorted(allowed) or any(not os.path.isfile(os.path.join(folder, name)) for name in allowed):
            layout_ok = False
    except OSError:
        layout_ok = False
    if not layout_ok:
        fail('V06', 'folder contents or part layout differ')
    joined_hash, joined_size = hashlib.sha256(), 0
    files_ok = bool(paths)
    for i, path in enumerate(paths):
        h, size = hashlib.sha256(), 0
        try:
            with open(path, 'rb') as f:
                while True:
                    data = f.read(1048576)
                    if not data:
                        break
                    h.update(data)
                    joined_hash.update(data)
                    size += len(data)
                    joined_size += len(data)
            if parts:
                p = parts[i]
                if not isinstance(p, dict) or p.get('size') != size or p.get('sha256') != h.hexdigest():
                    fail('V07', 'part size or hash differs', member=os.path.basename(path))
        except OSError:
            files_ok = False
            fail('V07', 'archive file missing or unreadable', member=os.path.basename(path))
    if not paths or joined_size != archive.get('size') or joined_hash.hexdigest() != archive.get('sha256'):
        fail('V07', 'joined archive size or hash differs')
    members = []
    marking = options['marking_patterns']
    if marking is None:
        checks['V16'] = 'not-run'
    if files_ok:
        stream = Stream(paths)
        ended = False
        try:
            while True:
                offset = stream.offset
                header = stream.read(512)
                if len(header) != 512:
                    fail('V08', 'missing or short end marker', offset=offset)
                    break
                if header == b'\0' * 512:
                    second = stream.read(512)
                    if second != b'\0' * 512:
                        fail('V08', 'missing second zero end marker', offset=offset + 512)
                    while True:
                        tail = stream.read(1048576)
                        if not tail:
                            break
                        if any(tail):
                            fail('V08', 'non-zero bytes after archive end', offset=stream.offset - len(tail))
                    ended = True
                    break

                def number(start, end):
                    text = header[start:end].rstrip(b'\0 ')
                    if not text or not re.fullmatch(b'[0-7]+', text):
                        raise ValueError()
                    return int(text, 8)

                try:
                    name = header[:100].split(b'\0', 1)[0].decode('ascii')
                    size = number(124, 136)
                    checksum = number(148, 156)
                    correct = sum(header[:148]) + 8 * 32 + sum(header[156:])
                    attributes = (number(100, 108) == 0o444 and number(108, 116) == 0 and number(116, 124) == 0
                                  and number(136, 148) == mtime and header[156:157] == b'0'
                                  and header[257:263] == b'ustar\0' and header[263:265] == b'00'
                                  and not any(header[157:257]) and not any(header[265:329]) and not any(header[345:500]))
                    if checksum != correct or not attributes:
                        fail('V08', 'header checksum or attributes differ', member=name, offset=offset)
                except (ValueError, UnicodeError):
                    fail('V08', 'invalid header encoding or octal field', offset=offset)
                    break
                if not re.fullmatch(r'(docs/[a-z0-9][a-z0-9-]{0,89}\.txt|pages/[a-z0-9][a-z0-9-]{0,89}\.tsv)', name) or len(name.encode('ascii')) > 100 or (members and name.encode('ascii') <= members[-1]['name'].encode('ascii')):
                    fail('V09', 'member name is unsafe, duplicated or out of order', member=name, offset=offset)
                if size > LIMIT:
                    fail('V11', 'member exceeds size limit', member=name, offset=offset)
                h = hashlib.sha256()
                remaining = size
                chunks = []
                while remaining:
                    data = stream.read(min(1048576, remaining))
                    if not data:
                        fail('V08', 'short member data', member=name, offset=stream.offset)
                        break
                    h.update(data)
                    if marking is not None and size <= LIMIT:
                        chunks.append(data)
                    remaining -= len(data)
                padding = stream.read((-size) % 512)
                if remaining or len(padding) != (-size) % 512 or any(padding):
                    fail('V08', 'invalid member padding or length', member=name, offset=stream.offset)
                    break
                members.append(dict(name=name, bytes=size, sha256=h.hexdigest()))
                if marking is not None and size <= LIMIT:
                    data = b''.join(chunks)
                    key = name.rsplit('/', 1)[-1].rsplit('.', 1)[0]
                    for pattern, reason in marking:
                        for match in pattern.finditer(data):
                            fail('V16', reason, key=key, member=name, offset=match.start())
            if not ended or stream.offset % 512:
                fail('V08', 'archive length or end markers invalid', offset=stream.offset)
        except OSError:
            fail('V08', 'archive stream could not be read', offset=stream.offset)
        finally:
            stream.close()
    else:
        fail('V08', 'archive stream unavailable')
    member_index = {m['name']: m for m in members}
    seen_keys, expected_members, previous = set(), set(), None
    total = 0
    for d in docs:
        if not isinstance(d, dict):
            fail('V17', 'document entry must be an object')
            fail('V10', 'document entry is not an object')
            continue
        key = d.get('doc_key')
        label = key if isinstance(key, str) else None
        names = DOC_FIELDS + (['family'] if block == 'cisco' else [])
        if list(d) != names or not isinstance(key, str) or not KEY_RE.fullmatch(key) or key in seen_keys or (previous is not None and isinstance(key, str) and key <= previous):
            fail('V10', 'document fields, keys or order differ', key=label)
        if isinstance(key, str):
            seen_keys.add(key)
            previous = key
        for field in ('doc_key', 'title', 'doc_kind', 'path', 'license'):
            if not isinstance(d.get(field), str) or (field == 'license' and not d.get(field)):
                fail('V17', 'expected string value', key=label, field=field)
        for field in ('platform', 'release', 'release_note', 'terms_url', 'source_url', 'page_url'):
            if d.get(field) is not None and not isinstance(d.get(field), str):
                fail('V17', 'expected string or null', key=label, field=field)
        if not integer(d.get('bytes')) or not hash_string(d.get('sha256')) or type(d.get('redistribute')) is not bool or not refs_valid(d.get('near_duplicate_of')):
            fail('V17', 'invalid size, hash, sharing flag or cross reference', key=label)
        if block == 'cisco' and (not isinstance(d.get('family'), list) or any(not isinstance(n, str) for n in d['family'])):
            fail('V17', 'family must be a list of strings', key=label, field='family')
        path = 'docs/' + str(key) + '.txt'
        expected_members.add(path)
        m = member_index.get(path)
        if d.get('path') != path or m is None or (m['bytes'], m['sha256']) != (d.get('bytes'), d.get('sha256')):
            fail('V10', 'document member size, hash or path differs', key=label, member=path)
        page_path = 'pages/' + str(key) + '.tsv'
        page = d.get('page_map')
        page_member = member_index.get(page_path)
        if page is not None:
            expected_members.add(page_path)
            if not isinstance(page, dict) or list(page) != ['path', 'bytes', 'sha256'] or not integer(page.get('bytes')) or not hash_string(page.get('sha256')) or not isinstance(page.get('path'), str):
                fail('V17', 'invalid page-map record', key=label, field='page_map')
            if not isinstance(page, dict) or page.get('path') != page_path or page_member is None or (page.get('bytes'), page.get('sha256')) != (page_member['bytes'], page_member['sha256']):
                fail('V10', 'page-map member differs', key=label, member=page_path)
        elif page_member is not None:
            fail('V10', 'unrecorded page-map member', key=label, member=page_path)
        if integer(d.get('bytes')):
            total += d['bytes']
        count['redistribute_true'] += d.get('redistribute') is True
        count['redistribute_false'] += d.get('redistribute') is False
        if isinstance(d.get('near_duplicate_of'), list):
            count['near_duplicate_entries'] += len(d['near_duplicate_of'])
    if set(member_index) != expected_members or len(member_index) != len(members) or obj.get('documents') != len(docs) or obj.get('bytes') != total:
        fail('V10', 'member set or document totals differ')
    count.update(documents=len(docs), members=len(members), page_maps=sum(m['name'].startswith('pages/') for m in members), text_bytes=total)
    manifest, xref = options['manifest'], options['xref_manifest']
    cut = set(options['cut_keys'] or ())
    for e in (manifest or []) + (xref or []):
        if e['doc_type'] not in BLOCKS:
            cut.add(e['doc_key'])
    if options['cut_keys'] is None and manifest is None:
        checks['V12'] = 'not-run'
    else:
        for key in sorted(cut):
            if key in seen_keys or any(m['name'].rsplit('/', 1)[-1].rsplit('.', 1)[0] == key for m in members) or any(isinstance(d, dict) and isinstance(d.get('near_duplicate_of'), list) and any(isinstance(r, dict) and r.get('doc_key') == key for r in d['near_duplicate_of']) for d in docs) or key.encode('utf-8') in raw:
                fail('V12', 'excluded key occurs in the block', key=key)
    if manifest is None:
        checks['V13'] = 'not-run'
    else:
        selected = {e['doc_key']: e for e in manifest if e['doc_type'] == block}
        if seen_keys != set(selected):
            fail('V13', 'document set differs from manifest')
        changed = {}
        for row in options['overrides'] or []:
            key, field = row['doc_key'], row['field']
            if key in selected:
                old = selected[key].get(field)
                if not isinstance(old, str) or sha(old.encode('utf-8')) != row['old_sha256']:
                    fail('V13', 'stale override', key=key, field=field)
                changed[(key, field)] = row['new_value']
        eligible = (set(selected) | {e['doc_key'] for e in manifest + (xref or []) if e['doc_type'] in BLOCKS[:3]}) - cut
        for d in docs:
            if not isinstance(d, dict) or not isinstance(d.get('doc_key'), str) or d['doc_key'] not in selected:
                continue
            key = d['doc_key']
            source = selected[key]
            for field in ('title', 'doc_kind', 'platform', 'release', 'release_note', 'bytes', 'sha256', 'license', 'terms_url', 'source_url', 'page_url', 'redistribute'):
                if d.get(field) != changed.get((key, field), source.get(field)):
                    fail('V13', 'copied metadata differs', key=key, field=field)
            refs = source.get('near_duplicate_of')
            if not isinstance(refs, list) or any(not isinstance(r, dict) for r in refs):
                fail('V13', 'manifest cross references malformed', key=key, field='near_duplicate_of')
            elif d.get('near_duplicate_of') != [r for r in refs if isinstance(r.get('doc_key'), str) and r['doc_key'] in eligible]:
                fail('V13', 'filtered cross references differ', key=key, field='near_duplicate_of')
    if block == 'cisco':
        family_rows = obj.get('families')
        family_names = []
        if not isinstance(family_rows, list):
            fail('V14', 'families must be a list')
            fail('V17', 'families must be a list', field='families')
            family_rows = []
        for row in family_rows:
            if not isinstance(row, dict) or list(row) != ['name', 'documents'] or not isinstance(row.get('name'), str) or not row.get('name') or not integer(row.get('documents')):
                fail('V17', 'invalid family count record', field='families')
                fail('V14', 'invalid family count record')
                continue
            name = row['name']
            family_names.append(name)
            if row['documents'] != sum(isinstance(d, dict) and isinstance(d.get('family'), list) and name in d['family'] for d in docs):
                fail('V14', 'family document count differs', field=name)
        if len(set(family_names)) != len(family_names) or not family_names or (options['vocabulary'] is not None and family_names != options['vocabulary']):
            fail('V14', 'family names differ or repeat')
        tags = options['families']
        if tags is not None and set(tags) != seen_keys:
            fail('V14', 'family input key set differs')
        for d in docs:
            if not isinstance(d, dict):
                continue
            names = d.get('family')
            if not isinstance(names, list) or not names or any(not isinstance(n, str) or n not in family_names for n in names) or len(set(n for n in names if isinstance(n, str))) != len(names):
                fail('V14', 'invalid document family list', key=d.get('doc_key') if isinstance(d.get('doc_key'), str) else None)
            elif names != sorted(names, key=family_names.index) or (tags is not None and (not isinstance(d.get('doc_key'), str) or tags.get(d['doc_key']) != names)):
                fail('V14', 'document families differ or are out of order', key=d.get('doc_key') if isinstance(d.get('doc_key'), str) else None)
    metadata = options['meta_patterns']
    if metadata is None:
        checks['V15'] = 'not-run'
    else:
        def visit(value, path, key=None, field=None):
            if isinstance(value, str):
                for pattern, reason in metadata:
                    if pattern.search(value):
                        fail('V15', reason, key=key, field=field or path)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    visit(item, path + '[%d]' % i, key, field)
            elif isinstance(value, dict):
                if path.startswith('docs[') and isinstance(value.get('doc_key'), str):
                    key = value['doc_key']
                for name, item in value.items():
                    visit(item, path + '.' + name if path else name, key, name if key else None)
        visit(obj, '')
    conv = obj.get('converter')
    if not isinstance(conv, dict) or list(conv) != ['name', 'version', 'note'] or not isinstance(conv.get('name'), str) or not conv.get('name') or not (conv.get('version') is None or isinstance(conv.get('version'), str)) or not isinstance(conv.get('note'), str):
        fail('V18', 'invalid converter fields or types', field='converter')
    findings.sort(key=lambda f: (f['check'], f['doc_key'] or '', f['member'] or '', f['field'] or '', -1 if f['offset'] is None else f['offset']))
    result['valid'] = 'fail' not in checks.values()
    return result, seen_keys


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(1, message)

    def exit(self, status=0, message=None):
        raise InputError(1, message or 'invalid arguments')


def parse_args(argv):
    seen = set()
    for arg in argv:
        if arg.startswith('-'):
            name = arg.split('=', 1)[0]
            if name in seen:
                raise InputError(1, 'option given twice: ' + name)
            seen.add(name)
    p = Parser(add_help=False, allow_abbrev=False)
    p.add_argument('dir')
    p.add_argument('--set', action='store_true')
    for name in ('manifest', 'xref-manifest', 'overrides', 'families', 'vocabulary', 'cut-keys', 'meta-patterns', 'marking-patterns'):
        p.add_argument('--' + name)
    return p.parse_args(argv)


def main(argv):
    folder = None
    try:
        a = parse_args(argv)
        folder = a.dir
        if not os.path.isdir(folder):
            raise InputError(2, 'DIR missing or unreadable')
        options = load_options(a)
        blocks, all_keys, set_findings = [], set(), []
        set_status = 'pass' if a.set else 'not-run'

        def set_fail(detail, key=None):
            nonlocal set_status
            set_status = 'fail'
            set_findings.append(dict(check='V19', doc_key=key, member=None, field=None, offset=None, detail=detail))

        if a.set:
            try:
                entries = sorted(os.listdir(folder))
            except OSError:
                raise InputError(2, 'DIR unreadable') from None
            for name in entries:
                path = os.path.join(folder, name)
                if os.path.isdir(path) and os.path.isfile(os.path.join(path, 'block.json')):
                    report, keys = validate(path, options)
                    blocks.append(report)
                    if not report['valid'] or report['block'] != name or name.startswith('.partial-'):
                        set_fail('invalid block in set')
                    for key in sorted(keys & all_keys):
                        set_fail('document key occurs in two blocks', key)
                    all_keys |= keys
                else:
                    set_fail('unexpected file or folder in set')
            if not blocks:
                set_fail('set holds no block folder')
        else:
            if not os.path.isfile(os.path.join(folder, 'block.json')):
                raise InputError(2, 'DIR has no block.json')
            report, keys = validate(folder, options)
            blocks.append(report)
        set_findings.sort(key=lambda f: (f['check'], f['doc_key'] or '', f['detail']))
        valid = all(b['valid'] for b in blocks) and set_status != 'fail'
        result = dict(tool='deep32-validate-block', tool_version='1', dir=folder, valid=valid, blocks=blocks,
                      set_checks=dict(V19=set_status), set_findings=set_findings)
        code = 0 if valid else 3
        if code:
            print('block validation failed', file=sys.stderr)
    except InputError as exc:
        code = exc.code
        result = dict(tool='deep32-validate-block', tool_version='1', dir=folder, valid=False,
                      status='error', exit=code, error=str(exc))
        print(str(exc) + ('\nUsage:\n' + USAGE if code == 1 else ''), file=sys.stderr)
    print(json.dumps(result, ensure_ascii=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
