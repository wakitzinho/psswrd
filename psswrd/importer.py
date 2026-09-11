"""Import from and export to Bitwarden JSON (unencrypted)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .vault import new_entry


def parse_bitwarden_file(path: str | Path) -> list[dict]:
    p = Path(path).expanduser()
    data = json.loads(p.read_text())
    # Bitwarden export is {"encrypted": false, "items": [...]}
    # also accept a bare list.
    items = data.get("items", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("Unrecognized Bitwarden export format.")
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        login = it.get("login") or {}
        uris = login.get("uris") or []
        url = ""
        if uris and isinstance(uris[0], dict):
            url = uris[0].get("uri", "") or ""
        out.append(new_entry(
            name=it.get("name", "") or "",
            email=login.get("username", "") or "",
            password=login.get("password", "") or "",
            url=url,
            notes=it.get("notes", "") or "",
        ))
    return out


def export_bitwarden(entries: list[dict], path: str | Path) -> None:
    """Export vault entries as an unencrypted Bitwarden JSON file.

    Writes atomically (temp file + rename) so the plaintext never exists
    at the destination with looser permissions, and refuses to overwrite an
    existing file silently.
    """
    p = Path(path).expanduser()
    if p.exists():
        raise FileExistsError(f"{p} already exists — refusing to overwrite")
    items = []
    for e in entries:
        item = {
            "name": e.get("name", ""),
            "login": {
                "username": e.get("email", ""),
                "password": e.get("password", ""),
                "uris": [{"uri": e.get("url", "")}] if e.get("url") else [],
            },
            "notes": e.get("notes", ""),
        }
        items.append(item)
    blob = {"encrypted": False, "items": items}
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(blob, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
