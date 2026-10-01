import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from services import mirrors as msvc


class MirrorsPage(Gtk.Box):
    def __init__(self, ctx):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        self.ctx = ctx
        self.mirrorlist_path = os.path.join(
            ctx.pkgbuilds_dir, "nyarch-mirrorlist", "mirrorlist"
        )
        self.pkg_dir = os.path.join(ctx.pkgbuilds_dir, "nyarch-mirrorlist")
        self.rows = []
        self._testing = False

        intro = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
        intro.set_markup(
            "Edits <b>PkgBuilds/nyarch-mirrorlist/mirrorlist</b>, the source of "
            "the nyarch-mirrorlist package. Save, then bump &amp; rebuild to ship "
            "it. <tt>$arch</tt> in URLs is substituted by pacman."
        )
        intro.add_css_class("dim-label")
        self.append(intro)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_add = Gtk.Button(label="Add mirror")
        btn_add.connect("clicked", lambda *_: self.add_row(""))
        bar.append(btn_add)
        self.btn_test = Gtk.Button(label="Test all")
        self.btn_test.set_tooltip_text("Measure response time of every mirror")
        self.btn_test.connect("clicked", lambda *_: self.test_all())
        bar.append(self.btn_test)
        bar.append(Gtk.Box(hexpand=True))
        self.btn_save = Gtk.Button(label="Save mirrorlist")
        self.btn_save.add_css_class("suggested-action")
        self.btn_save.connect("clicked", lambda *_: self.save())
        bar.append(self.btn_save)
        self.btn_ship = Gtk.Button(label="Bump & rebuild package")
        self.btn_ship.set_tooltip_text(
            "Bump pkgver to today and rebuild nyarch-mirrorlist into the repo"
        )
        self.btn_ship.connect("clicked", lambda *_: self.bump_and_rebuild())
        bar.append(self.btn_ship)
        self.append(bar)

        self.status = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
        self.status.add_css_class("dim-label")
        self.append(self.status)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        clamp_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        clamp = Adw.Clamp(maximum_size=900, tightening_threshold=700)
        self.rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        clamp.set_child(self.rows_box)
        clamp_holder.append(clamp)
        scroll.set_child(clamp_holder)
        self.append(scroll)

        GLib.idle_add(lambda: (self.load(), False)[1])

    def _guard_busy(self):
        if self.ctx.runner.busy or self._testing:
            self.ctx.toast("Another operation is already running")
            return True
        return False

    def load(self):
        servers = msvc.read_servers(self.mirrorlist_path)
        self.clear_rows()
        for url in servers:
            self.add_row(url)
        if not servers:
            self.add_row("")
        self.status.set_text(f"{len(servers)} mirror(s) — unsaved changes are lost on reload")
        return False

    def clear_rows(self):
        while self.rows_box.get_first_child():
            self.rows_box.remove(self.rows_box.get_first_child())
        self.rows = []

    def add_row(self, url, latency=None):
        row = Adw.EntryRow(title="Mirror URL")
        row.set_text(url)

        up = Gtk.Button(icon_name="go-up-symbolic")
        up.add_css_class("flat")
        up.set_tooltip_text("Move up (tried earlier by pacman)")
        up.connect("clicked", lambda *_b, r=row: self.move(r, -1))
        row.add_suffix(up)

        down = Gtk.Button(icon_name="go-down-symbolic")
        down.add_css_class("flat")
        down.set_tooltip_text("Move down")
        down.connect("clicked", lambda *_b, r=row: self.move(r, +1))
        row.add_suffix(down)

        test = Gtk.Button(icon_name="media-playback-start-symbolic")
        test.add_css_class("flat")
        test.set_tooltip_text("Test this mirror")
        test.connect("clicked", lambda *_b, r=row: self.test_one(r))
        row.add_suffix(test)

        rm = Gtk.Button(icon_name="user-trash-symbolic")
        rm.add_css_class("flat")
        rm.add_css_class("error")
        rm.set_tooltip_text("Remove this mirror")
        rm.connect("clicked", lambda *_b, r=row: self.remove_row(r))
        row.add_suffix(rm)

        lat_lbl = Gtk.Label(valign=Gtk.Align.CENTER)
        lat_lbl.add_css_class("dim-label")
        lat_lbl.set_width_chars(10)
        if latency:
            lat_lbl.set_text(latency)
        row._latency_label = lat_lbl

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(row)
        box.append(lat_lbl)
        self.rows.append(row)
        self.rows_box.append(box)

    def _holder_of(self, row):
        c = self.rows_box.get_first_child()
        while c:
            if c.get_first_child() is row:
                return c
            c = c.get_next_sibling()
        return None

    def move(self, row, delta):
        try:
            i = self.rows.index(row)
        except ValueError:
            return
        j = i + delta
        if j < 0 or j >= len(self.rows):
            return
        self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
        holders = {id(r): self._holder_of(r) for r in self.rows}
        for r in self.rows:
            h = holders.get(id(r))
            if h is not None:
                self.rows_box.append(h)
        self.status.set_text("Order changed — remember to Save")

    def remove_row(self, row):
        if row in self.rows:
            self.rows.remove(row)
        c = self.rows_box.get_first_child()
        while c:
            nxt = c.get_next_sibling()
            if c.get_first_child() is row:
                self.rows_box.remove(c)
                break
            c = nxt
        if not self.rows:
            self.add_row("")
        self.status.set_text("Mirror removed — remember to Save")

    def collect(self):
        return [r.get_text().strip() for r in self.rows if r.get_text().strip()]

    def save(self, quiet=False):
        servers = self.collect()
        bad = [
            u for u in servers
            if not u.startswith(("http://", "https://", "ftp://", "rsync://"))
        ]
        if bad:
            self.ctx.toast("Not a valid URL: " + bad[0][:40])
            return False
        try:
            msvc.write_servers(self.mirrorlist_path, servers)
        except OSError as e:
            self.ctx.toast(f"Save failed: {e}")
            return False
        if not quiet:
            self.ctx.toast(f"Saved {len(servers)} mirror(s)")
            self.status.set_text(f"Saved {len(servers)} mirror(s)")
        return True

    def test_one(self, row):
        url = row.get_text().strip()
        lbl = getattr(row, "_latency_label", None)
        if not url:
            return
        lbl.set_text("…")

        def work():
            ms, info = msvc.check_mirror(url)
            GLib.idle_add(
                lambda: (
                    lbl.set_text(
                        f"{ms:.0f} ms" if ms is not None else f"fail ({info})"
                    ),
                    False,
                )[1]
            )

        threading.Thread(target=work, daemon=True).start()

    def test_all(self):
        if self._testing:
            return
        urls = self.collect()
        if not urls:
            return
        self._testing = True
        self.btn_test.set_sensitive(False)
        pairs = list(zip(urls, self.rows))
        done_count = {"n": 0}

        def work():
            for url, row in pairs:
                ms, info = msvc.check_mirror(url)
                lbl = getattr(row, "_latency_label", None)

                def upd(ms=ms, info=info, lbl=lbl):
                    if lbl:
                        lbl.set_text(
                            f"{ms:.0f} ms" if ms is not None else f"fail ({info})"
                        )
                    done_count["n"] += 1
                    if done_count["n"] >= len(pairs):
                        self._testing = False
                        self.btn_test.set_sensitive(True)
                        self.ctx.toast("Mirror test finished")
                    return False

                GLib.idle_add(upd)

        threading.Thread(target=work, daemon=True).start()

    def bump_and_rebuild(self):
        if self.ctx.runner.busy:
            self.ctx.toast("Another operation is already running")
            return
        if not self.save(quiet=True):
            return

        def done(summary):
            failed = summary["failed"]

            def apply():
                if failed:
                    self.ctx.toast("Rebuild failed — see terminal")
                else:
                    self.ctx.toast("nyarch-mirrorlist shipped to the repository")
                self.ctx.reload_repo()
                self.btn_ship.set_sensitive(True)
                return False

            GLib.idle_add(apply)

        try:
            new_ver = self.ctx.builder.bump_pkgver(self.pkg_dir)
        except RuntimeError as e:
            self.ctx.toast(str(e))
            return
        self.btn_ship.set_sensitive(False)
        self.status.set_text(f"pkgver={new_ver}, rebuilding…")
        self.ctx.builder.build_local(["nyarch-mirrorlist"], done)

    def refresh(self):
        self.load()
