# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jeffrey A. Cummings
"""MinerU worker adapted from the authorised inventoried worker interface."""
import json
from pathlib import Path
import time
from mineru.parser import parse
from mineru.parser.writer import FileBasedDataWriter


def convert(source, output, tier='advanced', page_range=None):
    kwargs = {'tier': tier, 'ocr_mode': 'auto'}
    if page_range:
        kwargs['page_range'] = page_range
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    started = time.time()
    result = parse(Path(source), **kwargs)
    result.save(FileBasedDataWriter(str(output)))
    # Same summary file as the inventoried production worker writes.
    info = {'ok': True, 'tier': tier, 'page_range': page_range, 'pages': len(result.pages),
            'elapsed_seconds': round(time.time() - started, 3)}
    (output / 'worker_result.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
