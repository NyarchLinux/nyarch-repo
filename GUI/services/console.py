import re
import os
import signal

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango, GLib

try:
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte

    VTE_AVAILABLE = True
except (ImportError, ValueError):
    Vte = None
    VTE_AVAILABLE = False

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r")


class ConsolePanel(Gtk.Box):
    def __init__(self, cwd=None, **kwargs):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL, spacing=6, **kwargs
        )
        self.using_vte = VTE_AVAILABLE
        self.cwd = cwd or os.getcwd()
        self.shells = {}
        self.stack = Gtk.Stack(vexpand=True)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.append(Gtk.StackSwitcher(stack=self.stack))
        header.append(Gtk.Box(hexpand=True))
        self.shell_button = Gtk.Button(
            icon_name="utilities-terminal-symbolic", tooltip_text="Open interactive shell",
            sensitive=self.using_vte,
        )
        if not self.using_vte:
            self.shell_button.set_tooltip_text("Install vte4 for an interactive terminal")
        self.shell_button.connect("clicked", lambda *_: self.open_shell())
        header.append(self.shell_button)
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
            self.stack.add_titled(frame, "output", "Build output")
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
            self.stack.add_titled(scrolled, "output", "Build output")
        self.append(self.stack)

    def show_output(self):
        self.stack.set_visible_child_name("output")

    def open_shell(self, cwd=None):
        if not self.using_vte:
            self.write_line("Interactive shells require GTK 4 VTE (vte4 on Arch Linux).")
            return False
        cwd = os.path.abspath(cwd or self.cwd)
        shell = self.shells.get(cwd)
        if shell is None:
            term = Vte.Terminal()
            term.set_scrollback_lines(100000)
            term.set_font(Pango.FontDescription.from_string("Monospace 10"))
            shell = {"term": term, "pid": None, "starting": False}
            self.shells[cwd] = shell
            name = f"shell-{len(self.shells)}"
            self.stack.add_titled(term, name, f"Shell: {os.path.basename(cwd)}")
            term.connect("child-exited", lambda *_: shell.update(pid=None, starting=False))
        term = shell["term"]
        self.stack.set_visible_child(term)
        if shell["pid"] is None and not shell["starting"]:
            shell["starting"] = True

            def spawned(_term, pid, error=None, _data=None):
                shell["starting"] = False
                shell["pid"] = int(pid) if pid > 0 else None
                if pid < 0:
                    term.feed(f"\r\nCould not start shell: {error}\r\n".encode())

            executable = os.environ.get("SHELL") or "/bin/bash"
            if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
                executable = "/bin/bash"
            try:
                term.spawn_async(
                    pty_flags=Vte.PtyFlags.DEFAULT, working_directory=cwd,
                    argv=[executable, "-i"], envv=None,
                    spawn_flags=GLib.SpawnFlags.DEFAULT, timeout=-1,
                    cancellable=None, callback=spawned,
                )
            except (GLib.Error, TypeError) as error:
                shell["starting"] = False
                term.feed(f"\r\nCould not start shell: {error}\r\n".encode())
        term.grab_focus()
        return True

    def close_shells(self):
        for shell in self.shells.values():
            if shell["pid"]:
                try:
                    os.kill(shell["pid"], signal.SIGHUP)
                except ProcessLookupError:
                    pass

    @property
    def can_spawn_pty(self):
        return self.using_vte

    def write(self, data):
        if not data:
            return
        if self.using_vte:
            try:
                self.term.feed(data.replace(b"\n", b"\r\n"))
                return
            except Exception:
                return
        text = ANSI_RE.sub("", data.decode("utf-8", "replace"))
        end = self.buf.get_end_iter()
        self.buf.insert(end, text)
        adj = self.view.get_vadjustment()
        adj.set_value(adj.get_upper())

    def write_line(self, text=""):
        self.write((text + "\n").encode())

    def clear(self):
        if self.using_vte:
            visible = self.stack.get_visible_child()
            term = self.term if self.stack.get_visible_child_name() == "output" else visible
            try:
                term.reset(True, True)
            except Exception:
                term.feed(b"\x1b[2J\x1b[H")
        else:
            self.buf.set_text("")
