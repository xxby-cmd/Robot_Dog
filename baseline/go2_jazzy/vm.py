"""Compatibility entry for the shared per-machine desktop implementation."""
from pathlib import Path
import runpy
import sys

if __name__ == '__main__':
    target = Path(__file__).resolve().parents[1] / 'desktop_install' / 'vm.py'
    sys.path.insert(0, str(target.parent))
    runpy.run_path(str(target), run_name='__main__')
