"""psswrd TUI on prompt_toolkit — btop-styled and terminal-transparent.

Design rule: no widget paints a background except two explicit accents
(selected list row, focused button). Everything else emits no background
SGR at all, so terminal transparency (e.g. Alacritty background_opacity)
shows straight through.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from prompt_toolkit import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.bindings.focus import focus_next, focus_previous
from prompt_toolkit.layout import (
    D,
    Float,
    FloatContainer,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Button, Frame, Label, TextArea

from .clipboard import copy_text
from .generator import generate_password
from .importer import parse_bitwarden_file, export_bitwarden
from .vault import Vault, new_entry

ACCENT = "#ee7600"

STYLE = Style.from_dict({
    # base text classes (foreground only — never a background)
    "accent": f"bold {ACCENT}",
    "dim": "#5b626e",
    "body": "#c9d1d9",
    "bright": "bold #e6edf3",
    "good": "bold #3fb950",
    "status": "#8b949e",
    "menukey": f"bold {ACCENT}",
    "menudim": "#5b626e",
    "err": "bold #f85149",
    # frame/title/soup chrome: custom classes only (no prompt_toolkit
    # defaults leak in, so nothing here can paint a background)
    "frame.border": "#2a2f38",
    "frame.title": f"bold {ACCENT}",
    "dlgtitle": f"bold {ACCENT}",
    "dlg.border": f"{ACCENT}",
    # the only two intentional backgrounds in the whole UI
    "selected": f"bg:{ACCENT} #000000 bold",
    "button": "bold",
    "button.focused": f"bg:{ACCENT} #000000 bold",
})


def _fmt_ts(ts: int) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


def _strength(password: str) -> tuple[str, int, str]:
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


def _matches(entry: dict, query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    hay = f"{entry.get('name','')} {entry.get('email','')} {entry.get('url','')}".lower()
    return q in hay


class PsswrdApp:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self._selected_id: str | None = None
        self._status = "ready — / to filter, n for new, x to export"
        self._in_filter = False
        self._closers: list = []          # cancel-callbacks for open dialogs
        self._floats: list[Float] = []    # live floats (same order)
        self._focus_elems: list = []      # focus target per open dialog
        self._entry_fields: dict = {}     # current entry-dialog fields
        self._entry_field_order: list = [] # ordered list of entry fields
        self._entry_save = None            # current entry save callback
        self._gen_len = None              # current generator length field
        self.app: Application | None = None
        self._build_widgets()
        self._refresh_selection(keep=False)

    # ---------- widget construction ----------

    def _build_widgets(self) -> None:
        self.topbar_control = FormattedTextControl(self._topbar_tokens)
        self.list_control = FormattedTextControl(self._list_tokens)
        self.detail_control = FormattedTextControl(self._detail_tokens)
        self.status_control = FormattedTextControl(self._status_tokens)
        self.menubar_control = FormattedTextControl(self._menubar_tokens)

        self.list_window = Window(content=self.list_control)

        self.search_field = TextArea(
            height=1, multiline=False, wrap_lines=False,
            focusable=True, focus_on_click=True,
        )
        self.search_field.buffer.on_text_changed += self._on_filter_changed
        self.search_field.accept_handler = lambda _b: self._leave_filter()

        self.btn_mail = Button("[c]mail", handler=self._copy_mail)
        self.btn_edit = Button("[e]dit", handler=self._open_edit)
        self.btn_del = Button("[d]el", handler=self._open_delete)

        left = Frame(
            HSplit([self.search_field, self.list_window]),
            title="┤ VAULT ├",
            width=D(weight=38),
        )
        right = Frame(
            HSplit([
                Window(content=self.detail_control),
                VSplit(
                    [self.btn_mail, self.btn_edit, self.btn_del],
                    padding=3, height=1,
                ),
            ]),
            title="┤ DETAILS ├",
            width=D(weight=62),
        )
        root = HSplit([
            Frame(Window(content=self.topbar_control, height=1)),
            VSplit([left, right], padding=1),
            Window(content=self.status_control, height=1),
            Window(content=self.menubar_control, height=1),
        ])
        self.float_container = FloatContainer(content=root, floats=self._floats)
        self.layout = Layout(container=self.float_container)
        self.kb = KeyBindings()
        self._register_keys()

    # ---------- token sources ----------

    def _topbar_tokens(self):
        total = len(self.vault.entries)
        shown = len(self.filtered_entries())
        clock = datetime.now().strftime("%H:%M:%S")
        return [
            ("", "▚"),
            ("class:accent", "psswrd"),
            ("", f"▞  entries: {shown}/{total}  vault: {self.vault.path.name}  │  "),
            ("class:dim", f"  {clock}"),
        ]

    def _visible_entries(self) -> list[dict]:
        try:
            q = self.search_field.text
        except Exception:
            q = ""
        return sorted(
            [e for e in self.vault.entries if _matches(e, q)],
            key=lambda e: e.get("name", "").lower(),
        )

    def filtered_entries(self) -> list[dict]:
        return self._visible_entries()

    def _list_height(self) -> int:
        try:
            h = self.list_window.render_info.window_height
            return max(3, h) if h else 12
        except Exception:
            return 12

    def _list_tokens(self):
        entries = self._visible_entries()
        h = self._list_height()
        toks: list = []
        if not entries:
            return [("class:dim", "no entries — n for new, i to import")]
        try:
            sel = entries.index(next(e for e in entries if e["id"] == self._selected_id))
        except StopIteration:
            sel = 0
        start = max(0, min(sel - h // 2, len(entries) - h))
        if start > 0:
            toks += [("class:dim", "  ▲ more ▲\n")]
            shown = entries[start:start + h - 1]
        else:
            shown = entries[start:start + h]
        for e in shown:
            row = f"● {e.get('name','(no name)')[:20]:<20} │ {e.get('email','')[:26]:<26} │ ••••••••"
            style = "class:selected" if e["id"] == self._selected_id else ""
            toks += [(style, row, self._make_select(e["id"])), ("", "\n")]
        if start + len(shown) < len(entries):
            toks += [("class:dim", "  ▼ more ▼")]
        return toks

    def _make_select(self, entry_id: str):
        def handler(mouse_event) -> None:
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                self._selected_id = entry_id
                self._invalidate()
        return handler

    def _current(self) -> dict | None:
        for e in self.vault.entries:
            if e["id"] == self._selected_id:
                return e
        return None

    def _detail_tokens(self):
        e = self._current()
        if not e:
            return [("class:dim", "no entries — [n]ew / [i]mport")]
        toks: list = [
            ("class:accent", f"┤ {e.get('name','(no name)')} ├\n"),
            ("class:dim", "mail  "),
            ("", f"{e.get('email','—') or '—'}", self._click_copy_mail),
            ("", "\n"),
            ("class:dim", "pass  "),
        ]
        pw = e.get("password", "")
        if pw:
            toks += [("class:good", pw, self._click_copy_pass), ("", "\n")]
        else:
            toks += [("class:dim", "(empty)\n")]
        toks += [
            ("class:dim", "url   "),
            ("", f"{e.get('url','—') or '—'}\n"),
            ("class:dim", "note  \n"),
            ("", f"{e.get('notes','') or '—'}\n"),
            ("class:dim", f"upd {_fmt_ts(e.get('modified',0))} · new {_fmt_ts(e.get('created',0))}\n"),
        ]
        label, pct, color = _strength(pw)
        toks += [
            ("class:dim", "strength "),
            (f"{color}", _meter_bar(pct)),
            ("", f"  {pct:>3}% {label}  len {len(pw)}"),
        ]
        return toks

    def _click_copy_pass(self, mouse_event) -> None:
        if mouse_event.event_type == MouseEventType.MOUSE_UP:
            self._copy_pass_only()

    def _click_copy_mail(self, mouse_event) -> None:
        if mouse_event.event_type == MouseEventType.MOUSE_UP:
            self._copy_mail()

    def _status_tokens(self):
        return [("class:good", "▸ "), ("class:status", self._status)]

    def _menubar_tokens(self):
        items = [("n", "new"), ("e", "edit"), ("y", "copy"),
                 ("d", "del"), ("j/k", "move"), ("/", "filter"),
                 ("i", "import"), ("x", "export"), ("q", "quit")]
        toks: list = []
        for key, label in items:
            toks += [("class:menukey", f"[{key}]"), ("class:menudim", f"{label}  ")]
        return toks

    # ---------- state ----------

    def _invalidate(self) -> None:
        if self.app is not None:
            self.app.invalidate()

    def _log(self, msg: str) -> None:
        self._status = msg
        self._invalidate()

    def _refresh_selection(self, keep: bool = True) -> None:
        entries = self._visible_entries()
        ids = {e["id"] for e in entries}
        if not keep or self._selected_id not in ids:
            self._selected_id = entries[0]["id"] if entries else None

    def _move(self, delta: int) -> None:
        entries = self._visible_entries()
        if not entries:
            return
        ids = [e["id"] for e in entries]
        try:
            i = ids.index(self._selected_id)
        except ValueError:
            i = 0 if delta > 0 else len(ids) - 1
            self._selected_id = ids[i]
            self._invalidate()
            return
        i = max(0, min(len(ids) - 1, i + delta))
        self._selected_id = ids[i]
        self._invalidate()

    def _on_filter_changed(self, _buffer) -> None:
        self._refresh_selection(keep=True)
        self._invalidate()

    # ---------- copy ----------

    def _copy_mail(self) -> None:
        e = self._current()
        if e and e.get("email"):
            ok = copy_text(e["email"])
            self._log("mail copied" if ok else "no clipboard")
        else:
            self._log("nothing to copy")

    def _copy_pass_only(self) -> None:
        e = self._current()
        if e and e.get("password"):
            ok = copy_text(e["password"])
            self._log("pass copied to clipboard" if ok else "no clipboard")
        else:
            self._log("nothing to copy")

    # ---------- key bindings ----------

    def _active(self):
        return Condition(lambda: not self._closers and not self._in_filter)

    def _register_keys(self) -> None:
        kb = self.kb
        active = self._active()

        @kb.add("q", filter=active)
        def _(event):
            event.app.exit()

        @kb.add("c-c", filter=Condition(lambda: True))
        @kb.add("c-d", filter=Condition(lambda: True))
        def _(event):
            event.app.exit()

        @kb.add("n", filter=active)
        def _(event):
            self._open_entry(None)

        @kb.add("e", filter=active)
        def _(event):
            self._open_edit()

        @kb.add("d", filter=active)
        def _(event):
            self._open_delete()

        @kb.add("y", filter=active)
        def _(event):
            self._copy_pass_only()

        @kb.add("c", filter=active)
        def _(event):
            self._copy_mail()

        @kb.add("i", filter=active)
        def _(event):
            self._open_import()

        @kb.add("x", filter=active)
        def _(event):
            self._open_export()

        @kb.add("/", filter=active)
        def _(event):
            self._in_filter = True
            event.app.layout.focus(self.search_field)

        @kb.add("j", filter=active)
        @kb.add("down", filter=active)
        def _(event):
            self._move(1)

        @kb.add("k", filter=active)
        @kb.add("up", filter=active)
        def _(event):
            self._move(-1)

        @kb.add("pageup", filter=active)
        def _(event):
            self._move(-self._list_height())

        @kb.add("pagedown", filter=active)
        def _(event):
            self._move(self._list_height())

        @kb.add("escape", eager=True)
        def _(event):
            if self._closers:
                closer = self._closers[-1]
                if closer is not None:
                    closer()
            elif self._in_filter:
                self._leave_filter()

        @kb.add("enter", filter=Condition(lambda: self._in_filter and not self._closers))
        def _(event):
            self._leave_filter()

        def _in_entry_fields():
            if not self._entry_field_order or not self._closers:
                return False
            try:
                focused = self.app.layout.current
                return focused in self._entry_field_order
            except Exception:
                return False

        @kb.add("enter", filter=Condition(_in_entry_fields))
        def _(event):
            pass

        def _entry_open():
            return bool(self._entry_field_order and self._closers)

        @kb.add("c-g", filter=Condition(_entry_open))
        def _(event):
            pw_field = self._entry_fields.get("password")
            if pw_field:
                self._open_generator(
                    password=pw_field.text,
                    on_use=lambda p: self._gen_used(pw_field, p))

    def _leave_filter(self) -> None:
        self._in_filter = False
        if self.app is not None:
            self.app.layout.focus(self.btn_mail)
        self._invalidate()

    # ---------- floats / dialogs ----------

    def _dialog(self, title: str, body, buttons: list[Button],
                border: str = "class:frame.border") -> HSplit:
        """Dialog box with a tintable outline, built from raw containers
        only (no Dialog widget, so no default background classes leak in)."""
        from functools import partial
        from prompt_toolkit.filters import has_completions
        kb = KeyBindings()
        kb.add("tab", filter=~has_completions)(focus_next)
        kb.add("s-tab", filter=~has_completions)(focus_previous)
        fill = partial(Window, style=border)
        btnrow = VSplit(buttons, padding=3, height=1)
        top = VSplit([
            fill(char="╭", width=1, height=1),
            fill(char="─", height=1),
            Label([("class:dlgtitle", f" {title} ")], dont_extend_width=True),
            fill(char="─", height=1),
            fill(char="╮", width=1, height=1),
        ], height=1)
        bottom = VSplit([
            fill(char="╰", width=1, height=1),
            fill(char="─", height=1),
            fill(char="╯", width=1, height=1),
        ], height=1)
        middle = VSplit([
            fill(char="│", width=1),
            HSplit([body, btnrow]),
            fill(char="│", width=1),
        ])
        box = HSplit([top, middle, bottom], key_bindings=kb)
        return box

    def _push_float(self, dialog, focus_elem=None, on_esc=None) -> None:
        fl = Float(content=dialog)
        self._floats.append(fl)
        self._closers.append(on_esc)
        self._focus_elems.append(focus_elem)
        if self.app is not None:
            if focus_elem is not None:
                self.app.layout.focus(focus_elem)
            self._invalidate()

    def _pop_float(self) -> None:
        if self._floats:
            self._floats.pop()
        if self._closers:
            self._closers.pop()
        if self._focus_elems:
            self._focus_elems.pop()
        if not self._floats:
            self._entry_field_order = []
            self._entry_save = None
        if self.app is not None:
            # return focus to the dialog underneath (if any), else main actions
            target = self._focus_elems[-1] if self._focus_elems else None
            self.app.layout.focus(target or self.btn_mail)
            self._invalidate()

    def _field(self, text: str = "", password: bool = False, height: int = 1,
               multiline: bool = False, accept=None) -> TextArea:
        return TextArea(
            text=text, height=height if not multiline else 5,
            multiline=multiline, password=password,
            wrap_lines=False, focusable=True, focus_on_click=True,
            accept_handler=accept,
        )

    # ----- entry (new / edit) -----

    def _open_entry(self, entry: dict | None) -> None:
        entry = entry or {}
        f_name = self._field(entry.get("name", ""))
        f_email = self._field(entry.get("email", ""))
        f_pass = self._field(entry.get("password", ""), password=True)
        f_url = self._field(entry.get("url", ""))
        f_notes = self._field(entry.get("notes", ""), multiline=True)
        err = Label("", style="class:err")
        title = "EDIT" if entry.get("id") else "NEW"

        def do_save(_buffer=None) -> None:
            name = f_name.text.strip()
            if not name:
                err.text = "name is required"
                self._invalidate()
                return
            data = {
                "name": name,
                "email": f_email.text.strip(),
                "password": f_pass.text,
                "url": f_url.text.strip(),
                "notes": f_notes.text,
            }
            if entry.get("id"):
                self.vault.update(entry["id"], data)
                self._log("entry updated")
            else:
                created = new_entry(**data)
                self.vault.add(created)
                self._selected_id = created["id"]
                self._log(f"saved [{created['name']}]")
            try:
                self.search_field.text = ""
            except Exception:
                pass
            self._pop_float()

        def do_generate() -> None:
            self._open_generator(password=f_pass.text,
                                 on_use=lambda pw: self._gen_used(f_pass, pw))

        _fields = [f_name, f_email, f_pass, f_url, f_notes]
        self._entry_fields = {
            "name": f_name, "email": f_email, "password": f_pass,
            "url": f_url, "notes": f_notes,
        }
        self._entry_field_order = _fields
        self._entry_save = do_save

        def _make_next_handler(fields, idx):
            def handler(_buffer=None):
                if idx < len(fields) - 1:
                    self.app.layout.focus(fields[idx + 1])
                else:
                    do_save()
                return True
            return handler

        for i, f in enumerate(_fields):
            f.accept_handler = _make_next_handler(_fields, i)

        body = HSplit([
            Label("name:", style="class:dim"),
            f_name,
            Label("email / user:", style="class:dim"),
            f_email,
            Label("password:", style="class:dim"),
            f_pass,
            VSplit([Button("[ctrl+g]enerate…", handler=do_generate, width=18)], padding=0),
            Label("url:", style="class:dim"),
            f_url,
            Label("notes:", style="class:dim"),
            f_notes,
            err,
        ])
        dlg = self._dialog(
            f"┤ {title} ├  entry", body,
            [Button("[⏎]save", handler=do_save),
             Button("[esc]", handler=self._pop_float)],
            border="class:dlg.border",
        )
        self._push_float(dlg, focus_elem=f_name, on_esc=self._pop_float)

    def _gen_used(self, field: TextArea, password: str | None) -> None:
        if password:
            field.text = password
            self._log("generated password inserted")

    def _open_edit(self) -> None:
        e = self._current()
        if not e:
            self._log("nothing selected")
            return
        self._open_entry(dict(e))

    # ----- generator -----

    def _open_generator(self, password: str = "", on_use=None) -> None:
        state = {"upper": True, "lower": True, "digits": True, "symbols": True,
                 "preview": ""}
        f_len = self._field("20")
        preview = FormattedTextControl(lambda: [("class:good", state["preview"])])
        toggles: dict[str, Button] = {}

        def regen() -> None:
            try:
                n = int(f_len.text.strip() or "20")
            except ValueError:
                n = 20
            n = max(8, min(128, n))
            try:
                state["preview"] = generate_password(
                    n, upper=state["upper"], lower=state["lower"],
                    digits=state["digits"], symbols=state["symbols"])
            except ValueError as ex:
                state["preview"] = f"({ex})"
            self._invalidate()

        def make_toggle(key: str, label: str):
            def flip() -> None:
                state[key] = not state[key]
                toggles[key].text = f"[{'x' if state[key] else ' '}] {label}"
                regen()
            btn = Button(f"[x] {label}", handler=flip)
            toggles[key] = btn
            return btn

        f_len.buffer.on_text_changed += lambda _b: regen()
        f_len.accept_handler = lambda _b: use()
        self._gen_len = f_len
        regen()

        def use() -> None:
            pw = state["preview"]
            self._pop_float()
            if on_use and pw and not pw.startswith("("):
                on_use(pw)

        body = HSplit([
            Label("length [8-128]:", style="class:dim"),
            f_len,
            VSplit([make_toggle("upper", "UPPER A-Z"),
                    make_toggle("lower", "lower a-z")], padding=2),
            VSplit([make_toggle("digits", "digits 0-9"),
                    make_toggle("symbols", "symbols !@#")], padding=2),
            Window(content=preview, height=1),
        ])
        dlg = self._dialog(
            "┤ GEN ├  generate password", body,
            [Button("[r]egen", handler=regen),
             Button("[⏎]use", handler=use),
             Button("[esc]", handler=self._pop_float)],
        )
        self._push_float(dlg, focus_elem=f_len, on_esc=self._pop_float)

    # ----- confirm / import -----

    def _open_delete(self) -> None:
        e = self._current()
        if not e:
            return

        def yes() -> None:
            self.vault.delete(e["id"])
            self._selected_id = None
            self._refresh_selection(keep=False)
            self._log("entry deleted")
            self._pop_float()

        dlg = self._dialog(
            "┤ DEL ├  confirm",
            Label(f"delete [{e.get('name','?')}]?"),
            [Button("[⏎]delete", handler=yes),
             Button("[esc]", handler=self._pop_float)],
        )
        self._push_float(dlg, on_esc=self._pop_float)

    def _open_import(self) -> None:
        f_path = self._field("")
        err = Label("", style="class:err")

        def go(_buffer=None) -> None:
            path = f_path.text.strip()
            if not path:
                err.text = "enter a file path"
                self._invalidate()
                return
            try:
                items = parse_bitwarden_file(path)
            except Exception as ex:
                err.text = f"import failed: {ex}"
                self._invalidate()
                return
            if not items:
                self._log("nothing imported")
            else:
                for item in items:
                    self.vault.add(item)
                self._log(f"imported {len(items)} entries")
            self._refresh_selection(keep=False)
            self._pop_float()

        f_path.accept_handler = go

        body = HSplit([
            Label("path to unencrypted bitwarden json:", style="class:dim"),
            f_path,
            err,
        ])
        dlg = self._dialog(
            "┤ IMPORT ├  bitwarden json", body,
            [Button("[⏎]import", handler=go),
             Button("[esc]", handler=self._pop_float)],
        )
        self._push_float(dlg, focus_elem=f_path, on_esc=self._pop_float)

    def _open_export(self) -> None:
        f_path = self._field("")
        err = Label("", style="class:err")

        def go(_buffer=None) -> None:
            path = f_path.text.strip()
            if not path:
                err.text = "enter a file path"
                self._invalidate()
                return
            try:
                export_bitwarden(self.vault.entries, path)
            except Exception as ex:
                err.text = f"export failed: {ex}"
                self._invalidate()
                return
            self._log(f"exported {len(self.vault.entries)} entries")
            self._pop_float()

        f_path.accept_handler = go

        body = HSplit([
            Label("save path for unencrypted bitwarden json:", style="class:dim"),
            f_path,
            err,
        ])
        dlg = self._dialog(
            "┤ EXPORT ├  bitwarden json", body,
            [Button("[⏎]export", handler=go),
             Button("[esc]", handler=self._pop_float)],
        )
        self._push_float(dlg, focus_elem=f_path, on_esc=self._pop_float)

    # ---------- run ----------

    def _build_app(self, input=None, output=None) -> Application:
        kwargs: dict = {
            "layout": self.layout,
            "key_bindings": self.kb,
            "style": STYLE,
            "full_screen": True,
            "mouse_support": True,
        }
        if input is not None:
            kwargs["input"] = input
        if output is not None:
            kwargs["output"] = output
        return Application(**kwargs)

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(1)
            self._invalidate()

    def run(self) -> None:
        self.app = self._build_app()
        self.app.layout.focus(self.btn_mail)

        async def runner():
            ticker = asyncio.ensure_future(self._ticker())
            try:
                await self.app.run_async()
            finally:
                ticker.cancel()

        asyncio.run(runner())
