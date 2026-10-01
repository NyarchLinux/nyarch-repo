import re

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango

try:
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte

    VTE_AVAILABLE = True
except (ImportError, ValueError):
    Vte = None
    VTE_AVAILABLE = False

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r")


class ConsolePanel(Gtk.Box):
    def __init__(self, **kwargs):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL, spacing=6, **kwargs
        )
        self.using_vte = VTE_AVAILABLE
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = Gtk.Label(label="Terminal", halign=Gtk.Align.START)
        label.add_css_class("heading")
        label.add_css_class("dim-label")
        header.append(label)
        header.append(Gtk.Box(hexpand=True))
        clear_btn = Gtk.Button(
            icon_name="edit-clear-all-symbolic",
            tooltip_text="Clear terminal output",
        )
        clear_btn.add_css_class("flat")
        clear_btn.connect("clicked", lambda *_: self.clear())
        header.append(clear_btn)
        self.append(header)

        if self.using_vte:
            self.term = Vte.Terminal()
            try:
                self.term.set_scrollback_lines(100000)
                self.term.set_font(Pango.FontDescription.from_string("Monospace 10"))
                self.term.set_mouse_autohide(True)
            except Exception:
                pass
            frame = Gtk.Frame()
            frame.set_child(self.term)
            frame.set_vexpand(True)
            self.append(frame)
        else:
            scrolled = Gtk.ScrolledWindow(
                vexpand=True,
                hscrollbar_policy=Gtk.PolicyType.NEVER,
            )
            self.buf = Gtk.TextBuffer()
            self.view = Gtk.TextView(
                buffer=self.buf,
                editable=False,
                cursor_visible=False,
                wrap_mode=Gtk.WrapMode.WORD_CHAR,
                monospace=True,
            )
            self.view.add_css_class("console-text")
            self.tag_dim = self.buf.create_tag("dim", foreground="#9a9a9a")
            self.tag_err = self.buf.create_tag("err", foreground="#ff6b6b")
            scrolled.set_child(self.view)
            self.append(scrolled)

    @property
    def can_spawn_pty(self):
        return self.using_vte

    def write(self, data):
        if not data:
            return
        if self.using_vte:
            try:
                self.term.feed(data)
                return
            except Exception:
                pass
        text = ANSI_RE.sub("", data.decode("utf-8", "replace"))
        end = self.buf.get_end_iter()
        self.buf.insert(end, text)
        adj = self.view.get_vadjustment()
        adj.set_value(adj.get_upper())

    def write_line(self, text=""):
        self.write((text + "\n").encode())

    def clear(self):
        if self.using_vte:
            try:
                self.term.reset(True, True)
            except Exception:
                self.term.feed(b"\x1b[2J\x1b[H")
        else:
            self.buf.set_text("")
