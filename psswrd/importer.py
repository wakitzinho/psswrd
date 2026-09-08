"""Import from Bitwarden JSON exports (unencrypted)."""

from __future__ import annotations

import json
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
