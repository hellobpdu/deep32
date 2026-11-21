"""Run the existing signed disc-2 importer in the Qdrant network namespace."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys


IMPORTER = Path('/opt/deep32/import/d32import.py')
JOURNAL = Path('/var/lib/deep32/import/journal/state.json')


def import_arguments(disc, journal=JOURNAL):
    """Resume the same durable import without changing its recorded source."""
    if disc != '/media/content':
        raise ValueError('Mount the verified content disc at /media/content before importing.')
    args = ['--node', 'forge']
    if journal.exists():
        state = json.loads(journal.read_text(encoding='ascii'))
        if (state.get('device') == 'folder' and state.get('source_folder') != disc
                or state.get('qdrant_url') not in (None, 'http://127.0.0.1:6333')):
            raise ValueError('Restore the same content mount and Qdrant endpoint before resuming the import.')
        if state.get('outcome') is None:
            return args + ['--resume', '--qdrant-url', 'http://127.0.0.1:6333']
    return args + ['--folder', disc, '--qdrant-url', 'http://127.0.0.1:6333']


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    ap.add_argument('action', choices=('import',))
    ap.add_argument('--disc', default='/media/content')
    options = ap.parse_args(argv)
    try:
        args = import_arguments(options.disc)
        spec = importlib.util.spec_from_file_location('d32_full_importer', IMPORTER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        print('Waiting for signed content verification and index import; keep the content disc mounted and use start again after an interruption.', flush=True)
        return module.main(args)
    except (OSError, ValueError, KeyError):
        print('The content import could not resume; restore the same signed content disc and check full-role status.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
