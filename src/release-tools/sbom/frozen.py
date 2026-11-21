"""Reconcile measured adapters with the frozen public SBOM contracts."""
from contract_validate import load_contract, validate_contract, validate_contract_set

def bind(path, normal):
    value = load_contract('sbom-input', path)
    # technical contract refusal text for review.
    def require(condition, detail):
        if not condition:
            raise ValueError('frozen SBOM contract differs: ' + detail)
    for frozen, measured in [('release', 'release'), ('source_commit', 'source_revision'),
                             ('source_date_epoch', 'source_date_epoch'),
                             ('generator_sha256', 'generator_sha256')]:
        require(value[frozen] == normal[measured], frozen)
    roots = {row['id']: row for row in value['input_roots']}
    inventories = {row['id']: row for row in normal['roots']}
    require(roots.keys() == inventories.keys(), 'input roots')
    files = {}
    for name, root in roots.items():
        for row in inventories[name]['files']:
            path = root['path'] + '/' + row['path']
            require(path not in files, 'duplicate public sealed file reference')
            files[path] = row
        reference = root['manifest']
        require(reference['path'] in files and files[reference['path']]['sha256'] == reference['sha256'],
                'sealed root manifest')
    def refs(rows):
        return sorted([dict(path=roots[row['root']]['path'] + '/' + row['path'], sha256=row['sha256'])
                       for row in rows], key=lambda row: row['path'])
    measured_components = {row['id']: row for row in normal['components']}
    require({row['id'] for row in value['components']} == measured_components.keys(), 'component closure')
    purposes = {'source': 'SOURCE', 'application': 'APPLICATION', 'deb': 'OPERATING-SYSTEM',
                'openbsd-package': 'OPERATING-SYSTEM', 'openbsd-set': 'OPERATING-SYSTEM',
                'base-package': 'OPERATING-SYSTEM', 'wheel': 'LIBRARY', 'sdist': 'SOURCE',
                'tool': 'APPLICATION', 'data': 'FILE'}
    for component in value['components']:
        item = measured_components[component['id']]
        for frozen, measured in [('name', 'name'), ('version', 'version'), ('scope', 'usage'),
                                 ('licence', 'licence_declared'), ('source_url', 'source_url')]:
            require(component[frozen] == item[measured], 'component ' + frozen)
        require(component['purpose'] == purposes.get(item['kind']), 'component purpose cannot be represented')
        require(sorted(component['dependencies']) == item['dependencies'], 'component dependencies')
        require(sorted(component['licence_evidence'], key=lambda row: row['path']) == refs(item['licence_evidence']),
                'license evidence')
        require(len(item['files']) == 1 and component['sha256'] == item['files'][0]['sha256'],
                'component digest needs one measured archive or source file')
        if component['base_inventory'] is not None:
            reference = component['base_inventory']
            require(reference['path'] in files and files[reference['path']]['sha256'] == reference['sha256'],
                    'base inventory')
    subjects = {row['file']['path']: row for row in normal['subjects']}
    require({row['path'] for row in value['subjects']} == subjects.keys(), 'subject closure')
    for subject in value['subjects']:
        item = subjects[subject['path']]
        require(all(subject[key] == (item['kind'] if key == 'kind' else item['file'][key])
                    for key in ['kind', 'size', 'sha256']), 'subject identity')
        require(sorted(subject['components']) == sorted(item['components']), 'subject components')
        aliases = sorted(subject['aliases'], key=lambda row: row['role'])
        expected = lambda roles: [dict(role=role, path=subject['path']) for role in sorted(roles)]
        require(aliases in [expected(set(item['aliases'])), expected(set(item['aliases']) | {item['role']})],
                'subject aliases')
        if item['kind'] == 'runtime':
            require(not any(row['path'].endswith('.spdx.json') or row['path'].rsplit('/', 1)[-1] in
                            {'SBOM-INDEX.json', 'GAPS.json'} for row in item['runtime']['members']),
                    'embedded SBOM metadata')
    for exception in value['exceptions']:
        reference = exception['approval']
        require(reference['path'] in files and files[reference['path']]['sha256'] == reference['sha256'],
                'exception approval')
    require(not value['exceptions'], 'approved exceptions need a lossless component evidence mapping')
    # Source gaps and opaque component hashes have no lossless frozen mapping.
    require(all(row['field'] != 'source' for row in normal['gaps']), 'source gap is unrepresentable')
    return value


def release_index(contract, normal, index, assets, encode, checksum):
    """Publish frozen index/gaps; all assets remain beside the measured subjects."""
    subjects = {row['file']['path']: row for row in normal['subjects']}
    original = {row['artefact']['path']: row for row in index['subjects']}
    rows = []
    for frozen in sorted(contract['subjects'], key=lambda row: row['path']):
        item, old = subjects[frozen['path']], original[frozen['path']]
        gaps = []
        for gap in normal['gaps']:
            if gap['target'] not in [item['id'], *item['components']]:
                continue
            if gap['field'] == 'components':
                # public technical refusal text.
                raise ValueError('frozen SBOM component gap needs component-level measured evidence')
            # public missing-input description.
            gaps.append(dict(component=gap['target'], scope='licences', missing_input='License evidence.',
                             reason=gap['reason'], approval=None))
        gap_reference = None
        if gaps:
            name = item['id'] + '.gaps.json'
            document = dict(format='deep32-sbom-gap', format_version=1, release=contract['release'],
                            subject_path=frozen['path'], gaps=gaps)
            validate_contract('sbom-gap', document)
            assets[name] = encode(document)
            gap_reference = dict(path=name, sha256=checksum(assets[name]))
        coverage = dict(components=item['component_coverage'], licences=item['licence_coverage'])
        content = [] if item['kind'] == 'container' else [dict(path=old['contents']['path'], sha256=old['contents']['sha256'])]
        for binding in frozen['contents_sboms']:
            name = binding['path'].rsplit('/', 1)[-1]
            if name not in assets or checksum(assets[name]) != binding['sha256']:
                # public technical refusal text.
                raise ValueError('frozen contents SBOM binding differs')
        rows.append(dict(subject={key: frozen[key] for key in ['kind', 'path', 'size', 'sha256', 'aliases']},
                         sbom=dict(asset=old['sbom']['path'], sha256=old['sbom']['sha256'], media_paths=[]),
                         contents=content, coverage=coverage, gap=gap_reference))
    result = dict(format='deep32-sbom-index', format_version=1, release=contract['release'], subjects=rows)
    validate_contract_set({'sbom-input': contract, 'sbom-index': result})
    assets.pop('GAPS.json')
    assets['SBOM-INDEX.json'] = encode(result)
    return result
