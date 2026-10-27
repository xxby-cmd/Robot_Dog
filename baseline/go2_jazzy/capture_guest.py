#!/usr/bin/env python3
"""Save an unmodified screenshot of the guest X11 desktop for verification."""
import os
import sys
os.environ.setdefault('DISPLAY', ':0')
import gi
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk
window = Gdk.get_default_root_window()
if window is None:
    raise RuntimeError('No graphical guest session')
pixels = Gdk.pixbuf_get_from_window(window, 0, 0, window.get_width(), window.get_height())
pixels.savev(sys.argv[1], 'png', [], [])
print(sys.argv[1])
