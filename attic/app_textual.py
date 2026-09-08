"""Textual TUI (btop style): sidebar vault list + detail pane + modals."""

from __future__ import annotations

import asyncio
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    Switch,
    TextArea,
)

from .clipboard import copy_text
from .generator import generate_password
from .importer import parse_bitwarden_file
from .vault import Vault


def _fmt_ts(ts: int) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


def _clock() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _matches(entry: dict, query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    hay = f"{entry.get('name','')} {entry.get('email','')} {entry.get('url','')}".lower()
    return q in hay


def _strength(password: str) -> tuple[str, int, str]:
    """Return (label, percent 0-100, color) for the meter bar."""
    if not password:
        return "EMPTY", 0, "#5b626e"
    classes = sum((
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ))
    score = min(100, len(password) * 4 + (classes - 1) * 15)
    if score < 35:
        return "WEAK", score, "#f85149"
    if score < 65:
        return "FAIR", score, "#d29922"
    if score < 85:
        return "GOOD", score, "#a3be8c"
    return "STRONG", score, "#3fb950"


def _meter_bar(percent: int, width: int = 22) -> str:
    filled = round(width * percent / 100)
    return "█" * filled + "░" * (width - filled)


class EntryItem(ListItem):
    """One dense btop-process-list style row."""

    def __init__(self, entry: dict) -> None:
        super().__init__()
        self.entry = entry

    def compose(self) -> ComposeResult:
        name = (self.entry.get("name") or "(no name)")
        email = self.entry.get("email") or ""
        # fixed-width columns, truncated — like a process table
        row = f"● {name[:20]:<20} │ {email[:26]:<26} │ ••••••••"
        yield Label(f"[bold]{escape(row)}[/bold]", markup=True)


# ---------- modals (btop boxes: black bg, orange frame) ----------

class GeneratorScreen(ModalScreen):
    """Password generator: length + char classes. Dismisses with str|None."""

    CSS = """
    GeneratorScreen { align: center middle; background: rgba(0, 0, 0, 0.7); }
    #gen-box {
        width: 54; height: auto;
        border: round #ee7600; background: #0a0c10; padding: 1 2;
    }
    #gen-title { color: #ee7600; text-style: bold; }
    #gen-preview { text-align: center; margin: 1 0; color: #3fb950; }
    """

    def __init__(self, length: int = 20) -> None:
        super().__init__()
        self._length = max(8, min(128, length or 20))
        self._preview = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="gen-box"):
            yield Label("┤ GEN ├  generate password", id="gen-title")
            yield Label("length [8-128]:", classes="dim")
            yield Input(value=str(self._length), id="gen-length")
            yield Label("UPPER [A-Z]")
            yield Switch(value=True, id="gen-upper")
            yield Label("lower [a-z]")
            yield Switch(value=True, id="gen-lower")
            yield Label("digits [0-9]")
            yield Switch(value=True, id="gen-digits")
            yield Label("symbols [!@#..]")
            yield Switch(value=True, id="gen-symbols")
            yield Static("", id="gen-preview")
            with Horizontal(classes="btnrow"):
                yield Button("\\[r]egen", id="gen-regen")
                yield Button("\\[⏎]use", id="gen-use")
                yield Button("\\[esc]", id="gen-cancel")

    def on_mount(self) -> None:
        self._regen()

    def _read_opts(self) -> tuple[int, bool, bool, bool, bool]:
        try:
            n = int(self.query_one("#gen-length", Input).value or "20")
        except ValueError:
            n = 20
        n = max(8, min(128, n))
        g = lambda sid: self.query_one(sid, Switch).value  # noqa: E731
        return n, g("#gen-upper"), g("#gen-lower"), g("#gen-digits"), g("#gen-symbols")

    def _regen(self) -> None:
        n, up, lo, dg, sy = self._read_opts()
        try:
            self._preview = generate_password(n, upper=up, lower=lo, digits=dg, symbols=sy)
        except ValueError as e:
            self._preview = f"({e})"
        try:
            self.query_one("#gen-preview", Static).update(f"[bold]{escape(self._preview)}[/bold]")
        except Exception:
            pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "gen-regen":
            self._regen()
        elif bid == "gen-use":
            self.dismiss(self._preview if self._preview and not self._preview.startswith("(") else None)
        elif bid == "gen-cancel":
            self.dismiss(None)

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "gen-length":
            self._regen()


class EntryFormScreen(ModalScreen):
    """New / edit entry. Dismisses with dict|None."""

    CSS = """
    EntryFormScreen { align: center middle; background: rgba(0, 0, 0, 0.7); }
    #form-box {
        width: 64; height: auto;
        border: round #ee7600; background: #0a0c10; padding: 1 2;
    }
    #form-box Input, #form-box TextArea { margin-bottom: 1; }
    #form-title { color: #ee7600; text-style: bold; }
    """

    def __init__(self, entry: dict | None = None) -> None:
        super().__init__()
        self._entry = entry or {}

    def compose(self) -> ComposeResult:
        title = "EDIT" if self._entry.get("id") else "NEW"
        with Vertical(id="form-box"):
            yield Label(f"┤ {title} ├  entry", id="form-title")
            yield Label("name:", classes="dim")
            yield Input(value=self._entry.get("name", ""), id="f-name", placeholder="e.g. GitHub")
            yield Label("email / user:", classes="dim")
            yield Input(value=self._entry.get("email", ""), id="f-email", placeholder="you@example.com")
            yield Label("password:", classes="dim")
            yield Input(
                value=self._entry.get("password", ""),
                id="f-password",
                password=True,
                placeholder="••••••••",
            )
            with Horizontal(classes="btnrow"):
                yield Button("\\[g]enerate…", id="f-generate")
                yield Button("\\[s]how", id="f-show")
            yield Label("url:", classes="dim")
            yield Input(value=self._entry.get("url", ""), id="f-url", placeholder="https://…")
            yield Label("notes:", classes="dim")
            yield TextArea(text=self._entry.get("notes", ""), id="f-notes")
            with Horizontal(classes="btnrow"):
                yield Button("\\[⏎]save", id="f-save")
                yield Button("\\[esc]", id="f-cancel")

    def _collect(self) -> dict:
        g = lambda sid: self.query_one(sid, Input).value.strip()  # noqa: E731
        return {
            "name": g("#f-name"),
            "email": g("#f-email"),
            "password": self.query_one("#f-password", Input).value,
            "url": g("#f-url"),
            "notes": self.query_one("#f-notes", TextArea).text,
        }

    def _gen_done(self, password: str | None) -> None:
        if password:
            self.query_one("#f-password", Input).value = password
            self.app.notify("Generated password inserted.")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "f-cancel":
            self.dismiss(None)
        elif bid == "f-save":
            data = self._collect()
            if not data["name"]:
                self.app.notify("Name is required.", severity="error")
                return
            self.dismiss(data)
        elif bid == "f-show":
            inp = self.query_one("#f-password", Input)
            inp.password = not inp.password
            event.button.label = "\\[h]ide" if not inp.password else "\\[s]how"
        elif bid == "f-generate":
            self.app.push_screen(GeneratorScreen(), callback=self._gen_done)


class ConfirmScreen(ModalScreen):
    CSS = """
    ConfirmScreen { align: center middle; background: rgba(0, 0, 0, 0.7); }
    #confirm-box {
        width: 48; height: auto;
        border: round #f85149; background: #0a0c10; padding: 1 2;
    }
    #confirm-title { color: #f85149; text-style: bold; }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label("┤ DEL ├  confirm", id="confirm-title")
            yield Label(escape(self._message))
            with Horizontal(classes="btnrow"):
                yield Button("\\[⏎]delete", id="c-yes")
                yield Button("\\[esc]", id="c-no")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "c-yes")


class ImportScreen(ModalScreen):
    """Ask for Bitwarden JSON path, import into vault. Dismisses with int|None."""

    CSS = """
    ImportScreen { align: center middle; background: rgba(0, 0, 0, 0.7); }
    #import-box {
        width: 62; height: auto;
        border: round #ee7600; background: #0a0c10; padding: 1 2;
    }
    #import-title { color: #ee7600; text-style: bold; }
    """

    def __init__(self, vault: Vault) -> None:
        super().__init__()
        self._vault = vault

    def compose(self) -> ComposeResult:
        with Vertical(id="import-box"):
            yield Label("┤ IMPORT ├  bitwarden json", id="import-title")
            yield Label("path to unencrypted export:", classes="dim")
            yield Input(id="i-path", placeholder="~/bitwarden_export.json")
            with Horizontal(classes="btnrow"):
                yield Button("\\[⏎]import", id="i-go")
                yield Button("\\[esc]", id="i-cancel")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "i-cancel":
            self.dismiss(None)
            return
        path = self.query_one("#i-path", Input).value.strip()
        if not path:
            self.app.notify("Enter a file path.", severity="error")
            return
        try:
            items = parse_bitwarden_file(path)
        except Exception as e:
            self.app.notify(f"Import failed: {e}", severity="error")
            return
        if not items:
            self.app.notify("No items found in that file.", severity="warning")
            self.dismiss(0)
            return
        for e in items:
            self._vault.add(e)
        self.dismiss(len(items))


class LockScreen(ModalScreen):
    CSS = """
    LockScreen { align: center middle; background: rgba(0, 0, 0, 0.7); }
    #lock-box {
        width: 46; height: auto;
        border: round #d29922; background: #0a0c10; padding: 1 2;
    }
    #lock-title { color: #d29922; text-style: bold; }
    """

    def __init__(self, vault: Vault) -> None:
        super().__init__()
        self._vault = vault

    def compose(self) -> ComposeResult:
        with Vertical(id="lock-box"):
            yield Label("┤ LOCKED ├  master password", id="lock-title")
            yield Input(id="l-password", password=True, placeholder="master password…")
            with Horizontal(classes="btnrow"):
                yield Button("\\[⏎]unlock", id="l-go")
                yield Button("\\[q]uit", id="l-quit")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "l-quit":
            self.app.exit()
            return
        pw = self.query_one("#l-password", Input).value
        if self._vault.verify_password(pw):
            self.dismiss(True)
        else:
            self.app.notify("Wrong password.", severity="error")


# ---------- main app (btop layout) ----------

class PsswrdApp(App):
    CSS_PATH = "app.css"
    TITLE = "psswrd"

    BINDINGS = [
        ("q", "quit", "quit"),
        ("n", "new_entry", "new"),
        ("e", "edit_entry", "edit"),
        ("d", "delete_entry", "del"),
        ("r", "reveal", "reveal"),
        ("c", "copy_email", "copy mail"),
        ("y", "copy_password", "copy pass"),
        ("/", "focus_search", "search"),
        ("i", "import_vault", "import"),
        ("l", "lock", "lock"),
        ("j", "cursor_down", "down"),
        ("k", "cursor_up", "up"),
    ]

    def __init__(self, vault: Vault) -> None:
        super().__init__()
        self.vault = vault
        self._selected_id: str | None = None
        self._revealed = False
        self._hide_timer = None
        self._status_msg = "ready"
        self._clock_timer = None

    def compose(self) -> ComposeResult:
        yield Static("", id="topbar")
        with Horizontal(id="main"):
            with Vertical(id="sidebar"):
                yield Input(placeholder="filter  ( / )", id="search")
                yield ListView(id="entries")
            with Vertical(id="detail"):
                yield Static("", id="detail-body")
                yield Static("", id="meter")
                with Horizontal(id="detail-actions"):
                    yield Button("\\[r]eveal", id="btn-pass")
                    yield Button("\\[c]mail", id="btn-copy-email")
                    yield Button("\\[e]dit", id="btn-edit")
                    yield Button("\\[d]el", id="btn-delete")
        yield Static("", id="status")
        yield Static("", id="menubar")

    def on_mount(self) -> None:
        try:
            self.query_one("#sidebar").border_title = "┤ VAULT ├"
            self.query_one("#detail").border_title = "┤ DETAILS ├"
        except Exception:
            pass
        self.refresh_list()
        self.update_detail()
        self._log("ready — / to filter, n for new")
        self._tick_clock()
        try:
            self.query_one("#search", Input).focus()
        except Exception:
            pass

    def _tick_clock(self) -> None:
        try:
            self._update_topbar()
            self._update_menubar()
        except Exception:
            pass
        self._clock_timer = self.set_timer(1.0, self._tick_clock)

    # ----- chrome -----

    def _update_topbar(self) -> None:
        try:
            bar = self.query_one("#topbar", Static)
        except Exception:
            return
        total = len(self.vault.entries)
        shown = len(self.filtered_entries())
        lock = "● UNLOCKED" if not self._revealed else "◉ REVEALED"
        bar.update(
            f"▚[bold #ee7600]psswrd[/]▞"
            f"  [dim]entries:[/dim][bold]{shown}/{total}[/bold]"
            f"  [dim]vault:[/dim]{escape(self.vault.path.name)}"
            f"  [dim]│[/dim]  {lock}"
            f"  [dim]│[/dim]  {_clock()}"
        )

    def _update_menubar(self) -> None:
        try:
            bar = self.query_one("#menubar", Static)
        except Exception:
            return
        bar.update(
            "[bold #ee7600]\\[n][/][dim]new[/dim]  "
            "[bold #ee7600]\\[e][/][dim]edit[/dim]  "
            "[bold #ee7600]\\[r][/][dim]reveal[/dim]  "
            "[bold #ee7600]\\[y][/][dim]copy[/dim]  "
            "[bold #ee7600]\\[d][/][dim]del[/dim]  "
            "[bold #ee7600]\\[j/k][/][dim]move[/dim]  "
            "[bold #ee7600]\\[/][/][dim]filter[/dim]  "
            "[bold #ee7600]\\[i][/][dim]import[/dim]  "
            "[bold #ee7600]\\[l][/][dim]lock[/dim]  "
            "[bold #ee7600]\\[q][/][dim]quit[/dim]"
        )

    def _log(self, msg: str) -> None:
        self._status_msg = msg
        try:
            self.query_one("#status", Static).update(f"[bold #3fb950]▸[/] {escape(msg)}")
        except Exception:
            pass
        self.notify(msg)

    # ----- list -----

    def filtered_entries(self) -> list[dict]:
        try:
            q = self.query_one("#search", Input).value
        except Exception:
            q = ""
        return sorted(
            [e for e in self.vault.entries if _matches(e, q)],
            key=lambda e: e.get("name", "").lower(),
        )

    def refresh_list(self, keep_selection: bool = True) -> None:
        try:
            lv = self.query_one("#entries", ListView)
        except Exception:
            return
        entries = self.filtered_entries()
        if keep_selection and self._selected_id:
            ids = {e["id"] for e in entries}
            if self._selected_id not in ids:
                self._selected_id = entries[0]["id"] if entries else None
        elif not keep_selection:
            self._selected_id = entries[0]["id"] if entries else None
        if self._selected_id is None and entries:
            self._selected_id = entries[0]["id"]
        lv.clear()
        for e in entries:
            item = EntryItem(e)
            lv.append(item)
            if e["id"] == self._selected_id:
                lv.index = len(lv.children) - 1
        self._revealed = False
        self._update_topbar()

    # ----- detail -----

    def _current(self) -> dict | None:
        for e in self.vault.entries:
            if e["id"] == self._selected_id:
                return e
        return None

    def update_detail(self) -> None:
        try:
            body = self.query_one("#detail-body", Static)
            meter = self.query_one("#meter", Static)
        except Exception:
            return
        e = self._current()
        if not e:
            body.update("[dim]no entries — [n]ew / [i]mport[/dim]")
            meter.update("")
            self._update_topbar()
            return
        pw = e.get("password", "")
        if self._revealed and pw:
            shown = f"[bold #3fb950]{escape(pw)}[/]"
        elif pw:
            shown = "[dim]••••••••  (r to reveal+copy)[/dim]"
        else:
            shown = "[dim](empty)[/dim]"
        label, pct, color = _strength(pw)
        meter.update(
            f"[dim]strength │[/dim] [{color}]{_meter_bar(pct)}[/{color}]"
            f" [bold {color}]{pct:>3}% {label}[/]"
            f" [dim]│ len {len(pw)}[/dim]"
        )
        body.update(
            f"[bold #ee7600]┤ {escape(e.get('name','(no name)'))} ├[/]\n"
            f"[dim]mail │[/dim] {escape(e.get('email','—') or '—')}\n"
            f"[dim]pass │[/dim] {shown}\n"
            f"[dim]url  │[/dim] {escape(e.get('url','—') or '—')}\n"
            f"[dim]note │[/dim]\n{escape(e.get('notes','')) or '[dim]—[/dim]'}\n"
            f"[dim]upd {_fmt_ts(e.get('modified',0))} · new {_fmt_ts(e.get('created',0))}[/dim]"
        )
        try:
            self.query_one("#btn-pass", Button).label = (
                "\\[h]ide" if self._revealed else "\\[r]eveal"
            )
        except Exception:
            pass
        self._update_topbar()

    def _reveal_and_copy(self) -> None:
        e = self._current()
        if not e or not e.get("password"):
            self._log("nothing to copy")
            return
        self._revealed = True
        ok = copy_text(e["password"])
        self.update_detail()
        self._log("pass copied to clipboard" if ok else "revealed (no clipboard)")
        # auto-hide after 20s
        try:
            if self._hide_timer:
                self._hide_timer.stop()
        except Exception:
            pass
        try:
            loop = asyncio.get_event_loop()
            self._hide_timer = loop.call_later(20, lambda: self.call_from_thread(self._auto_hide))
        except Exception:
            pass

    def _auto_hide(self) -> None:
        self._revealed = False
        self.update_detail()

    # ----- events -----

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.refresh_list()
            self.update_detail()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, EntryItem):
            self._selected_id = item.entry["id"]
            self._revealed = False
            self.update_detail()

    async def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, EntryItem):
            self._selected_id = item.entry["id"]
            self._revealed = False
            self.update_detail()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-pass":
            if self._revealed:
                self._revealed = False
                self.update_detail()
            else:
                self._reveal_and_copy()
        elif bid == "btn-copy-email":
            await self.action_copy_email()
        elif bid == "btn-edit":
            await self.action_edit_entry()
        elif bid == "btn-delete":
            await self.action_delete_entry()

    # ----- actions -----

    async def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    async def action_cursor_down(self) -> None:
        try:
            lv = self.query_one("#entries", ListView)
            lv.action_cursor_down()
        except Exception:
            pass

    async def action_cursor_up(self) -> None:
        try:
            lv = self.query_one("#entries", ListView)
            lv.action_cursor_up()
        except Exception:
            pass

    async def action_reveal(self) -> None:
        if self._revealed:
            self._revealed = False
            self.update_detail()
        else:
            self._reveal_and_copy()

    async def action_copy_email(self) -> None:
        e = self._current()
        if e and e.get("email"):
            ok = copy_text(e["email"])
            self._log("mail copied" if ok else "no clipboard")
        else:
            self._log("nothing to copy")

    async def action_copy_password(self) -> None:
        e = self._current()
        if e and e.get("password"):
            ok = copy_text(e["password"])
            self._log("pass copied to clipboard" if ok else "no clipboard")
        else:
            self._log("nothing to copy")

    async def action_lock(self) -> None:
        self._revealed = False
        self.update_detail()
        self._log("locked")
        await self.push_screen(LockScreen(self.vault))

    async def action_new_entry(self) -> None:
        def done(data: dict | None) -> None:
            if not data:
                return
            from .vault import new_entry
            entry = new_entry(**{k: data.get(k, "") for k in ("name", "email", "password", "url", "notes")})
            self.vault.add(entry)
            self._selected_id = entry["id"]
            try:
                self.query_one("#search", Input).value = ""
            except Exception:
                pass
            self.refresh_list()
            self.update_detail()
            self._log(f"saved [{entry['name']}]")
        self.push_screen(EntryFormScreen(), callback=done)

    async def action_edit_entry(self) -> None:
        e = self._current()
        if not e:
            self._log("nothing selected")
            return

        def done(data: dict | None) -> None:
            if not data:
                return
            self.vault.update(e["id"], data)
            self._revealed = False
            self.refresh_list()
            self.update_detail()
            self._log("entry updated")
        self.push_screen(EntryFormScreen(dict(e)), callback=done)

    async def action_delete_entry(self) -> None:
        e = self._current()
        if not e:
            return

        def done(yes: bool | None) -> None:
            if yes:
                self.vault.delete(e["id"])
                self._selected_id = None
                self._revealed = False
                self.refresh_list(keep_selection=False)
                self.update_detail()
                self._log("entry deleted")
        self.push_screen(ConfirmScreen(f"delete [{e.get('name','?')}]?"), callback=done)

    async def action_import_vault(self) -> None:
        def done(count: int | None) -> None:
            if count is None:
                return
            self.refresh_list(keep_selection=False)
            self.update_detail()
            self._log(f"imported {count} entries" if count else "nothing imported")
        self.push_screen(ImportScreen(self.vault), callback=done)
