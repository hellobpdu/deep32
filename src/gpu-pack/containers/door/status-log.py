"""Docker C6 channel: repeat the current short-lived status, without retaining stale fields."""
from pathlib import Path
import time

while True:
    path = Path('/status/status')
    if path.is_file():
        line = path.read_text(encoding='ascii')
        if line.startswith('D32S/1 ') and len(line.encode('ascii')) <= 512:
            print(line, end='', flush=True)
    time.sleep(10)
