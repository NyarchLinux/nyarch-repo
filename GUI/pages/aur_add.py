import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from services.aur import vercmp


def esc(text):
    return GLib.markup_escape_text(str(text or ""))


class AurAddPage(Gtk.Box):
    def __init__(self, ctx):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        self.ctx = ctx
        self.pkg = None
        self._working = False

        intro = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
        intro.set_markup(
            "Type an <b>AUR package name</b> to add it to the repository (or "
            "rebuild it when a newer version exists). Its AUR dependencies are "
            "resolved automatically and missing ones are built too."
        )
        self.append(intro)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry = Gtk.Entry(
            hexpand=True,
            placeholder_text="e.g. yay-bin",
        )
        self.entry.connect("activate", lambda *_: self.lookup())
        bar.append(self.entry)
        self.btn_lookup = Gtk.Button(label="Look up")
        self.btn_lookup.add_css_class("suggested-action")
        self.btn_lookup.connect("clicked", lambda *_: self.lookup())
        bar.append(self.btn_lookup)
        self.btn_add = Gtk.Button(label="Add / Update")
        self.btn_add.add_css_class("success")
        self.btn_add.set_sensitive(False)
        self.btn_add.connect("clicked", lambda *_: self.start_add())
        bar.append(self.btn_add)
        self.append(bar)

        self.card_frame = Gtk.Frame(margin_top=4)
        self.card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=12,
            margin_bottom=12,
            margin_start=14,
            margin_end=14,
        )
        self.card_frame.set_child(self.card)
        self.card_frame.set_visible(False)
        self.append(self.card_frame)

        self.status = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
        self.status.add_css_class("dim-label")
        self.append(self.status)

    def _guard_busy(self):
        if self.ctx.runner.busy or self._working:
            self.ctx.toast("Another operation is already running")
            return True
        return False

    def lookup(self):
        if self._guard_busy():
            return
        name = self.entry.get_text().strip()
        if not name:
            return
        self.btn_lookup.set_sensitive(False)
        self.btn_add.set_sensitive(False)
        self.card_frame.set_visible(False)
        self.status.set_text(f"Looking up '{name}' on the AUR…")

        def work():
            try:
                rec = self.ctx.aur.info_one(name)
                err = None
            except Exception as e:
                rec, err = None, str(e)
            GLib.idle_add(self._lookup_done, rec, err)

        threading.Thread(target=work, daemon=True).start()

    def _lookup_done(self, rec, err):
        self.btn_lookup.set_sensitive(True)
        if err is not None:
            self.status.set_text(f"AUR lookup failed: {err}")
            return False
        if rec is None:
            self.status.set_text("Package not found in the AUR.")
            return False
        self.pkg = rec
        while self.card.get_first_child():
            self.card.remove(self.card.get_first_child())

        title = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
        title.set_markup(
            f"<span size='x-large' weight='bold'>{esc(rec['Name'])}</span> "
            f"<span size='large' alpha='60%'>{esc(rec.get('Version', '?'))}</span>"
        )
        self.card.append(title)
        desc = rec.get("Description") or ""
        if desc:
            d = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
            d.set_text(desc)
            self.card.append(d)

        db = self.ctx.repo_db
        local = db.local_version(rec["Name"])
        if local is None:
            note = "Not in repository yet — it will be built and added."
        elif vercmp(rec.get("Version", ""), local) > 0:
            note = f"Update available: {local} → {rec['Version']}"
        else:
            note = f"Already up to date ({local}). Rebuilding will refresh it."
        n = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
        n.set_text(note)
        n.add_css_class("dim-label")
        self.card.append(n)

        meta = []
        if rec.get("Maintainer"):
            meta.append(("Maintainer", rec["Maintainer"]))
        votes = rec.get("NumVotes")
        if votes is not None:
            meta.append(("Votes", str(votes)))
        pop = rec.get("Popularity")
        if pop is not None:
            meta.append(("Popularity", f"{pop:.2f}"))
        ood = rec.get("OutOfDate")
        grid = Gtk.Grid(column_spacing=18, row_spacing=4)
        rows = meta + [("State", "⚠ OUT OF DATE" if ood else "Up to date upstream")]
        for i, (k, v) in enumerate(rows):
            kl = Gtk.Label(halign=Gtk.Align.START, xalign=0)
            kl.set_markup(f"<b>{k}</b>")
            vl = Gtk.Label(halign=Gtk.Align.START, xalign=0)
            vl.set_text(v)
            grid.attach(kl, 0, i, 1, 1)
            grid.attach(vl, 1, i, 1, 1)
        self.card.append(grid)

        self.card_frame.set_visible(True)
        self.btn_add.set_sensitive(True)
        self.status.set_text("")
        return False

    def start_add(self):
        if self._guard_busy() or self.pkg is None:
            return
        name = self.pkg["Name"]
        self.btn_add.set_sensitive(False)
        self.btn_lookup.set_sensitive(False)
        self.status.set_text(f"Resolving dependency closure of '{name}'…")

        def work():
            try:
                visited, missing = self.ctx.aur.resolve_closure([name])
                err = None
            except Exception as e:
                visited, missing, err = {}, [], str(e)
            GLib.idle_add(self._resolve_done, visited, missing, err)

        threading.Thread(target=work, daemon=True).start()

    def _resolve_done(self, visited, missing, err):
        if err is not None:
            self.status.set_text(f"Dependency resolution failed: {err}")
            self.btn_add.set_sensitive(True)
            self.btn_lookup.set_sensitive(True)
            return False
        target = self.pkg["Name"]
        db = self.ctx.repo_db
        known = db.all_known_names() | set(
            self.ctx.builder.read_list(self.ctx.aur_pkgs_full_file)
        )

        new_pkgs = []
        rebuild = []
        skipped = []
        for pname in sorted(visited.keys()):
            if pname == target:
                continue
            local = db.local_version(pname)
            aur_v = visited[pname].get("Version", "")
            if pname not in known or local is None:
                new_pkgs.append(pname)
            elif vercmp(aur_v, local) > 0:
                rebuild.append(pname)
            else:
                skipped.append(pname)

        local_target = db.local_version(target)
        if local_target is None:
            action_verb = "New package to build"
        elif vercmp(self.pkg.get("Version", ""), local_target) > 0:
            action_verb = (
                f"Update {target}: {local_target} → {self.pkg.get('Version')}"
            )
        else:
            action_verb = f"Rebuild current {target} ({local_target})"

        lines = [action_verb]
        if new_pkgs:
            lines.append("")
            lines.append(f"Missing AUR dependencies to build ({len(new_pkgs)}):")
            lines.extend("  • " + p for p in new_pkgs[:40])
            if len(new_pkgs) > 40:
                lines.append(f"  … and {len(new_pkgs) - 40} more")
        if rebuild:
            lines.append("")
            lines.append(f"Outdated dependencies to update ({len(rebuild)}):")
            lines.extend("  • " + p for p in rebuild[:20])
            if len(rebuild) > 20:
                lines.append(f"  … and {len(rebuild) - 20} more")
        if skipped:
            lines.append("")
            lines.append(f"Already current (skipped): {len(skipped)}")
        repo_only = [m for m in missing if m != target]
        if repo_only:
            lines.append("")
            lines.append(
                "Not in AUR — assumed from official repos (skipped): "
                + ", ".join(repo_only[:15])
            )

        build_list = [target] + new_pkgs + rebuild
        confirm_label = (
            "Update & Build" if local_target is not None else "Add & Build"
        )
        dlg = Adw.MessageDialog.new(
            self.ctx.win, f"Build {target}?", "\n".join(lines)
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", confirm_label)
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect(
            "response",
            lambda _d, resp: self._confirmed(build_list) if resp == "ok" else self._reset(),
        )
        dlg.present()
        return False

    def _reset(self):
        self.btn_add.set_sensitive(self.pkg is not None)
        self.btn_lookup.set_sensitive(True)
        self.status.set_text("")

    def _confirmed(self, build_list):
        target = self.pkg["Name"]
        try:
            self.ctx.builder.append_unique(self.ctx.aur_pkgs_file, [target])
            self.ctx.builder.merge_into_full(build_list)
        except OSError as e:
            self.ctx.toast(f"Could not update AUR package lists: {e}")
            self._reset()
            return False
        self._working = True
        uniq = list(dict.fromkeys(build_list))
        self.status.set_text(
            f"Building {len(uniq)} package(s): {', '.join(uniq[:8])}"
            + ("…" if len(uniq) > 8 else "")
        )

        def done(summary):
            self._working = False
            built = summary["built"]
            failed = summary["failed"]

            def apply():
                if failed:
                    self.ctx.toast(
                        "Finished with failures: " + ", ".join(failed[:5])
                    )
                    self.status.set_text("Build finished with errors — see terminal below.")
                else:
                    self.ctx.toast(f"Successfully built {len(built)} package(s)")
                    self.status.set_text("Done! Repository database updated.")
                self.ctx.reload_repo()
                self.btn_lookup.set_sensitive(True)
                return False

            GLib.idle_add(apply)

        self.ctx.builder.build_aur_packages(uniq, done)

    def refresh(self):
        pass
