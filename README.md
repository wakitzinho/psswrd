# WIP absolutely not finished

# psswrd — local-first TUI password manager

Type `psswrd`, enter your master password, manage passwords in a btop-style TUI.
Built on `prompt_toolkit`, so it paints no backgrounds — terminal
transparency (e.g. Alacritty `background_opacity`) shows straight through.
Only the selected row and the focused button carry a background.

- Vault: single encrypted file `~/.config/psswrd/vault.enc`
  (Argon2id → AES-256-GCM, `0600` perms). Override with `PSSWRD_VAULT=/path` or `--vault`.
- Sidebar: searchable, scrollable list (name + email + `••••`).
  Click/Enter an item → detail on the right.
- Detail: click **Reveal + Copy** to show the password *and* copy it
  (auto-hides after 20s). Edit / Delete / Copy email below.
- New entry: fields name, email/username, password, url, notes.
  **Generate…** opens a generator modal (length 8–128 + upper/lower/digits/symbols).
- `i` / Import button: import unencrypted Bitwarden JSON export.
- `l`: lock screen (re-enter master password). `q`: quit.

## Install

```bash
cd ~/Projects/passwrd
pipx install -e .
psswrd                    # first run creates the vault
```

Needs Python ≥ 3.10. Deps: `prompt-toolkit`, `cryptography`, `argon2-cffi`, `pyperclip`
(clipboard also falls back to `wl-copy` / `xclip` / `xsel` / `pbcopy`).

 mouse: click list rows, password/mail lines, and all buttons.

## Keys

| key | action |
|-----|--------|
| `/` | focus filter |
| `j` / `k` | move up/down |
| `n` | new entry |
| `e` | edit selected |
| `r` | reveal + copy password |
| `y` | copy password |
| `c` | copy email |
| `d` | delete selected |
| `i` | import Bitwarden JSON |
| `x` | export passwords as JSON |
| `l` | lock |

