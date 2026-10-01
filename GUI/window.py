import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from services.repo_db import RepoDatabase
from services.aur import AurClient
from services.builder import Builder
from services.runner import Runner
from services.console import ConsolePanel
from pages.packages import PackagesPage
from pages.aur_add import AurAddPage
from pages.updates import UpdatesPage
from pages.health import HealthPage
from pages.local_builds import LocalBuildsPage
from pages.keyring import KeyringPage
from pages.mirrors import MirrorsPage


class AppContext:
    pass


NAV_ITEMS = [
    ("packages", "package-x-generic-symbolic", "Packages"),
    ("aur", "list-add-symbolic", "Add from AUR"),
    ("updates", "software-update-available-symbolic", "AUR Updates"),
    ("health", "security-high-symbolic", "Integrity"),
    ("local", "applications-engineering-symbolic", "Local PKGBUILDs"),
    ("keyring", "dialog-password-symbolic", "Keyring"),
    ("mirrors", "applications-internet-symbolic", "Mirrors"),
]


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(
            application=app,
            title="Nyarch Repo Manager",
            default_width=1240,
            default_height=820,
        )
        self.ctx = AppContext()
        gui_dir = os.path.dirname(os.path.abspath(__file__))
        ctx = self.ctx
        ctx.win = self
        ctx.repo_root = os.path.dirname(gui_dir)
        ctx.x86_64_dir = os.path.join(ctx.repo_root, "x86_64")
        ctx.pkgbuilds_dir = os.path.join(ctx.repo_root, "PkgBuilds")
        ctx.aur_build_dir = os.path.join(ctx.repo_root, "AURBuilder", "build")
        ctx.aur_pkgs_file = os.path.join(ctx.repo_root, "AURpkgs")
        ctx.aur_pkgs_full_file = os.path.join(ctx.repo_root, "AURpkgs_full")
        ctx.toast_overlay = Adw.ToastOverlay()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        split = Adw.NavigationSplitView(
            sidebar_width_fraction=0.22,
            min_sidebar_width=200,
            max_sidebar_width=320,
            hexpand=True,
            vexpand=True,
        )

        sidebar_page = Adw.NavigationPage(title="Sidebar")
        sidebar_toolbar = Adw.ToolbarView()
        sidebar_header = Adw.HeaderBar()
        sidebar_title = Adw.WindowTitle(
            title="Nyarch Repo", subtitle=os.path.basename(ctx.repo_root)
        )
        sidebar_header.set_title_widget(sidebar_title)
        sidebar_toolbar.add_top_bar(sidebar_header)

        sidebar_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True
        )
        self.nav_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.nav_list.add_css_class("navigation-sidebar")
        for _key, icon, label in NAV_ITEMS:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=10, margin_top=4, margin_bottom=4)
            img = Gtk.Image.new_from_icon_name(icon)
            lbl = Gtk.Label(label=label, halign=Gtk.Align.START, xalign=0)
            row._page_key = _key
            box.append(img)
            box.append(lbl)
            row.set_child(box)
            self.nav_list.append(row)
        sidebar_scroll.set_child(self.nav_list)
        sidebar_toolbar.set_content(sidebar_scroll)
        sidebar_page.set_child(sidebar_toolbar)
        split.set_sidebar(sidebar_page)

        content_page = Adw.NavigationPage(title="Nyarch Repo Manager")
        content_toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title="Packages", subtitle="")
        header.set_title_widget(self.title_widget)
        self.spinner = Gtk.Spinner()
        self.spinner.set_visible(False)
        header.pack_end(self.spinner)
        self.term_toggle = Gtk.ToggleButton(
            icon_name="utilities-terminal-symbolic",
            active=False,
            tooltip_text="Toggle terminal panel",
        )
        self.term_toggle.connect("toggled", self._on_term_toggled)
        header.pack_end(self.term_toggle)

        self.stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True
        )
        content_toolbar.add_top_bar(header)

        console = ConsolePanel()
        self.console = console
        self.term_frame = Gtk.Frame()
        self.term_frame.add_css_class("term-frame")
        self.term_frame.set_child(console)
        self.term_frame.set_size_request(-1, 64)
        self.term_frame.set_visible(False)
        self._last_term_pos = None

        runner = Runner(console)
        runner.on_busy_changed = self._on_busy_changed
        runner.on_current_changed = self._on_current_changed
        ctx.runner = runner
        ctx.console = console

        ctx.repo_db = RepoDatabase(ctx.repo_root)
        ctx.aur = AurClient()
        ctx.builder = Builder(ctx)

        self.pages = {
            "packages": PackagesPage(ctx),
            "aur": AurAddPage(ctx),
            "updates": UpdatesPage(ctx),
            "health": HealthPage(ctx),
            "local": LocalBuildsPage(ctx),
            "keyring": KeyringPage(ctx),
            "mirrors": MirrorsPage(ctx),
        }
        for key, page in self.pages.items():
            self.stack.add_named(page, key)

        stack_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        stack_holder.append(self.stack)

        self.main_paned = Gtk.Paned(
            orientation=Gtk.Orientation.VERTICAL,
            resize_start_child=True,
            resize_end_child=False,
            shrink_start_child=False,
            shrink_end_child=False,
            vexpand=True,
        )
        self.main_paned.add_css_class("wide-handle")
        self.main_paned.set_start_child(stack_holder)
        self.main_paned.set_end_child(self.term_frame)
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.append(self.main_paned)
        content_toolbar.set_content(content_box)
        content_page.set_child(content_toolbar)
        split.set_content(content_page)

        outer.append(split)

        ctx.toast_overlay.set_child(outer)
        self.set_content(ctx.toast_overlay)

        self.nav_list.connect("row-selected", self._on_nav_changed)
        self.nav_list.select_row(self.nav_list.get_row_at_index(0))

        GLib.idle_add(self._initial_load)

    def _initial_load(self):
        self.reload_repo(lambda: None)
        return False

    def toast(self, message):
        t = Adw.Toast(title=message, timeout=5)
        self.ctx.toast_overlay.add_toast(t)

    def reload_repo(self, done=None):
        db = self.ctx.repo_db

        def work():
            db.load()

            def apply():
                for p in self.pages.values():
                    try:
                        p.refresh()
                    except Exception as e:
                        print(f"refresh error: {e}")
                if db.error:
                    self.toast(db.error)
                if done:
                    done()
                return False

            GLib.idle_add(apply)

        threading.Thread(target=work, daemon=True).start()

    def goto_package(self, name):
        self.show_page("packages")
        self.pages["packages"].focus_name(name)

    def show_page(self, key):
        index = [k for k, _, _ in NAV_ITEMS].index(key)
        row = self.nav_list.get_row_at_index(index)
        if row:
            self.nav_list.select_row(row)
        self.stack.set_visible_child_name(key)
        label = dict((k, l) for k, _, l in NAV_ITEMS)[key]
        self.title_widget.set_title(label)

    def _on_nav_changed(self, _list, row):
        if row is None:
            return
        key = row._page_key
        self.stack.set_visible_child_name(key)
        label = dict((k, l) for k, _, l in NAV_ITEMS)[key]
        self.title_widget.set_title(label)

    def _on_busy_changed(self, busy):
        def apply():
            if busy:
                self.spinner.start()
                self.spinner.set_visible(True)
            else:
                self.spinner.stop()
                self.spinner.set_visible(False)
            return False

        GLib.idle_add(apply)

    def _on_current_changed(self, title):
        def apply():
            self.title_widget.set_subtitle(title or "")
            if title and not self.term_toggle.get_active():
                self.term_toggle.set_active(True)
            return False

        GLib.idle_add(apply)

    def _on_term_toggled(self, btn):
        active = btn.get_active()
        if active:
            self.term_frame.set_visible(True)

            def restore_pos():
                height = self.main_paned.get_height()
                pos = self._last_term_pos or max(180, height - 220)
                self.main_paned.set_position(
                    min(pos, max(0, height - 80))
                )
                return False

            GLib.idle_add(restore_pos)
        else:
            self._last_term_pos = self.main_paned.get_position()
            self.term_frame.set_visible(False)
            self.main_paned.set_position(0)
