"""Encrypted vault: Argon2id -> AES-256-GCM, single local file."""

from __future__ import annotations

import base64
import getpass
import json
import os
import secrets
import time
import uuid
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Argon2id params (OWASP-ish, reasonable for desktop)
_TIME_COST = 3
_MEMORY_COST = 64 * 1024  # 64 MiB
_PARALLELISM = 4
_KEY_LEN = 32
_SALT_LEN = 16
_NONCE_LEN = 12


def default_vault_path() -> Path:
    env = os.environ.get("PSSWRD_VAULT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "psswrd" / "vault.enc"


def derive_key(master_password: str, salt: bytes) -> bytes:
    return hash_secret_raw(
        master_password.encode("utf-8"),
        salt,
        time_cost=_TIME_COST,
        memory_cost=_MEMORY_COST,
        parallelism=_PARALLELISM,
        hash_len=_KEY_LEN,
        type=Type.ID,
    )


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def new_entry(name: str = "", email: str = "", password: str = "",
              url: str = "", notes: str = "") -> dict:
    now = int(time.time())
    return {
        "id": uuid.uuid4().hex,
        "name": name,
        "email": email,
        "password": password,
        "url": url,
        "notes": notes,
        "created": now,
        "modified": now,
    }


class VaultError(Exception):
    pass


class WrongPasswordError(VaultError):
    pass


class Vault:
    """Holds decrypted entries + key material needed to save/lock."""

    def __init__(self, path: Path, salt: bytes, key: bytes, entries: list[dict]):
        self.path = path
        self.salt = salt
        self.key = key
        self.entries: list[dict] = entries

    # ---- load / create / save ----

    @classmethod
    def exists(cls, path: Path | None = None) -> bool:
        p = path or default_vault_path()
        return p.exists()

    @classmethod
    def create(cls, path: Path | None, master_password: str) -> "Vault":
        p = path or default_vault_path()
        salt = secrets.token_bytes(_SALT_LEN)
        key = derive_key(master_password, salt)
        v = cls(p, salt, key, [])
        v.save()
        return v

    @classmethod
    def load(cls, path: Path | None, master_password: str) -> "Vault":
        p = path or default_vault_path()
        try:
            raw = json.loads(p.read_text())
            salt = _b64d(raw["salt"])
            nonce = _b64d(raw["nonce"])
            ct = _b64d(raw["ciphertext"])
        except (KeyError, ValueError, OSError) as e:
            raise VaultError(f"Vault file is corrupt or unreadable: {e}")

        key = derive_key(master_password, salt)
        try:
            pt = AESGCM(key).decrypt(nonce, ct, None)
        except InvalidTag:
            raise WrongPasswordError("Wrong master password.")
        try:
            data = json.loads(pt.decode("utf-8"))
            entries = data.get("entries", [])
        except ValueError as e:
            raise VaultError(f"Could not decode vault payload: {e}")
        return cls(p, salt, key, entries)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        nonce = secrets.token_bytes(_NONCE_LEN)
        payload = json.dumps({"entries": self.entries}).encode("utf-8")
        ct = AESGCM(self.key).encrypt(nonce, payload, None)
        blob = {
            "v": 1,
            "kdf": "argon2id",
            "salt": _b64e(self.salt),
            "nonce": _b64e(nonce),
            "ciphertext": _b64e(ct),
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(blob))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def verify_password(self, master_password: str) -> bool:
        """For in-app lock screen: check without re-reading file."""
        try:
            other = derive_key(master_password, self.salt)
        except Exception:
            return False
        return secrets.compare_digest(other, self.key)

    # ---- entry CRUD ----

    def add(self, entry: dict) -> dict:
        entry.setdefault("id", uuid.uuid4().hex)
        now = int(time.time())
        entry.setdefault("created", now)
        entry["modified"] = now
        self.entries.append(entry)
        self.save()
        return entry

    def update(self, entry_id: str, fields: dict) -> dict | None:
        for e in self.entries:
            if e["id"] == entry_id:
                e.update(fields)
                e["modified"] = int(time.time())
                self.save()
                return e
        return None

    def delete(self, entry_id: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e["id"] != entry_id]
        if len(self.entries) != before:
            self.save()
            return True
        return False

    def change_master_password(self, new_password: str) -> None:
        self.salt = secrets.token_bytes(_SALT_LEN)
        self.key = derive_key(new_password, self.salt)
        self.save()


def prompt_new_vault(path: Path) -> str:
    print(f"No vault found at {path}")
    print("Let's create one. Your master password encrypts everything.")
    print("(There is no recovery — forget it and the vault is lost.)")
    while True:
        pw1 = getpass.getpass("New master password (min 8 chars): ")
        if len(pw1) < 8:
            print("Too short, use at least 8 characters.")
            continue
        pw2 = getpass.getpass("Confirm master password: ")
        if pw1 != pw2:
            print("Passwords don't match, try again.")
            continue
        return pw1


def prompt_unlock(path: Path, tries: int = 3) -> Vault:
    for i in range(tries):
        pw = getpass.getpass("Master password: ")
        try:
            return Vault.load(path, pw)
        except WrongPasswordError:
            left = tries - i - 1
            print(f"Wrong password. ({left} tries left)" if left else "Wrong password.")
        except VaultError as e:
            print(f"Error: {e}")
            raise SystemExit(1)
    print("Too many failed attempts.")
    raise SystemExit(1)
