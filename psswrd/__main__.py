"""CLI entry: `psswrd` -> master password prompt -> TUI."""

from __future__ import annotations

import argparse
from pathlib import Path

from .app import PsswrdApp
from .vault import Vault, default_vault_path, prompt_new_vault, prompt_unlock


def main() -> None:
    ap = argparse.ArgumentParser(prog="psswrd", description="Local-first TUI password manager.")
    ap.add_argument("--vault", default=None, help="Path to vault file (default ~/.config/psswrd/vault.enc)")
    args = ap.parse_args()

    path = Path(args.vault).expanduser() if args.vault else default_vault_path()

    if not path.exists():
        master = prompt_new_vault(path)
        vault = Vault.create(path, master)
        print(f"Vault created at {path}")
    else:
        vault = prompt_unlock(path)

    PsswrdApp(vault).run()


if __name__ == "__main__":
    main()
