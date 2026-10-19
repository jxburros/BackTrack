"""BackTrack training code: front end, models, GGUF export, parity, data and evaluation (README.md)."""

import os
import subprocess

# Run from the BackTrack desktop hub (no console of its own) on Windows, every program the
# training starts would open a console window of its own; BACKTRACK_HIDE_CONSOLES keeps them hidden.
if os.name == "nt" and os.environ.get("BACKTRACK_HIDE_CONSOLES"):
    _popen_init = subprocess.Popen.__init__

    def _hidden_popen_init(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x08000000  # CREATE_NO_WINDOW
        _popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _hidden_popen_init
