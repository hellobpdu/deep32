# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jeffrey A. Cummings
"""Portable PyMuPDF baseline: prepare, extract in page order, clean, write source map.
This is not a claim of equivalence to the production round-specific conversion chain.
Use --backend production only after the staged production adapter passes the corpus gate.
"""
import json
from pathlib import Path
import re
import pymupdf


def convert(source, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    pages = []
    with pymupdf.open(source) as document:
        for number, page in enumerate(document, 1):
            text = page.get_text('text', sort=True).replace('\x00', '')
            text = '\n'.join(line.rstrip() for line in text.splitlines()).strip()
            text = re.sub(r'\n{3,}', '\n\n', text)
            pages.append({'page': number, 'text': text})
    # Empty scans are not silently accepted as successful conversion.
    if not any(page['text'] for page in pages):
        raise ValueError('no extractable text; choose the MinerU OCR backend')
    (output / 'markdown.md').write_text('\n\n'.join(page['text'] for page in pages) + '\n', encoding='utf-8')
    (output / 'pages.json').write_text(json.dumps(pages, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
