#!/usr/bin/env python3
"""Compute declared storage budgets for every bundle and backend without measuring.

The tool reads byte counts only: the length of each component ISO, the member
inventory on each component's media (deep32/PAYLOAD.json and the lengths of the
other media files), the image archives named by images/index.json, and the
member and carrier sizes of the source companions. It hashes no ISO and no
payload. The budgets come from capabilities.declared_budget, the same formula
the validator applies, and each row is labeled declared-upper-bound.

    python3 -I -B storage_declare.py --components PRODUCER/components \\
        --images PRODUCER/images --image-index PRODUCER/stage/payload/images/index.json \\
        --companion-inputs COMPANION-INPUTS.json --output STORAGE-MEASUREMENTS.json \\
        --summary STORAGE-DECLARED-SUMMARY.json

--measured FILE takes rows from an existing measurement document; a bundle and
backend found there keeps its measured row and gets no declared row.
"""

import argparse
import hashlib
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import alpha2_assemble as alpha

cap, contracts, whole = alpha.capabilities, alpha.contracts, alpha.whole
BACKENDS = {'docker-engine': ('download', 'data', 'docker'),
            'docker-desktop': ('download', 'data', 'docker', 'desktop-backing')}
GENERATED = frozenset({'SHA256SUMS', 'pins.json', 'deep32/PAYLOAD.json', 'deep32/PAYLOAD.SHA256',
                       'deep32/RUNTIME.json', 'deep32/payload/images/index.json'})
PAYLOAD = 'deep32/payload/'
LIMIT = 8 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def document(path, limit=LIMIT):
    raw = alpha._read(path, limit)
    return raw, contracts.strict_loads(raw, max_bytes=limit)


def size(value):
    require(type(value) is int and 0 <= value < 2 ** 53, 'A byte count is missing or out of range.')
    return value


def runtime_facts(bundle_id, media, iso):
    """Member, archive and ISO byte counts of one component, from names and lengths only."""
    media = whole.plain_path(media, directory=True)
    listed = set()
    for line in alpha._read(media / 'SHA256SUMS', LIMIT).decode('ascii').splitlines():
        digest, separator, name = line.partition('  ')
        contracts.safe_path(name)
        require(separator and len(digest) == 64 and name not in listed and name != 'SHA256SUMS',
                'Malformed media checksum list: ' + bundle_id)
        listed.add(name)
    present = {}
    for path in media.rglob('*'):
        if path.is_dir():
            whole.plain_path(path, directory=True)
        else:
            present[path.relative_to(media).as_posix()] = whole.plain_path(path).stat().st_size
    require(set(present) == listed | {'SHA256SUMS'} and GENERATED <= present.keys(),
            'The media tree and its checksum list name different files: ' + bundle_id)
    _, payload = document(media / 'deep32/PAYLOAD.json')
    require(type(payload) is list, 'Invalid payload inventory: ' + bundle_id)
    rows = {}
    for row in payload:
        require(type(row) is dict and set(row) == {'path', 'size', 'sha256'} and type(row['path']) is str
                and row['path'] not in rows, 'Invalid payload inventory row: ' + bundle_id)
        rows[row['path']] = size(row['size'])
    require({PAYLOAD + name for name in rows} == {name for name in present if name.startswith(PAYLOAD)}
            and all(present[PAYLOAD + name] == length for name, length in rows.items()),
            'The payload inventory differs from the media files: ' + bundle_id)
    _, index = document(media / 'deep32/payload/images/index.json')
    require(type(index) is list and index and all(
        type(row) is dict and type(row.get('path')) is str and row['path'] in rows for row in index),
        'The image index names an archive outside the payload inventory: ' + bundle_id)
    archives = sorted({row['path'] for row in index})
    members = {name: length for name, length in present.items() if name not in GENERATED}
    iso = whole.plain_path(iso)
    facts = dict(iso_bytes=iso.stat().st_size, member_bytes=sum(members.values()),
                 image_archive_bytes=sum(rows[name] for name in archives),
                 member_slack_bytes=cap.DECLARED_BLOCK_BYTES * len(members))
    require(facts['iso_bytes'] >= facts['member_bytes'], 'An ISO is smaller than its members: ' + bundle_id)
    count = str(len(members))
    sources = dict(
        iso_bytes='length of ' + iso.name,
        member_bytes=count + ' members: deep32/PAYLOAD.json sizes and the lengths of the other media files',
        image_archive_bytes='deep32/PAYLOAD.json sizes of ' + ', '.join(archives),
        member_slack_bytes=str(cap.DECLARED_BLOCK_BYTES) + ' bytes for each of ' + count + ' members')
    return facts, sources, hashlib.sha256(alpha._read(media / 'pins.json', LIMIT)).hexdigest()


def companion_facts(path):
    """Member and carrier byte counts of the nine source companions."""
    _, inputs = document(path)
    require(type(inputs) is dict and inputs.get('format') == 'deep32-source-build-owner-staging/1'
            and inputs.get('release') == alpha.RELEASE and type(inputs.get('carriers')) is list
            and type(inputs.get('companions')) is list, 'Expected the owner source-build companion inputs.')
    carriers = {}
    for row in inputs['carriers']:
        require(type(row) is dict and type(row.get('sha256')) is str and row['sha256'] not in carriers,
                'Invalid or repeated source carrier.')
        carriers[row['sha256']] = size(row.get('size'))
    result = {}
    for row in inputs['companions']:
        require(type(row) is dict and type(row.get('id')) is str and row['id'] not in result
                and type(row.get('members')) is list and type(row.get('bindings')) is list,
                'Invalid or repeated source companion.')
        used = sorted({binding.get('carrier_sha256') for binding in row['bindings'] if type(binding) is dict})
        require(used and all(digest in carriers for digest in used), 'A companion names an unknown carrier.')
        count = str(len(row['members']))
        facts = dict(carrier_bytes=sum(carriers[digest] for digest in used),
                     member_bytes=sum(size(member.get('size')) for member in row['members']),
                     member_slack_bytes=cap.DECLARED_BLOCK_BYTES * len(row['members']), image_archive_bytes=0)
        sources = dict(
            carrier_bytes='sizes of the source carriers ' + ', '.join(digest[:16] for digest in used),
            member_bytes=count + ' members of the companion inputs',
            member_slack_bytes=str(cap.DECLARED_BLOCK_BYTES) + ' bytes for each of ' + count + ' members',
            image_archive_bytes='a source companion has no image archives')
        result[row['id']] = (facts, sources)
    require(set(result) == {'source-build-' + key for key in alpha.BUNDLES},
            'Expected exactly the nine source companions of this release.')
    return result


def declared_row(bundle_id, kind, backend, facts, sources):
    domains = []
    for domain in BACKENDS[backend]:
        names = cap.declared_input_names(kind, domain)
        budget = cap.declared_budget(kind, backend, domain, {name: facts[name] for name in names})
        domains.append(dict(budget=budget, method=cap.DECLARED_METHOD, formula=dict(cap.DECLARED_FORMULA),
                            inputs=[dict(name=name, bytes=facts[name], source=sources[name]) for name in names]))
    return dict(bundle_id=bundle_id, backend=backend, domains=domains)


def measured_rows(path, hashes):
    """Rows of an existing measurement document that stay measured."""
    _, existing = document(path, cap.STORAGE_LIMIT)
    require(type(existing) is dict and existing.get('format') == cap.STORAGE_FORMAT
            and existing.get('release') == alpha.VERSION and type(existing.get('measurements')) is list
            and all(existing.get(key) == value for key, value in hashes.items()),
            'The measured document must bind this release, its pins and its image index.')
    rows = {}
    for row in existing['measurements']:
        require(type(row) is dict and set(row) == {'bundle_id', 'backend', 'domains'}
                and type(row['domains']) is list and (row['bundle_id'], row['backend']) not in rows
                and all(type(entry) is dict and entry.get('method') != cap.DECLARED_METHOD
                        for entry in row['domains']),
                'A row of the measured document is repeated, malformed or declared.')
        rows[row['bundle_id'], row['backend']] = row
    return rows


def prepare(args):
    components = whole.plain_path(args.components, directory=True)
    images = whole.plain_path(args.images, directory=True)
    bundles, pins = {}, set()
    for key in alpha.BUNDLES:
        facts, sources, pin = runtime_facts(key, components / key / 'media', images / alpha.image_name(key))
        bundles[key] = ('embedder' if key.startswith('embedder-') else key, facts, sources)
        pins.add(pin)
    require(len(pins) == 1, 'The nine components carry different pin registers.')
    for name, (facts, sources) in companion_facts(args.companion_inputs).items():
        bundles[name] = ('corresponding-source-build', facts, sources)
    hashes = dict(pins_sha256=pins.pop(),
                  image_index_sha256=hashlib.sha256(alpha._read(args.image_index, LIMIT)).hexdigest())
    measured = measured_rows(args.measured, hashes) if args.measured else {}
    require(all(bundle in bundles and backend in BACKENDS for bundle, backend in measured),
            'The measured document names an unknown bundle or backend.')
    rows, labels = [], {}
    for name, (kind, facts, sources) in bundles.items():
        for backend in BACKENDS:
            row = measured.get((name, backend)) or declared_row(name, kind, backend, facts, sources)
            labels[name, backend] = 'measured' if (name, backend) in measured else cap.DECLARED_METHOD
            rows.append(row)
    raw = contracts.canonical(dict(format=cap.STORAGE_FORMAT, release=alpha.VERSION, measurements=rows, **hashes))
    require(len(raw) <= cap.STORAGE_LIMIT, 'The storage document exceeds its size limit.')
    return raw, summary(raw, rows, labels, {name: facts for name, (_, facts, _) in bundles.items()})


def summary(raw, rows, labels, facts):
    """Per-row peaks and the Forge totals the preflight will ask for, in bytes and GiB."""
    peaks = {(row['bundle_id'], row['backend']): {entry['budget']['domain']: entry['budget']['peak_bytes']
                                                  for entry in row['domains']} for row in rows}
    forge = []
    for card in alpha.CLASSES:
        for backend, domains in BACKENDS.items():
            selected = ['core', 'embedder-' + card, 'ingestion']
            total = {domain: sum(peaks[name, backend][domain] for name in selected) for domain in domains[1:]}
            # The Desktop guest filesystem lives inside the virtual disk; the host needs data and backing.
            host = sum(value for domain, value in total.items()
                       if backend == 'docker-engine' or domain != 'docker')
            forge.append(dict(
                hardware_class=card, backend=backend, peak_bytes=total, host_bytes=host,
                peak_gib={domain: round(value / cap.GIB, 2) for domain, value in total.items()},
                host_gib=round(host / cap.GIB, 2),
                basis=cap.DECLARED_METHOD if any(labels[name, backend] == cap.DECLARED_METHOD
                                                 for name in selected) else 'measured'))
    return dict(format='deep32-declared-storage-summary/1', release=alpha.VERSION,
                storage_sha256=hashlib.sha256(raw).hexdigest(), formula=dict(cap.DECLARED_FORMULA),
                rows=[dict(bundle_id=name, backend=backend, method=labels[name, backend], peak_bytes=peaks[name, backend])
                      for name, backend in peaks],
                inputs=facts, forge=forge)


def report(result):
    lines = ['storage document sha256 ' + result['storage_sha256'],
             'rows: %d declared, %d measured' % (
                 sum(row['method'] == cap.DECLARED_METHOD for row in result['rows']),
                 sum(row['method'] != cap.DECLARED_METHOD for row in result['rows']))]
    for row in result['forge']:
        parts = ', '.join('%s %.2f GiB' % (domain, value) for domain, value in row['peak_gib'].items())
        lines.append('Forge %-12s %-14s %s; host filesystems %.2f GiB (%s)' % (
            row['hardware_class'], row['backend'], parts, row['host_gib'], row['basis']))
    return '\n'.join(lines)


def write_new(path, raw):
    path = Path(os.path.abspath(path))
    whole.plain_path(path.parent, directory=True)
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                     allow_abbrev=False)
    for name in ('components', 'images', 'image-index', 'companion-inputs'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--measured', type=Path, help='existing measurement document whose rows are kept')
    parser.add_argument('--output', type=Path, help='new file for the canonical storage document')
    parser.add_argument('--summary', type=Path, help='new file for the per-row peaks and Forge totals')
    args = parser.parse_args(argv)
    try:
        raw, result = prepare(args)
        if args.output:
            write_new(args.output, raw)
        if args.summary:
            write_new(args.summary, contracts.canonical(result))
    except (ValueError, OSError, whole.Refusal, KeyError, TypeError) as exc:
        detail = 'an input is missing or the output exists' if isinstance(exc, OSError) else str(exc)
        parser.exit(2, 'Declared storage refused: ' + detail + '\n')
    print(report(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
