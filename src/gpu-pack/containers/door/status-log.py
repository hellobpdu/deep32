"""Docker stage log: omit protected C6 pairing and sign-in fields."""
from pathlib import Path
import time


def log_line(line):
    if (not line.isascii() or not line.startswith('D32S/1 ') or len(line) > 512
            or not line.endswith('\n') or line.count('\n') != 1
            or any(ord(c) < 32 for c in line[:-1])):
        return None
    prefix, separator, message = line[:-1].partition(' msg=')
    fields = [field for field in prefix.split(' ') if not field.startswith(('pair=', 'signin='))]
    return ' '.join(fields) + (separator + message if separator else '') + '\n'


def main():
    while True:
        path = Path('/status/status')
        if path.is_file():
            line = log_line(path.read_text(encoding='ascii'))
            if line is not None:
                print(line, end='', flush=True)
        time.sleep(10)


if __name__ == '__main__':
    main()
