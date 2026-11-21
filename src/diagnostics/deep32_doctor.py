"""Isolated script entry point for the source/installed diagnostics directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep32_diag.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
