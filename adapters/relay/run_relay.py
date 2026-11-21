"""Trusted absolute-path entry for the existing connector's service definition."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep32_relay.service import main

if __name__ == "__main__":
    raise SystemExit(main())
