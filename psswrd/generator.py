"""Password generator."""

from __future__ import annotations

import secrets
import string

SYMBOLS = "!@#$%^&*()-_=+[]{};:,.<>?/~"


def generate_password(length: int = 20, upper: bool = True,
                      lower: bool = True, digits: bool = True,
                      symbols: bool = True) -> str:
    pools: list[str] = []
    if lower:
        pools.append(string.ascii_lowercase)
    if upper:
        pools.append(string.ascii_uppercase)
    if digits:
        pools.append(string.digits)
    if symbols:
        pools.append(SYMBOLS)
    if not pools:
        raise ValueError("Enable at least one character class.")
    length = max(length, len(pools))  # guarantee one of each
    alphabet = "".join(pools)
    # ensure at least one char from each pool
    chars = [secrets.choice(p) for p in pools]
    chars += [secrets.choice(alphabet) for _ in range(length - len(chars))]
    # shuffle without `random` module (CSPRNG-friendly Fisher-Yates)
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)
