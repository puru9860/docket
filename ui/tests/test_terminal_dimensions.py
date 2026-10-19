import importlib.util
import os
import pty
import subprocess
import termios
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('terminal_dimensions', Path(__file__).parents[1]/'terminal_dimensions.py')
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


@unittest.skipUnless(Path('/proc').is_dir(), 'Linux PTY telemetry')
class TerminalDimensionsTests(unittest.TestCase):
    def test_actual_pty_size_is_read_without_input_resize_or_stale_pid_attribution(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master); self.addCleanup(os.close, slave)
        termios.tcsetwinsize(master, (22,147))
        child = subprocess.Popen(['python3','-B','-c','import time; time.sleep(30)'], stdin=slave, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (child.terminate(), child.wait(timeout=5)))
        stat = (Path('/proc')/str(child.pid)/'stat').read_text()
        start = stat[stat.rfind(')')+2:].split()[19]
        self.assertEqual(reader.dimensions(child.pid,start), {'cols':147,'rows':22})
        self.assertEqual(termios.tcgetwinsize(master), (22,147))
        self.assertIsNone(reader.dimensions(child.pid,'stale-start'))
        self.assertIsNone(reader.dimensions('../not-a-pid',start))
