"""Read Linux PTY dimensions without resizing or reading terminal input."""
import json
import os
import re
import sys
import termios
from pathlib import Path


def dimensions(pid, start):
    if not str(pid).isdigit() or not start:
        return None
    try:
        process = Path('/proc') / str(pid)
        stat = (process / 'stat').read_text()
        if stat[stat.rfind(')') + 2:].split()[19] != str(start):
            return None
        fd_path = process / 'fd/0'
        if not re.fullmatch(r'/dev/pts/\d+', os.readlink(fd_path)):
            return None
        fd = os.open(fd_path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            rows, cols = termios.tcgetwinsize(fd)
        finally:
            os.close(fd)
        if 20 <= cols <= 500 and 5 <= rows <= 200:
            return {'cols': cols, 'rows': rows}
    except (OSError, ValueError, IndexError):
        pass
    return None


if __name__ == '__main__':
    print(json.dumps(dimensions(*sys.argv[1:3])))
