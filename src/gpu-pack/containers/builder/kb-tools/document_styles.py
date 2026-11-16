#!/usr/bin/env python3
"""Launch the isolated converter's document-style command in a separate process.

DEEP32_STYLES_COMMAND may contain a JSON array naming a converter-side command.
For a local source checkout, DEEP32_CONVERTER_PYTHON selects its interpreter.
The converter process needs the supplied original document paths and PyMuPDF.
"""
import json
import os
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    configured = os.environ.get('DEEP32_STYLES_COMMAND')
    if configured:
        command = json.loads(configured)
        if not isinstance(command, list) or not command or not all(isinstance(v, str) and v for v in command):
            raise ValueError('DEEP32_STYLES_COMMAND must be a non-empty JSON string array')
    else:
        helper = Path(__file__).resolve().parents[2] / 'converter-agpl/document-styles/document_styles.py'
        command = [os.environ.get('DEEP32_CONVERTER_PYTHON', sys.executable), '-I', '-B', str(helper)]
    return subprocess.run(command + list(arguments), check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
