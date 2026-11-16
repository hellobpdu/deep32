"""Adds the disc 2 importer and points the engine, the Cisco titles index and the package list at what it installs (release specification section 3).

What it changes
  Installs the disc 2 importer and moves its content paths.
Usage
  apply(tree_dir) returns the sorted created and changed paths.
Exit
  Raises before writing when an input is missing or a change is already applied.
"""
import os
import base64
import hashlib

NEW_FILES = {'distrib/site/usr/local/d32/libexec/d32-content-import': 'distrib/site/usr/local/d32/libexec/d32-content-import', 'distrib/site/usr/local/d32/libexec/d32-import/c6-strings.tsv': 'distrib/site/usr/local/d32/libexec/d32-import/c6-strings.tsv', 'distrib/site/usr/local/d32/libexec/d32-import/d32import.py': 'distrib/site/usr/local/d32/libexec/d32-import/d32import.py', 'distrib/site/usr/local/d32/libexec/d32-import/imp_c6.py': 'distrib/site/usr/local/d32/libexec/d32-import/imp_c6.py', 'distrib/site/usr/local/d32/libexec/d32-import/imp_disc.py': 'distrib/site/usr/local/d32/libexec/d32-import/imp_disc.py', 'distrib/site/usr/local/d32/libexec/d32-import/imp_files.py': 'distrib/site/usr/local/d32/libexec/d32-import/imp_files.py', 'distrib/site/usr/local/d32/libexec/d32-import/imp_fs.py': 'distrib/site/usr/local/d32/libexec/d32-import/imp_fs.py', 'distrib/site/usr/local/d32/libexec/d32-import/imp_index.py': 'distrib/site/usr/local/d32/libexec/d32-import/imp_index.py', 'distrib/site/usr/local/d32/libexec/d32-import/imp_journal.py': 'distrib/site/usr/local/d32/libexec/d32-import/imp_journal.py', 'distrib/site/usr/local/d32/libexec/d32-import/imp_manifest.py': 'distrib/site/usr/local/d32/libexec/d32-import/imp_manifest.py', 'distrib/site/usr/local/d32/libexec/d32-import/imp_paths.py': 'distrib/site/usr/local/d32/libexec/d32-import/imp_paths.py', 'distrib/site/usr/local/d32/libexec/d32-import/imp_report.py': 'distrib/site/usr/local/d32/libexec/d32-import/imp_report.py', 'distrib/site/usr/local/d32/libexec/d32-import/imp_switch.py': 'distrib/site/usr/local/d32/libexec/d32-import/imp_switch.py', 'distrib/site/usr/local/d32/libexec/d32-import/imp_units.py': 'distrib/site/usr/local/d32/libexec/d32-import/imp_units.py', 'distrib/site/usr/local/d32/libexec/d32-import/vendor/VENDOR.txt': 'distrib/site/usr/local/d32/libexec/d32-import/vendor/VENDOR.txt', 'distrib/site/usr/local/d32/libexec/d32-import/vendor/f32_sse.py': 'distrib/site/usr/local/d32/libexec/d32-import/vendor/f32_sse.py', 'distrib/site/usr/local/d32/libexec/d32-import/vendor/kb_bundle.py': 'distrib/site/usr/local/d32/libexec/d32-import/vendor/kb_bundle.py', 'distrib/site/usr/local/d32/libexec/d32-import/vendor/kb_rules.json': 'distrib/site/usr/local/d32/libexec/d32-import/vendor/kb_rules.json', 'distrib/site/usr/local/d32/libexec/d32-import/vendor/mkindex.py': 'distrib/site/usr/local/d32/libexec/d32-import/vendor/mkindex.py', 'distrib/site/usr/local/d32/libexec/d32-import/vendor/openpgp_verify.py': 'distrib/site/usr/local/d32/libexec/d32-import/vendor/openpgp_verify.py'}
SHA256 = {
    'distrib/site/usr/local/d32/libexec/d32-content-import': 'c9bdf2c7b616a4ea28179d4743360c1975ede45f3eb03808b651bbfd578d6a78',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/c6-strings.tsv': '618da7d8e327a59f6c5aa954181fd6b736802342a2e068f881586575c0d7ceaf',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/d32import.py': 'e3587ab61a1e0ca69b65b8e70f1d88686f967fce7a120843a3478c1b0a005fe3',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/imp_c6.py': '822d442b6bb377bee2d5a385a8a4c3336f8d7d36c0fbb5ed4d93329b15974516',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/imp_disc.py': '00d27fb41c92435bc6915c2ed3c29c94eeb961eb29aa27cbacfb03f078aa29d2',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/imp_files.py': '1bd7c6ad192e8c5a3d27dda32a7b7e8cc617f03a9beecfc6c4a10cddab47225a',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/imp_fs.py': '774317919178c7c9321daadccfa548e871b9e3dcd665017dedec0dd9b73866e3',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/imp_index.py': '63784518b322c798d9e5a755c7a276a3cadaa33d87b9c0629910bfda9ab6c0aa',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/imp_journal.py': 'ea3d2f67617a9cb6d4dc453bff594c60a7ba2fbc0ae2e9e992ca39f1eac00d9d',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/imp_manifest.py': '377e5b0438f2cf0f5d9777fc2ce8eee8625142d97bac3b53c0283ad04621abe3',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/imp_paths.py': '3d6e01ef7ac14eeb2a6b617112778c1124bf474575a05c5f1bf676a2748305a9',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/imp_report.py': 'd13c1afe26f5bf3c947d8f9c0759475dbb2f12ecfbbeec24ba87d84d64972d4d',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/imp_switch.py': 'e0a56b827a7172488af937133f42c0bfc450a4965475ba96a7606c648c177ace',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/imp_units.py': 'fd60c0b1722e725c04f3c1addd89de816a8aa153a9c1eae0fe4b66cc458bd2af',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/vendor/VENDOR.txt': '67b6948187cca27115795ab9de9dbbb2f21f2e5b9ec8c87172ab8c46e43ad7e8',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/vendor/f32_sse.py': '913d0d0b0d817bd11a612463e91c3925f4cb0bc01901dc67a0d5713f7c1d546f',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/vendor/kb_bundle.py': 'b8e45d381fe9f1ce758f6694891591be5e3e62c960b3a9d909a928d45ec46b1b',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/vendor/kb_rules.json': '7852cbef4d14e9939d136d8c330589cd1103648e0148c86441fca0a2ac52e493',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/vendor/mkindex.py': 'a49fbacb1b7a89fdef5cea70e3939204cf8b6d5a2fc9b1f10fce28deae86cc4f',  # sha256  # sha256 or public signing fingerprint
    'distrib/site/usr/local/d32/libexec/d32-import/vendor/openpgp_verify.py': 'bb65d5f4724cad162d7efe71bd47e32cbedceef6360d095a3de16ba540ffc72d',  # sha256  # sha256 or public signing fingerprint
}
EDITS = [('ship/site/etc/d32/site.d/66-ship-node.sh', 'RAG_ROOT=/d32/active/gen/current/root\n', 'RAG_ROOT=/d32/libcontent/kb/root\n'), ('ship/site/etc/d32/site.d/66-ship-node.sh', 'RAG_QDRANT_URL=file:/d32/active/gen/current/dense\n', 'RAG_QDRANT_URL=file:/d32/libcontent/kb/dense\n'), ('node/shims/etc/d32/core.env.example', 'RAG_ROOT=/d32/active/gen/current/root\n', 'RAG_ROOT=/d32/libcontent/kb/root\n'), ('node/shims/etc/d32/core.env.example', '# degraded; with exact_local: RAG_QDRANT_URL=file:/d32/active/gen/current/dense and "inet" goes\n', '# degraded; with exact_local: RAG_QDRANT_URL=file:/d32/libcontent/kb/dense and "inet" goes\n'), ('library/src/lib/document_route.h', '#define CISCO_CATALOGUE_PATH "/d32/a/share/e26/cisco-public.sqlite"\n', '#define CISCO_CATALOGUE_PATH "/d32/libcontent/cisco/cisco-public.sqlite"\n')]
APPEND = ('ship/install/pkg-roots', '# the disc 2 importer: gpgv checks the signature of disc 2 (contract C2 section 4)\ngnupg\n')

def apply(tree_dir):
    pending = {}
    for rel in sorted(NEW_FILES):
        path = os.path.join(tree_dir, *rel.split('/'))
        if os.path.lexists(path):
            raise ValueError('already applied: ' + rel)
        with open(os.path.join(os.path.dirname(__file__), '902-disc2-importer-sources', *NEW_FILES[rel].split('/')), 'rb') as source_file:
            data = source_file.read()
        if hashlib.sha256(data).hexdigest() != SHA256[rel]:
            raise ValueError('embedded hash: ' + rel)
        pending[rel] = data
    for rel, old, new in EDITS:
        path = os.path.join(tree_dir, *rel.split('/'))
        if rel not in pending:
            with open(path, 'rb') as f:
                pending[rel] = f.read()
        count = pending[rel].count(old.encode('ascii'))
        if count != 1:
            raise ValueError(rel + ': count ' + str(count))
        pending[rel] = pending[rel].replace(old.encode('ascii'), new.encode('ascii'))
    rel, suffix = APPEND
    path = os.path.join(tree_dir, *rel.split('/'))
    with open(path, 'rb') as f:
        data = f.read()
    if not data.endswith(b'xv\n') or b'gnupg' in data.splitlines():
        raise ValueError(rel + ': append anchor')
    pending[rel] = data + suffix.encode('ascii')
    for rel, data in sorted(pending.items()):
        path = os.path.join(tree_dir, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        with open(path, 'rb') as f:
            if hashlib.sha256(f.read()).digest() != hashlib.sha256(data).digest():
                raise ValueError('write hash: ' + rel)
    return sorted(pending)
