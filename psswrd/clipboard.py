"""Clipboard helper with graceful fallbacks.

passwords persist on the system clipboard across app exits; that is the
point of a clipboard. But a secret must not sit there forever, so the app
calls `clear_clipboard()` shortly after copying.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

# maximum time we wait for a one-shot tool (pbcopy / xclip -loops=1)
_LOCKBOX_TIMEOUT = 10.0


def _tool(prog: str) -> str | None:
    return shutil.which(prog)


def _spawn_stdin_loop(cmd: list[str], payload: bytes) -> bool:
    """Feed payload to a persistance clipboard daemon and return once read.

    On Wayland/X11 the underlying tool (wl-copy / xclip) forks a daemon that
    keeps the selection alive after it exits. We write the payload via a
    private file so the daemon's stdin never holds our pipes open, pass it
    through a scratch file, and detach the daemon into its own session.
    """
    try:
        with tempfile.NamedTemporaryFile(prefix=".psswrd-", delete=False) as tf:
            tf.write(payload)
            tmp_path = tf.name
    except OSError:
        return False
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd + [tmp_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            proc.wait(timeout=_LOCKBOX_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        return proc.returncode in (0, None)
    except OSError:
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def copy_text(text: str) -> bool:
    # 1) pyperclip if installed
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return True
    except Exception:
        pass
    # 2) native tools
    for tool in ("wl-copy", "xclip", "xsel", "pbcopy"):
        prog = _tool(tool)
        if not prog:
            continue
        try:
            if tool in ("wl-copy", "xclip", "xsel"):
                if _spawn_stdin_loop([prog], text.encode()):
                    return True
            else:  # pbcopy: one-shot, stdin + EOF is enough
                subprocess.run(prog, input=text.encode(),
                               check=True, timeout=_LOCKBOX_TIMEOUT)
                return True
        except Exception:
            continue
    return False


def clear_clipboard() -> bool:
    """Best-effort removal of the current clipboard contents."""
    try:
        import pyperclip  # type: ignore

        pyperclip.copy("")
        return True
    except Exception:
        pass
    for cmd in (
        (["wl-copy", "--clear"], b""),
        (["xclip", "-selection", "clipboard", "/dev/null"], b""),
        (["xsel", "--clipboard", "--clear"], b""),
        (["pbcopy"], b""),
    ):
        prog = _tool(cmd[0][0])
        if not prog:
            continue
        try:
            if cmd[0][0] == "xclip":
                subprocess.run(cmd[0], check=True, timeout=5)
            else:
                subprocess.run(cmd[0], input=cmd[1], check=True, timeout=5)
            return True
        except Exception:
            continue
    return False