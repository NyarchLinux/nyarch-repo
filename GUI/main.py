import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gdk

from window import MainWindow


class NyarchRepoManagerApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="moe.nyarchlinux.repomanager",
            flags=0,
        )

    def do_activate(self):
        win = self.props.active_window
        if win is None:
            win = MainWindow(self)
        win.present()
        self.hold()


def main():
    app = NyarchRepoManagerApp()
    display = Gdk.Display.get_default()
    if display is not None:
        provider = Gtk.CssProvider()
        try:
            provider.load_from_path(os.path.join(BASE_DIR, "style.css"))
        except Exception as e:
            print(f"Could not load style.css: {e}")
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
