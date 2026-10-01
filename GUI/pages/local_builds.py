import os
import re
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib


def parse_pkgbuild(path):
    info = {"name": None, "version": None, "rel": "1", "desc": ""}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(65536)
    except OSError:
        return info

    def field(name):
        m = re.search(rf"^{name}=(.*)$", text, re.M)
        if not m:
            return None
        val = m.group(1).strip()
        for q in ("'", '"'):
            if val.startswith(q) and len(val) > 1:
                end = val.find(q, 1)
                if end > 0:
                    return val[1:end]
        return val.split()[0] if val else None

    info["name"] = field("pkgname")
    info["version"] = field("pkgver")
    rel = field("pkgrel")
    if rel:
        info["rel"] = rel
    desc = field("pkgdesc")
    if desc:
        info["desc"] = desc
    return info


class LocalBuildsPage(Gtk.Box):
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
        self._dirs = []

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh_btn = Gtk.Button(label="Rescan PKGBUILDs")
        refresh_btn.connect("clicked", lambda *_: self.refresh())
        bar.append(refresh_btn)
        self.btn_all = Gtk.Button(label="Build all")
        self.btn_all.add_css_class("suggested-action")
        self.btn_all.connect("clicked", lambda *_: self.build_all())
        bar.append(self.btn_all)
        bar.append(Gtk.Box(hexpand=True))
        self.summary = Gtk.Label(valign=Gtk.Align.CENTER, wrap=True)
        self.summary.add_css_class("dim-label")
        bar.append(self.summary)
        self.append(bar)

        intro = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
        intro.set_markup(
            "Packages defined in <b>PkgBuilds/</b>. Building runs "
            "<tt>makepkg -sf --noconfirm</tt> in each directory with live output "
            "below, then moves artifacts to the repository."
        )
        intro.add_css_class("dim-label")
        self.append(intro)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        scroll.set_child(self.listbox)
        self.append(scroll)

    def _scan(self):
        base = self.ctx.pkgbuilds_dir
        found = []
        try:
            names = sorted(os.listdir(base))
        except OSError as e:
            self.summary.set_text(f"Cannot read {base}: {e}")
            return []
        for d in names:
            p = os.path.join(base, d)
            pb = os.path.join(p, "PKGBUILD")
            if not os.path.isdir(p) or not os.path.isfile(pb):
                continue
            info = parse_pkgbuild(pb)
            found.append(
                {
                    "dir": d,
                    "name": info["name"] or d,
                    "version": f"{info['version'] or '?'}-{info['rel']}",
                    "desc": info["desc"],
                    "local": self.ctx.repo_db.local_version(info["name"] or d),
                }
            )
        return found

    def refresh(self):
        while self.listbox.get_first_child():
            self.listbox.remove(self.listbox.get_first_child())
        self._dirs = self._scan()
        for item in self._dirs:
            row = Adw.ActionRow(title=f"{item['name']} {item['version']}")
            sub = item["desc"] or item["dir"]
            if item["local"]:
                from services.aur import vercmp

                cmp = vercmp(item["version"], item["local"])
                state = (
                    "up to date" if cmp <= 0 else f"in repo: {item['local']} (older)"
                )
                sub += f"  ·  in repository ({state})"
            row.set_subtitle(sub)

            btn = Gtk.Button(label="Build", valign=Gtk.Align.CENTER)
            btn.connect("clicked", lambda *_b, d=item["dir"]: self.build_one(d))
            row.add_suffix(btn)
            self.listbox.append(row)
        self.summary.set_text(f"{len(self._dirs)} local package(s) found")

    def _guard(self):
        if self.ctx.runner.busy:
            self.ctx.toast("Another operation is already running")
            return True
        return False

    def build_one(self, dirname):
        if self._guard():
            return
        self._start_build([dirname])

    def build_all(self):
        if self._guard():
            return
        if not self._dirs:
            self.refresh()
        dirs = [d["dir"] for d in self._dirs]
        if not dirs:
            self.ctx.toast("No PKGBUILD directories found")
            return
        self._start_build(dirs)

    def _start_build(self, dirs):
        self.btn_all.set_sensitive(False)
        self.summary.set_text(f"Building {len(dirs)} package(s)…")

        def done(summary):
            built = summary["built"]
            failed = summary["failed"]

            def apply():
                if failed:
                    self.ctx.toast("Failures: " + ", ".join(failed[:5]))
                    self.summary.set_text(
                        f"Finished: {len(built)} ok, {len(failed)} failed"
                    )
                else:
                    self.ctx.toast(
                        f"Built and installed {len(built)} package(s)"
                    )
                    self.summary.set_text(f"Finished: {len(built)} package(s)")
                self.ctx.reload_repo()
                GLib.idle_add(self.refresh)
                self.btn_all.set_sensitive(True)
                return False

            GLib.idle_add(apply)

        self.ctx.builder.build_local(dirs, done)
