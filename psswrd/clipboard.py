"""Clipboard helper with graceful fallbacks."""

from __future__ import annotations

import shutil
import subprocess


def copy_text(text: str) -> bool:
    # 1) pyperclip if installed
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return True
    except Exception:
        pass
    # 2) native tools
    for cmd in (
        (["wl-copy"], text),
        (["xclip", "-selection", "clipboard"], text),
        (["xsel", "--clipboard", "--input"], text),
        (["pbcopy"], text),
    ):
        prog = cmd[0][0]
        if shutil.which(prog):
            try:
                subprocess.run(cmd[0], input=text.encode(),
                               check=True, timeout=5)
                return True
            except Exception:
                continue
    return False
