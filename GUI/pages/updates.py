import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, GObject

from services.aur import vercmp


class UpdateRow(GObject.Object):
    STATUS_LABEL = {
        "update": "UPDATE",
        "current": "up to date",
        "notbuilt": "NOT BUILT",
        "error": "fetch error",
    }

    STATUS_CLASS = {
        "update": ["pill", "pill-orange"],
        "current": ["pill", "pill-green"],
        "notbuilt": ["pill", "pill-red"],
        "error": ["pill", "pill-gray"],
    }

    def __init__(self, name, local, aur, status):
        super().__init__()
        self.name = name
        self.local = local or "—"
        self.aur = aur or "?"
        self.status = status


class UpdatesPage(Gtk.Box):
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
        self._checking = False

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_check = Gtk.Button(label="Check for updates")
        self.btn_check.add_css_class("suggested-action")
        self.btn_check.connect("clicked", lambda *_: self.check())
        bar.append(self.btn_check)
        self.btn_selected = Gtk.Button(
            label="Update selected", sensitive=False
        )
        self.btn_selected.connect("clicked", lambda *_: self.update_selected())
        bar.append(self.btn_selected)
        self.btn_edit = Gtk.Button(label="Edit PKGBUILD", sensitive=False)
        self.btn_edit.connect("clicked", lambda *_: self.edit_selected())
        bar.append(self.btn_edit)
        self.btn_all = Gtk.Button(label="Update all outdated", sensitive=False)
        self.btn_all.add_css_class("destructive-action")
        self.btn_all.connect("clicked", lambda *_: self.update_outdated())
        bar.append(self.btn_all)
        bar.append(Gtk.Box(hexpand=True))
        self.summary = Gtk.Label(valign=Gtk.Align.CENTER, wrap=True)
        self.summary.add_css_class("dim-label")
        bar.append(self.summary)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text("Reload repository data")
        refresh.connect("clicked", lambda *_: ctx.reload_repo())
        bar.append(refresh)
        self.append(bar)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.store = Gio.ListStore()
        self.sel_model = Gtk.MultiSelection(model=self.store)
        self.sel_model.connect("selection-changed", self._selection_changed)
        view = Gtk.ColumnView(model=self.sel_model)
        view.add_css_class("data-table")
        self._build_columns(view)
        scroll.set_child(view)
        self.append(scroll)

    def _build_columns(self, view):
        nf = Gtk.SignalListItemFactory()
        nf.connect("setup", self._setup_text)
        nf.connect("bind", self._bind_name)
        col = Gtk.ColumnViewColumn(title="Package", factory=nf, expand=True)
        col.set_resizable(True)
        view.append_column(col)

        lf = Gtk.SignalListItemFactory()
        lf.connect("setup", self._setup_text)
        lf.connect("bind", lambda _f, item: item.get_child().set_text(item.get_item().local))
        col = Gtk.ColumnViewColumn(title="Local", factory=lf)
        col.set_resizable(True)
        view.append_column(col)

        af = Gtk.SignalListItemFactory()
        af.connect("setup", self._setup_text)
        af.connect("bind", lambda _f, item: item.get_child().set_text(item.get_item().aur))
        col = Gtk.ColumnViewColumn(title="AUR", factory=af)
        col.set_resizable(True)
        view.append_column(col)

        sf = Gtk.SignalListItemFactory()
        sf.connect("setup", self._setup_pill)
        sf.connect("bind", self._bind_status)
        col = Gtk.ColumnViewColumn(title="Status", factory=sf)
        col.set_resizable(True)
        view.append_column(col)

    @staticmethod
    def _setup_text(_factory, item):
        lbl = Gtk.Label(halign=Gtk.Align.START, margin_top=6, margin_bottom=6,
                        margin_start=6, margin_end=6)
        lbl.set_ellipsize(3)
        item.set_child(lbl)

    @staticmethod
    def _bind_name(_factory, item):
        row = item.get_item()
        item.get_child().set_markup(
            f"<b>{GLib.markup_escape_text(row.name)}</b>"
        )

    @staticmethod
    def _setup_pill(_factory, item):
        lbl = Gtk.Label(margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        item.set_child(lbl)

    @staticmethod
    def _bind_status(_factory, item):
        row = item.get_item()
        lbl = item.get_child()
        lbl.set_text(UpdateRow.STATUS_LABEL[row.status])
        for cls in ("pill", "pill-green", "pill-orange", "pill-red", "pill-gray"):
            if lbl.has_css_class(cls):
                lbl.remove_css_class(cls)
        for cls in UpdateRow.STATUS_CLASS[row.status]:
            lbl.add_css_class(cls)

    def _guard(self):
        if self.ctx.runner.busy or self._checking:
            self.ctx.toast("Another operation is already running")
            return True
        return False

    def check(self):
        if self._guard():
            return
        tracked = list(dict.fromkeys(
            self.ctx.builder.read_list(self.ctx.aur_pkgs_file)
            + self.ctx.builder.read_list(self.ctx.aur_pkgs_full_file)
        ))
        pkgs = tracked
        if not pkgs:
            self.summary.set_text("No AUR packages listed (AURpkgs is empty).")
            return
        self._checking = True
        self.btn_check.set_sensitive(False)
        self.summary.set_text(f"Checking {len(pkgs)} packages against the AUR…")
        repo_db = self.ctx.repo_db

        def work():
            try:
                aur = self.ctx.aur.info(pkgs)
            except Exception as e:
                GLib.idle_add(self._done, [], 0, 0, str(e))
                return
            rows = []
            for p in pkgs:
                local = repo_db.local_version(p)
                rec = aur.get(p)
                aur_v = rec.get("Version") if rec else ""
                if rec is None:
                    rows.append(UpdateRow(p, local, "", "error"))
                elif local is None:
                    rows.append(UpdateRow(p, None, aur_v, "notbuilt"))
                elif vercmp(aur_v, local) > 0:
                    rows.append(UpdateRow(p, local, aur_v, "update"))
                else:
                    rows.append(UpdateRow(p, local, aur_v, "current"))
            updates = sum(1 for r in rows if r.status == "update")
            notbuilt = sum(1 for r in rows if r.status == "notbuilt")
            GLib.idle_add(self._done, rows, updates, notbuilt, None)

        threading.Thread(target=work, daemon=True).start()

    def _done(self, rows, updates, notbuilt, err):
        self._checking = False
        self.btn_check.set_sensitive(True)
        self.store.remove_all()
        order = {"update": 0, "notbuilt": 1, "error": 2, "current": 3}
        rows.sort(key=lambda r: (order[r.status], r.name))
        for r in rows:
            self.store.append(r)
        if err:
            self.summary.set_text(f"AUR query failed: {err}")
        else:
            parts = [f"{len(rows)} tracked"]
            if updates:
                parts.append(f"{updates} update(s) available")
            else:
                parts.append("all up to date")
            if notbuilt:
                parts.append(f"{notbuilt} not built")
            self.summary.set_text(", ".join(parts))
        self.btn_all.set_sensitive(bool(updates or notbuilt))
        self.btn_selected.set_sensitive(False)
        return False

    def _selected_names(self):
        sel = self.sel_model.get_selection()
        names = []
        for i in range(sel.get_size()):
            pos = sel.get_nth(i)
            row = self.sel_model.get_item(pos)
            names.append(row.name)
        return names

    def _selection_changed(self, *_):
        names = self._selected_names()
        self.btn_selected.set_sensitive(bool(names))
        self.btn_edit.set_sensitive(len(names) == 1)

    def edit_selected(self):
        if self._guard():
            return
        names = self._selected_names()
        if len(names) == 1:
            self.ctx.builder.prepare_aur_pkgbuild(names[0], self.ctx.open_pkgbuild)

    def update_selected(self):
        if self._guard():
            return
        names = [
            n for n in self._selected_names() if n
        ]
        if not names:
            self.ctx.toast("Select at least one package first")
            return
        self._confirm_build(names)

    def update_outdated(self):
        if self._guard():
            return
        outdated = []
        for i in range(self.store.get_n_items()):
            row = self.store.get_item(i)
            if row.status in ("update", "notbuilt"):
                outdated.append(row.name)
        if not outdated:
            self.ctx.toast("Nothing to update — run a check first")
            return
        self._confirm_build(outdated)

    def _confirm_build(self, names):
        shown = ", ".join(names[:12]) + ("…" if len(names) > 12 else "")
        dlg = Adw.MessageDialog.new(
            self.ctx.win,
            f"Build {len(names)} package(s)?",
            f"This will update and rebuild:\n{shown}\n"
            "Missing dependencies are built first. Edited PKGBUILDs are kept.\n"
            "New artifacts are signed and the database is refreshed.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Build")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _d, resp: self._build(names) if resp == "ok" else None)
        dlg.present()

    def _build(self, names):
        if self._guard():
            return
        uniq = list(dict.fromkeys(names))
        self.summary.set_text(f"Rebuilding {len(uniq)} package(s)…")
        self.btn_check.set_sensitive(False)

        def done(summary):
            failed = summary["failed"]
            built = summary["built"]

            def apply():
                if failed:
                    self.ctx.toast("Failures: " + ", ".join(failed[:5]))
                else:
                    self.ctx.toast(f"Built and updated {len(built)} package(s)")
                self.ctx.reload_repo()
                self.check()
                return False

            GLib.idle_add(apply)

        self.ctx.builder.build_aur_packages(uniq, done)

    def refresh(self):
        pass
