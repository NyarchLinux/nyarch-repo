import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gio, GLib, Adw, GObject


def esc(text):
    return GLib.markup_escape_text(str(text or ""))


def confirm_prune(ctx):
    if ctx.runner.busy:
        ctx.toast("Another operation is already running")
        return
    candidates = ctx.builder.get_prune_candidates()
    if not candidates:
        ctx.toast("No old versions to remove — repository is already pruned")
        return
    import os as _os

    freed = sum(
        _os.path.getsize(r["path"])
        for r in candidates
        if _os.path.exists(r["path"])
    )
    lines = []
    for r in candidates[:25]:
        keep = ctx.repo_db.latest_record(r["name"])
        keep_v = keep["split"][1] if keep and keep.get("split") else "?"
        lines.append(f"  • {r['name']} {r['split'][1]}  (keeping {keep_v})")
    if len(candidates) > 25:
        lines.append(f"  … and {len(candidates) - 25} more")
    mb = freed / 1024 / 1024
    dlg = Adw.MessageDialog.new(
        ctx.win,
        f"Remove {len(candidates)} old package version(s)?",
        "The newest version of every package is kept, everything older is "
        f"deleted ({mb:.1f} MiB freed) and the database is rebuilt.\n\n"
        + "\n".join(lines),
    )
    dlg.add_response("cancel", "Cancel")
    dlg.add_response("prune", "Delete")
    dlg.set_response_appearance("prune", Adw.ResponseAppearance.DESTRUCTIVE)
    dlg.connect(
        "response",
        lambda _d, resp: _do_prune(ctx) if resp == "prune" else None,
    )
    dlg.present()


def _do_prune(ctx):
    def done(summary):
        def apply():
            if summary["deleted"]:
                msg = f"Removed {summary['deleted']} old version(s)"
                if summary["freed"]:
                    msg += f", {summary['freed'] / 1024 / 1024:.1f} MiB freed"
                if not summary["db_ok"]:
                    msg += " — database rebuild FAILED"
                ctx.toast(msg)
            else:
                ctx.toast("Nothing removed — see terminal")
            ctx.reload_repo()
            return False

        GLib.idle_add(apply)

    ctx.builder.prune_old_versions(done)


class PackageRow(GObject.Object):
    def __init__(self, name, version, arch, desc):
        super().__init__()
        self.name = name
        self.version = version
        self.arch = arch
        self.desc = desc
        self.badges = []
        self.badge_tips = []
        self.signed_all = True
        self.entry = None


class PackagesPage(Gtk.Box):
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
        self._query = ""

        topbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.search = Gtk.SearchEntry(
            placeholder_text="Search packages…", hexpand=True
        )
        self.search.connect("search-changed", self._on_search_changed)
        topbar.append(self.search)
        prune_btn = Gtk.Button(icon_name="edit-delete-symbolic")
        prune_btn.set_tooltip_text("Remove all old package versions from the repository")
        prune_btn.connect("clicked", lambda *_: confirm_prune(ctx))
        topbar.append(prune_btn)
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Reload repository database")
        refresh_btn.connect("clicked", lambda *_: ctx.reload_repo())
        topbar.append(refresh_btn)
        self.append(topbar)

        self.paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL,
            position=540,
            resize_start_child=False,
            shrink_start_child=False,
            vexpand=True,
        )
        self.append(self.paned)

        left_scroll = Gtk.ScrolledWindow()
        left_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.store = Gio.ListStore()
        self.filter = Gtk.CustomFilter()
        self.filter.set_filter_func(self._match_row)
        self.filter_model = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.sel_model = Gtk.SingleSelection(model=self.filter_model)
        self.view = Gtk.ColumnView(model=self.sel_model)
        self.view.add_css_class("data-table")
        self._build_columns()
        self.sel_model.connect("selection-changed", lambda *_: self._show_detail())
        left_scroll.set_child(self.view)
        self.paned.set_start_child(left_scroll)

        right_scroll = Gtk.ScrolledWindow()
        right_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=800, tightening_threshold=600)
        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        clamp.set_child(self.detail_box)
        right_scroll.set_child(clamp)
        self.paned.set_end_child(right_scroll)

        self._placeholder()

    def _build_columns(self):
        name_factory = Gtk.SignalListItemFactory()
        name_factory.connect("setup", self._setup_text_cell)
        name_factory.connect("bind", self._bind_name_cell)
        col = Gtk.ColumnViewColumn(title="Package", factory=name_factory, expand=True)
        col.set_resizable(True)
        self.view.append_column(col)

        ver_factory = Gtk.SignalListItemFactory()
        ver_factory.connect("setup", self._setup_text_cell)
        ver_factory.connect("bind", self._bind_ver_cell)
        col = Gtk.ColumnViewColumn(title="Version", factory=ver_factory)
        col.set_resizable(True)
        self.view.append_column(col)

        arch_factory = Gtk.SignalListItemFactory()
        arch_factory.connect("setup", self._setup_text_cell)
        arch_factory.connect("bind", self._bind_arch_cell)
        self.view.append_column(Gtk.ColumnViewColumn(title="Arch", factory=arch_factory))

        badge_factory = Gtk.SignalListItemFactory()
        badge_factory.connect("setup", self._setup_badge_cell)
        badge_factory.connect("bind", self._bind_badge_cell)
        col = Gtk.ColumnViewColumn(title="Status", factory=badge_factory)
        col.set_resizable(True)
        self.view.append_column(col)

    @staticmethod
    def _setup_text_cell(_factory, item):
        label = Gtk.Label(halign=Gtk.Align.START, margin_top=6, margin_bottom=6,
                          margin_start=6, margin_end=6)
        label.set_ellipsize(3)
        item.set_child(label)

    @staticmethod
    def _bind_name_cell(_factory, item):
        row = item.get_item()
        label = item.get_child()
        label.set_markup(f"<b>{esc(row.name)}</b>")
        if row.desc:
            label.set_tooltip_text(row.desc)

    @staticmethod
    def _bind_ver_cell(_factory, item):
        item.get_child().set_text(item.get_item().version)

    @staticmethod
    def _bind_arch_cell(_factory, item):
        item.get_child().set_text(item.get_item().arch)

    @staticmethod
    def _setup_badge_cell(_factory, item):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4,
                      margin_top=4, margin_bottom=4, margin_start=6, margin_end=6)
        item.set_child(box)

    @staticmethod
    def _bind_badge_cell(_factory, item):
        box = item.get_child()
        while box.get_first_child():
            box.remove(box.get_first_child())
        row = item.get_item()
        icons = {
            "unsigned": ("dialog-warning-symbolic", "Some files are unsigned"),
            "stale": ("dialog-question-symbolic",
                      "Listed in db but package file missing on disk"),
            "dupes": ("view-more-symbolic", "Multiple versions on disk"),
            "orphan": ("user-trash-symbolic",
                       "File present but not in repository db"),
        }
        for b in row.badges:
            icon, tip = icons.get(b, ("dialog-information-symbolic", b))
            img = Gtk.Image.new_from_icon_name(icon)
            img.set_tooltip_text(tip)
            if b in ("unsigned", "stale"):
                img.add_css_class("warning")
            box.append(img)
        if not row.badges and row.signed_all:
            img = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            img.add_css_class("success")
            img.set_tooltip_text("Signed and consistent")
            box.append(img)

    def _match_row(self, row):
        q = getattr(self, "_query", "")
        if not q:
            return True
        return (
            q in row.name.lower()
            or q in (row.desc or "").lower()
            or q in row.version.lower()
        )

    def _on_search_changed(self, entry):
        self._query = entry.get_text().strip().lower()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def refresh(self):
        db = self.ctx.repo_db
        self.store.remove_all()
        for e in sorted(db.entries, key=lambda x: x.name.lower()):
            recs = db.records_named(e.name)
            latest = db.latest_record(e.name) or {}
            row = PackageRow(e.name, e.version, e.arch or "", e.desc or "")
            row.entry = e
            row.signed_all = all(r["sig"] for r in recs) if recs else False
            dupes = db.duplicates.get(e.name, [])
            if not row.signed_all:
                row.badges.append("unsigned")
            if e.filename and e.filename not in db.files:
                row.badges.append("stale")
            if len(dupes) > 1:
                row.badges.append("dupes")
            self.store.append(row)
        for fn, rec in sorted(db.files.items()):
            if rec["entry"] is None:
                sp = rec.get("split")
                row = PackageRow(
                    rec["name"] or fn,
                    sp[1] if sp else "?",
                    sp[2] if sp else "",
                    f"Orphan file (not in db): {fn}",
                )
                row.badges.append("orphan")
                row.signed_all = rec["sig"]
                self.store.append(row)
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self._placeholder()

    def _placeholder(self):
        while self.detail_box.get_first_child():
            self.detail_box.remove(self.detail_box.get_first_child())
        lbl = Gtk.Label(wrap=True, xalign=0)
        lbl.set_markup(
            "<span alpha='55%'>Select a package to inspect its metadata, "
            "dependencies and on-disk status.</span>"
        )
        self.detail_box.append(lbl)

    def focus_name(self, name):
        self.search.set_text(name)

        def select_first():
            if self.filter_model.get_n_items() > 0:
                self.sel_model.set_selected(0)
            return False

        GLib.idle_add(select_first)

    def selected_row(self):
        pos = self.sel_model.get_selected()
        if pos == Gtk.INVALID_LIST_POSITION:
            return None
        return self.sel_model.get_item(pos)

    def _show_detail(self):
        row = self.selected_row()
        while self.detail_box.get_first_child():
            self.detail_box.remove(self.detail_box.get_first_child())
        if row is None:
            self._placeholder()
            return
        e = row.entry
        db = self.ctx.repo_db
        if e is None:
            self._detail_title(row.name, row.version, row.arch, row.desc)
            return
        self._detail_title(e.name, e.version, e.arch or "", e.desc)
        meta = []
        if e.base and e.base != e.name:
            meta.append(("Package base", e.base))
        if e.url:
            meta.append(("URL", e.url))
        if e.license:
            meta.append(("License", ", ".join(e.license)))
        if e.packager:
            meta.append(("Packager", e.packager))
        if e.builddate:
            import datetime

            try:
                ts = int(e.builddate)
                meta.append(("Built", datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")))
            except (ValueError, OverflowError):
                pass
        if e.csize:
            meta.append(("Download size", f"{e.csize / 1024 / 1024:.1f} MiB"))
        if e.isize:
            meta.append(("Installed size", f"{e.isize / 1024 / 1024:.1f} MiB"))
        if e.filename:
            sig_ok = db.files.get(e.filename, {}).get("sig", False)
            meta.append(("File", e.filename + ("\n✓ signed" if sig_ok else "\n⚠ unsigned")))
        self._detail_grid(meta)
        self._dep_chips("Depends", e.depends)
        self._dep_chips("Make depends", e.makedepends)
        self._dep_chips("Check depends", e.checkdepends)
        self._dep_chips("Opt depends", e.optdepends)
        self._dep_chips("Provides", e.provides)
        self._dep_chips("Conflicts", e.conflicts)
        self._dep_chips("Replaces", e.replaces)
        recs = db.records_named(e.name)
        if len(recs) > 1:
            lines = [
                f"{r['file']}" + ("" if r["sig"] else "  (unsigned)")
                for r in recs
            ]
            self._detail_section(
                f"Versions on disk ({len(recs)}) — consider pruning old ones",
                lines,
            )

    def _detail_title(self, name, version, arch, desc):
        title = Gtk.Label(halign=Gtk.Align.START, wrap=True, xalign=0)
        title.set_markup(
            f"<span size='x-large' weight='bold'>{esc(name)}</span> "
            f"<span size='large' alpha='60%'>{esc(version)}</span> "
            f"<span alpha='50%'>[{esc(arch)}]</span>"
        )
        title.add_css_class("title-2")
        self.detail_box.append(title)
        if desc:
            d = Gtk.Label(halign=Gtk.Align.START, wrap=True, xalign=0)
            d.set_text(desc)
            d.add_css_class("dim-label")
            self.detail_box.append(d)

    def _detail_grid(self, pairs):
        pairs = [(k, v) for k, v in pairs if v]
        if not pairs:
            return
        grid = Gtk.Grid(column_spacing=16, row_spacing=6)
        for i, (k, v) in enumerate(pairs):
            kl = Gtk.Label(halign=Gtk.Align.START, xalign=0)
            kl.set_markup(f"<b>{k}</b>")
            vl = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
            vl.set_text(v)
            grid.attach(kl, 0, i, 1, 1)
            grid.attach(vl, 1, i, 1, 1)
        inner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_top=10, margin_bottom=10, margin_start=10, margin_end=10,
        )
        inner.append(grid)
        frame = Gtk.Frame()
        frame.set_child(inner)
        self.detail_box.append(frame)

    def _detail_section(self, heading, lines):
        lines = [l for l in lines if l]
        if not lines:
            return
        h = Gtk.Label(halign=Gtk.Align.START, xalign=0)
        h.set_markup(f"<b>{heading}</b>")
        h.add_css_class("heading")
        self.detail_box.append(h)
        for l in lines:
            lbl = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
            lbl.set_text(l)
            lbl.add_css_class("monospace")
            lbl.set_margin_start(8)
            self.detail_box.append(lbl)

    def _dep_chips(self, heading, deps):
        deps = list(deps or [])
        if not deps:
            return
        h = Gtk.Label(halign=Gtk.Align.START, xalign=0)
        h.set_markup(f"<b>{heading} ({len(deps)})</b>")
        h.add_css_class("heading")
        self.detail_box.append(h)
        flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            activate_on_single_click=True,
            homogeneous=False,
            max_children_per_line=8,
        )
        flow.connect("child-activated", self._on_chip_activated)
        for dep in deps[:200]:
            base = dep.split("<")[0].split(">")[0].split("=")[0].strip()
            chip_label = Gtk.Label(label=dep)
            chip_label.set_ellipsize(3)
            chip_label.set_max_width_chars(28)
            chip = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                margin_top=2, margin_bottom=2, margin_start=4, margin_end=4,
            )
            chip.add_css_class("chip")
            chip.append(chip_label)
            chip._dep_target = base
            flow.append(chip)
        self.detail_box.append(flow)

    def _on_chip_activated(self, _flow, child):
        target = child.get_child()._dep_target
        self.ctx.goto_package(target)
