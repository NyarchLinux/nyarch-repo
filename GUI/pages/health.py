import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

from services.integrity import full_check


def esc(text):
    return GLib.markup_escape_text(str(text or ""))


class HealthPage(Gtk.Box):
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
        self._results = []

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_run = Gtk.Button(label="Run full integrity check")
        self.btn_run.add_css_class("suggested-action")
        self.btn_run.connect("clicked", lambda *_: self.run_check())
        bar.append(self.btn_run)
        self.btn_sign = Gtk.Button(label="Sign unsigned packages")
        self.btn_sign.set_tooltip_text("gpg --detach-sign every unsigned .pkg.tar.zst")
        self.btn_sign.connect("clicked", lambda *_: self.sign_unsigned())
        bar.append(self.btn_sign)
        self.btn_db = Gtk.Button(label="Rebuild database")
        self.btn_db.set_tooltip_text("Re-run x86_64/update_repo.sh (repo-add)")
        self.btn_db.connect("clicked", lambda *_: self.rebuild_db())
        bar.append(self.btn_db)
        prune_btn = Gtk.Button(label="Remove old versions")
        prune_btn.set_tooltip_text(
            "Keep the newest version of each package, delete older ones, rebuild db"
        )
        from pages.packages import confirm_prune

        prune_btn.connect("clicked", lambda *_: confirm_prune(self.ctx))
        bar.append(prune_btn)
        bar.append(Gtk.Box(hexpand=True))
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text("Reload repository data")
        refresh.connect("clicked", lambda *_: ctx.reload_repo())
        bar.append(refresh)
        self.append(bar)

        self.banner_frame = Gtk.Frame()
        banner_inner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            margin_top=10,
            margin_bottom=10,
            margin_start=14,
            margin_end=14,
        )
        self.banner_title = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
        self.banner_title.set_markup(
            "<span size='x-large' weight='bold'>Integrity unknown</span>"
        )
        self.banner_sub = Gtk.Label(
            halign=Gtk.Align.START, xalign=0, wrap=True
        )
        self.banner_sub.add_css_class("dim-label")
        self.banner_sub.set_text(
            "Run a check to verify signatures, archives and .MTREE checksums."
        )
        banner_inner.append(self.banner_title)
        banner_inner.append(self.banner_sub)
        self.banner_frame.set_child(banner_inner)
        self.append(self.banner_frame)

        self.progress = Gtk.Label(halign=Gtk.Align.START, xalign=0)
        self.progress.add_css_class("dim-label")
        self.progress.set_visible(False)
        self.append(self.progress)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.issue_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.issue_list.add_css_class("boxed-list")
        scroll.set_child(self.issue_list)
        self.append(scroll)

    def _set_banner(self, ok_count, warn_count, fail_count, keys_used):
        total = len(self._results)
        if fail_count:
            cls = "banner-fail"
            title = f"Repository is unhealthy — {fail_count} failing package(s)"
        elif warn_count:
            cls = "banner-warn"
            title = f"Repository mostly healthy — {warn_count} warning(s)"
        elif total == 0:
            cls = "banner-neutral"
            title = "Nothing to check"
        else:
            cls = "banner-ok"
            title = "Repository is healthy ✓"
        for c in ("banner-ok", "banner-warn", "banner-fail", "banner-neutral"):
            self.banner_frame.remove_css_class(c)
        self.banner_frame.add_css_class(cls)
        self.banner_title.set_markup(
            f"<span size='x-large' weight='bold'>{title}</span>"
        )
        key_bits = (
            f"{len(keys_used)} signing key(s) in use"
            if keys_used
            else "no signatures found"
        )
        self.banner_sub.set_text(
            f"{total} package(s): {ok_count} clean, {warn_count} warning(s), "
            f"{fail_count} failure(s) — {key_bits}"
        )

    def run_check(self):
        if self.ctx.runner.busy or self._checking:
            self.ctx.toast("Another operation is already running")
            return
        db = self.ctx.repo_db
        if not db.files:
            self.ctx.toast("No packages found in x86_64/")
            return
        self._checking = True
        self.btn_run.set_sensitive(False)
        self.progress.set_visible(True)
        self.progress.set_text("Starting integrity scan…")

        def prog(i, total, name):
            GLib.idle_add(
                self.progress.set_text,
                f"Checking {name} ({i + 1}/{total})…" if name else "Finishing…",
            )

        def work():
            try:
                results = full_check(db, progress=prog)
                err = None
            except Exception as e:
                results, err = [], str(e)
            GLib.idle_add(self._done, results, err)

        threading.Thread(target=work, daemon=True).start()

    def _done(self, results, err):
        self._checking = False
        self.btn_run.set_sensitive(True)
        self.progress.set_visible(False)
        if err is not None:
            self.progress.set_visible(True)
            self.progress.set_text(f"Check failed: {err}")
            return False
        self._results = results
        while self.issue_list.get_first_child():
            self.issue_list.remove(self.issue_list.get_first_child())

        ok = warn = fail = 0
        keys = set()
        severity_icon = {
            "ok": ("emblem-ok-symbolic", "success"),
            "warn": ("dialog-warning-symbolic", "warning"),
            "fail": ("dialog-error-symbolic", "error"),
        }
        for r in results:
            gv = r.get("gpg") or {}
            if gv.get("ok"):
                keys.add(gv.get("fingerprint") or gv.get("key_id") or "?")
            sev = r["severity"]
            if sev == "ok":
                ok += 1
            elif sev == "warn":
                warn += 1
            else:
                fail += 1
            row = Gtk.ListBoxRow()
            box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=10,
                margin_top=8,
                margin_bottom=8,
                margin_start=12,
                margin_end=12,
            )
            icon_name, css = severity_icon[sev]
            img = Gtk.Image.new_from_icon_name(icon_name)
            img.add_css_class(css)
            box.append(img)
            text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            title = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
            disp = r["name"] or r["file"]
            title.set_markup(
                f"<b>{esc(disp)}</b>  <span alpha='55%'>{esc(r['file'])}</span>"
            )
            text_box.append(title)
            sub_bits = []
            if gv.get("present"):
                if gv.get("ok"):
                    kid = gv.get("fingerprint") or gv.get("key_id") or ""
                    uid = gv.get("key_uid") or ""
                    sub_bits.append(
                        f"signed by {kid[:16]}… ({uid})" if kid else "signed"
                    )
                else:
                    sub_bits.append(gv.get("detail", "signature invalid"))
            else:
                sub_bits.append("unsigned")
            if r.get("mtree_checked"):
                sub_bits.append(f".MTREE verified {r['mtree_checked']} file(s)")
            for m in r["messages"]:
                if m.startswith(".MTREE") and r.get("mtree_checked"):
                    continue
                sub_bits.append(m)
            sub = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
            sub.add_css_class("dim-label")
            joined = "; ".join(dict.fromkeys(sub_bits))
            sub.set_markup(f"<small>{esc(joined)}</small>")
            text_box.append(sub)
            box.append(text_box)
            row.set_child(box)
            self.issue_list.append(row)

        structural = []
        if self.ctx.repo_db.orphans:
            structural.append(
                (f"{len(self.ctx.repo_db.orphans)} orphan file(s)",
                 ", ".join(self.ctx.repo_db.orphans[:6]))
            )
        if self.ctx.repo_db.stale:
            structural.append(
                (f"{len(self.ctx.repo_db.stale)} stale db entr(y/ies)",
                 ", ".join(self.ctx.repo_db.stale[:6]))
            )
        for heading, detail in structural:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=10,
                margin_top=8,
                margin_bottom=8,
                margin_start=12,
                margin_end=12,
            )
            img = Gtk.Image.new_from_icon_name("folder-open-symbolic")
            img.add_css_class("warning")
            box.append(img)
            tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            t = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
            t.set_markup(f"<b>{heading}</b>")
            s = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
            s.add_css_class("dim-label")
            s.set_text(detail)
            tb.append(t)
            tb.append(s)
            box.append(tb)
            row.set_child(box)
            self.issue_list.append(row)

        self._set_banner(ok, warn, fail, keys)
        return False

    def sign_unsigned(self):
        if self.ctx.runner.busy or self._checking:
            self.ctx.toast("Another operation is already running")
            return
        targets = self.ctx.repo_db.unsigned_files()

        def done(signed, failures):
            def apply():
                if signed:
                    self.ctx.toast(f"Signed {signed} package(s)")
                elif not failures:
                    self.ctx.toast("All packages are already signed")
                if failures:
                    self.ctx.toast("Failed to sign: " + ", ".join(failures[:3]))
                self.ctx.reload_repo()
                return False

            GLib.idle_add(apply)

        self.ctx.builder.sign_unsigned(done)

    def rebuild_db(self):
        if self.ctx.runner.busy or self._checking:
            self.ctx.toast("Another operation is already running")
            return

        def done(ok):
            def apply():
                if ok:
                    self.ctx.toast("Repository database rebuilt")
                else:
                    self.ctx.toast("Database rebuild failed — see terminal")
                self.ctx.reload_repo()
                return False

            GLib.idle_add(apply)

        self.ctx.builder.update_db(done)

    def refresh(self):
        pass
