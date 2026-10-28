import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk


class EditorPage(Gtk.Box):
    def __init__(self, ctx):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.ctx = ctx
        self.path = None
        self._original = ""
        self._loading = False
        bar = Gtk.Box(spacing=8)
        self.title = Gtk.Label(label="Open a PKGBUILD from Local PKGBUILDs or the AUR.",
                               xalign=0, hexpand=True, ellipsize=3)
        bar.append(self.title)
        self.reload_button = Gtk.Button(label="Reload", sensitive=False)
        self.reload_button.connect("clicked", lambda *_: self.confirm_discard(self._load))
        bar.append(self.reload_button)
        terminal = Gtk.Button(icon_name="utilities-terminal-symbolic",
                              tooltip_text="Open a shell in this package directory")
        terminal.connect("clicked", lambda *_: ctx.open_terminal(
            os.path.dirname(self.path) if self.path else ctx.repo_root))
        bar.append(terminal)
        self.save_button = Gtk.Button(label="Save", sensitive=False)
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", lambda *_: self.save())
        bar.append(self.save_button)
        self.append(bar)
        self.buffer = Gtk.TextBuffer()
        self.buffer.connect("changed", self._changed)
        self.view = Gtk.TextView(buffer=self.buffer, monospace=True,
                                 editable=False,
                                 left_margin=12, right_margin=12,
                                 top_margin=12, bottom_margin=12)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key_pressed)
        self.view.add_controller(keys)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(self.view)
        self.append(scroll)
        self.status = Gtk.Label(label="Ctrl+S to save. PKGBUILDs execute code when built.",
                                xalign=0)
        self.status.add_css_class("dim-label")
        self.append(self.status)

    def text(self):
        return self.buffer.get_text(
            self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)

    @property
    def dirty(self):
        return self.path is not None and self.text() != self._original

    def _changed(self, *_):
        if not self._loading:
            self.save_button.set_sensitive(self.dirty)
            if self.path:
                self.title.set_text(("* " if self.dirty else "") + self.path)

    def _key_pressed(self, _controller, keyval, _keycode, state):
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self.save()
            return True
        return False

    def open(self, path):
        path = os.path.abspath(path)
        if path == self.path:
            self.ctx.win.show_page("editor")
            return

        def load():
            if self._load(path):
                self.ctx.win.show_page("editor")

        self.confirm_discard(load)

    def _load(self, path=None):
        path = path or self.path
        if not path:
            return False
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            stat = os.stat(path)
        except (OSError, UnicodeError) as error:
            self.ctx.toast(f"Cannot open PKGBUILD: {error}")
            return False
        self.path = path
        self._original = text
        self._mtime = stat.st_mtime_ns
        self._loading = True
        self.buffer.set_text(text)
        self._loading = False
        self._changed()
        self.reload_button.set_sensitive(True)
        self.view.set_editable(True)
        self.view.grab_focus()
        return True

    def save(self):
        if not self.path:
            return False
        try:
            if os.stat(self.path).st_mtime_ns != self._mtime:
                self.ctx.toast("PKGBUILD changed on disk. Reload before saving.")
                return False
            text = self.text()
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(text)
            self._original = text
            self._mtime = os.stat(self.path).st_mtime_ns
        except OSError as error:
            self.ctx.toast(f"Cannot save PKGBUILD: {error}")
            return False
        self._changed()
        self.ctx.toast("PKGBUILD saved")
        return True

    def confirm_discard(self, action):
        if not self.dirty:
            action()
            return
        dialog = Adw.MessageDialog.new(
            self.ctx.win, "Save PKGBUILD changes?",
            "Your unsaved edits will be lost if you discard them.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("discard", "Discard")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def response(_dialog, choice):
            if choice == "discard" or (choice == "save" and self.save()):
                action()

        dialog.connect("response", response)
        dialog.present()

    def refresh(self):
        pass
