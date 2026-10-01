import datetime
import os
import re
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

from services import keyring as ksvc


def esc(text):
    return GLib.markup_escape_text(str(text or ""))


class KeyringPage(Gtk.Box):
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
        self.base_dir = os.path.join(ctx.pkgbuilds_dir, "nyarch-keyring")
        self.clone_dir = os.path.join(self.base_dir, "NyarchKeyring")

        intro = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
        intro.set_markup(
            "Edits the Nyarch signing keyring packaged as <b>nyarch-keyring</b>. "
            "It fetches the keyring source from GitHub into "
            "<tt>PkgBuilds/nyarch-keyring/NyarchKeyring</tt>, lets you add or "
            "remove trusted keys, then bump the version and rebuild the package. "
            "Remember to <b>commit and push</b> the clone so users get your changes."
        )
        intro.add_css_class("dim-label")
        self.append(intro)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_fetch = Gtk.Button(label="Fetch keyring repo")
        self.btn_fetch.add_css_class("suggested-action")
        self.btn_fetch.set_tooltip_text(
            "Clone or update PkgBuilds/nyarch-keyring/NyarchKeyring from GitHub"
        )
        self.btn_fetch.connect("clicked", lambda *_: self.fetch())
        bar.append(self.btn_fetch)
        self.btn_refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        self.btn_refresh.set_tooltip_text("Reload key list")
        self.btn_refresh.connect("clicked", lambda *_: self.refresh_list())
        bar.append(self.btn_refresh)

        self.import_entry = Gtk.Entry(
            placeholder_text="Fingerprint / key ID…", hexpand=True
        )
        self.import_entry.connect("activate", lambda *_: self.recv_from_keyserver())
        bar.append(self.import_entry)
        self.btn_recv = Gtk.Button(label="From keyserver")
        self.btn_recv.set_tooltip_text(
            f"Fetch the given key ID from {ksvc.DEFAULT_KEYSERVER}"
        )
        self.btn_recv.connect("clicked", lambda *_: self.recv_from_keyserver())
        bar.append(self.btn_recv)

        btn_paste = Gtk.Button(label="Import paste…")
        btn_paste.connect("clicked", lambda *_: self.paste_import_dialog())
        bar.append(btn_paste)

        bar.append(Gtk.Box(hexpand=True))
        self.btn_bump = Gtk.Button(label="Bump pkgver")
        self.btn_bump.set_tooltip_text(
            "Increment pkgver in nyarch-keyring/PKGBUILD so a new package is built"
        )
        self.btn_bump.connect("clicked", lambda *_: self.bump_pkgver())
        bar.append(self.btn_bump)
        self.btn_build = Gtk.Button(label="Rebuild package")
        self.btn_build.add_css_class("suggested-action")
        self.btn_build.set_tooltip_text(
            "Build nyarch-keyring with makepkg and add it to the repository"
        )
        self.btn_build.connect("clicked", lambda *_: self.rebuild_package())
        bar.append(self.btn_build)
        self.append(bar)

        self.status = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
        self.status.add_css_class("dim-label")
        self.append(self.status)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        clamp_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        clamp = Adw.Clamp(maximum_size=900, tightening_threshold=700)
        clamp.set_child(self.listbox)
        clamp_holder.append(clamp)
        scroll.set_child(clamp_holder)
        self.append(scroll)

        GLib.idle_add(self.refresh_list)

    def _guard(self):
        if self.ctx.runner.busy:
            self.ctx.toast("Another operation is already running")
            return True
        return False

    def set_status(self, text):
        self.status.set_text(text)

    def keyring_file(self):
        return ksvc.find_keyring_file(self.clone_dir)

    def fetch(self):
        if self._guard():
            return
        self.btn_fetch.set_sensitive(False)

        def done(ok):
            def apply():
                self.btn_fetch.set_sensitive(True)
                if ok:
                    kf = self.keyring_file()
                    if kf:
                        self.ctx.toast("Keyring repo ready — keys loaded")
                    else:
                        self.ctx.toast(
                            "Repo cloned but no .gpg keyring file found inside"
                        )
                else:
                    self.ctx.toast("Fetch failed — see terminal")
                self.refresh_list()
                return False

            GLib.idle_add(apply)

        self.ctx.runner.run(
            self.base_dir,
            ["bash", "-c", ksvc.ensure_clone_script(self.base_dir)],
            "Fetching NyarchKeyring repository",
            done,
        )

    def refresh_list(self):
        if os.path.isdir(self.clone_dir) and not self.ctx.runner.busy:
            threading.Thread(target=self._load_keys, daemon=True).start()
        elif not os.path.isdir(self.clone_dir):
            self._render([], "No local clone yet — click “Fetch keyring repo”.")
        return False

    def _load_keys(self):
        kf = self.keyring_file()
        try:
            if kf is None:
                raise RuntimeError(
                    "cloned but no *.gpg keyring file found in NyarchKeyring/"
                )
            keys = ksvc.list_keys(kf)
            err = None
        except Exception as e:
            keys, err, kf = [], str(e), None
        GLib.idle_add(self._render, keys, err, kf)

    def _render(self, keys, err=None, kf=None):
        while self.listbox.get_first_child():
            self.listbox.remove(self.listbox.get_first_child())
        if err:
            row = Gtk.ListBoxRow()
            lbl = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True,
                            margin_top=10, margin_bottom=10,
                            margin_start=12, margin_end=12)
            lbl.set_markup(f"<span foreground='#ff7b74'>{esc(err)}</span>")
            row.set_child(lbl)
            self.listbox.append(row)
            self.set_status("")
            return False
        if not keys:
            row = Gtk.ListBoxRow()
            lbl = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True,
                            margin_top=10, margin_bottom=10,
                            margin_start=12, margin_end=12)
            lbl.set_markup(
                "<span alpha='55%'>No keys in the keyring yet. Fetch the repo, "
                "then import keys by fingerprint or paste.</span>"
            )
            row.set_child(lbl)
            self.listbox.append(row)
            self.set_status("")
            return False

        today = datetime.date.today()
        for k in keys:
            uid = k["uids"][0] if k["uids"] else k["keyid"]
            fpr = k["fpr"] or k["keyid"]
            sub = f"{fpr}"
            exp = ""
            if k["expires"]:
                try:
                    d = datetime.date.fromtimestamp(int(k["expires"]))
                    left = (d - today).days
                    exp = f" · expires {d}" + (
                        f" ({left}d)" if 0 <= left <= 60 else ""
                    )
                    if left < 0:
                        exp = f" · EXPIRED {d}"
                except (ValueError, OverflowError):
                    pass
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          margin_top=8, margin_bottom=8,
                          margin_start=12, margin_end=12)
            img = Gtk.Image.new_from_icon_name("dialog-password-symbolic")
            box.append(img)
            tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            title = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
            title.set_markup(f"<b>{esc(uid)}</b>")
            subtitle = Gtk.Label(halign=Gtk.Align.START, xalign=0, wrap=True)
            subtitle.add_css_class("dim-label")
            subtitle.set_markup(
                f"<small><tt>{esc(fpr)}</tt>{esc(exp)}</small>"
            )
            tb.append(title)
            tb.append(subtitle)
            box.append(tb)
            box.append(Gtk.Box(hexpand=True))

            btn_exp = Gtk.Button(icon_name="share-symbolic")
            btn_exp.add_css_class("flat")
            btn_exp.set_tooltip_text("Export armored public key")
            btn_exp.connect("clicked", lambda *_b, key=k: self.export_dialog(key))
            box.append(btn_exp)

            btn_del = Gtk.Button(icon_name="user-trash-symbolic")
            btn_del.add_css_class("flat")
            btn_del.add_css_class("error")
            btn_del.set_tooltip_text("Remove this key from the keyring")
            btn_del.connect("clicked", lambda *_b, key=k: self.delete_key(key))
            box.append(btn_del)

            row.set_child(box)
            self.listbox.append(row)
        self.set_status(
            f"{len(keys)} key(s) in {os.path.relpath(kf, self.ctx.repo_root)}"
        )
        return False

    def recv_from_keyserver(self):
        if self._guard():
            return
        kf = self.keyring_file()
        if kf is None:
            self.ctx.toast("Fetch the keyring repo first")
            return
        raw = self.import_entry.get_text().strip().replace(",", " ")
        ids = [t for t in raw.split() if t]
        if not ids:
            self.ctx.toast("Type a fingerprint or key ID first")
            return
        self.btn_recv.set_sensitive(False)
        self.set_status(f"Fetching {len(ids)} key(s) from the keyserver…")

        def work():
            try:
                ksvc.recv_keys(kf, ids)
                err = None
            except Exception as e:
                err = str(e)
            GLib.idle_add(self._after_modify, err, "imported from keyserver")

        threading.Thread(target=work, daemon=True).start()

    def paste_import_dialog(self):
        if self._guard():
            return
        kf = self.keyring_file()
        if kf is None:
            self.ctx.toast("Fetch the keyring repo first")
            return
        dlg = Adw.MessageDialog.new(
            self.ctx.win, "Paste armored public key",
            "Paste a `gpg --armor --export` block below and press Import.",
        )
        view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        frame = Gtk.Frame(margin_top=8)
        sc = Gtk.ScrolledWindow(height_request=180,
                                hscrollbar_policy=Gtk.PolicyType.NEVER)
        sc.set_child(view)
        frame.set_child(sc)
        extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        extra.append(frame)
        dlg.set_extra_child(extra)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Import")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_d, resp):
            if resp != "ok":
                return
            buf = view.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            if "BEGIN PGP" not in text:
                self.ctx.toast("That does not look like an armored key block")
                return
            self.btn_recv.set_sensitive(False)
            self.set_status("Importing pasted key…")

            def work():
                try:
                    ksvc.import_armored(kf, text)
                    err = None
                except Exception as e:
                    err = str(e)
                GLib.idle_add(self._after_modify, err, "pasted key imported")

            threading.Thread(target=work, daemon=True).start()

        dlg.connect("response", on_response)
        dlg.present()

    def delete_key(self, key):
        if self._guard():
            return
        kf = self.keyring_file()
        if kf is None:
            return
        target = key["fpr"] or key["keyid"]
        uid = key["uids"][0] if key["uids"] else target
        dlg = Adw.MessageDialog.new(
            self.ctx.win,
            "Remove key?",
            f"This deletes\n<b>{uid}</b>\n({target})\nfrom the Nyarch keyring.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("del", "Remove")
        dlg.set_response_appearance("del", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, resp):
            if resp != "del":
                return
            self.set_status(f"Removing key {target[:16]}…")

            def work():
                try:
                    ksvc.delete_keys(kf, [target])
                    err = None
                except Exception as e:
                    err = str(e)
                GLib.idle_add(self._after_modify, err, "key removed")

            threading.Thread(target=work, daemon=True).start()

        dlg.connect("response", on_response)
        dlg.present()

    def _after_modify(self, err, what):
        self.btn_recv.set_sensitive(True)
        if err:
            self.ctx.toast(f"Failed: {err}")
            self.set_status(f"Failed: {err}")
        else:
            self.ctx.toast(what.capitalize())
            self.set_status(what.capitalize() + ". Commit & push the clone when done.")
        self.refresh_list()
        return False

    def export_dialog(self, key):
        kf = self.keyring_file()
        target = key["fpr"] or key["keyid"]

        def work():
            try:
                armor = ksvc.export_key(kf, target)
                err = None
            except Exception as e:
                armor, err = "", str(e)
            GLib.idle_add(self._show_export, armor, err)

        threading.Thread(target=work, daemon=True).start()

    def _show_export(self, armor, err):
        if err:
            self.ctx.toast(f"Export failed: {err}")
            return False
        dlg = Adw.MessageDialog.new(self.ctx.win, "Public key (armored)", None)
        view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        buf = view.get_buffer()
        buf.set_text(armor)
        sc = Gtk.ScrolledWindow(height_request=260,
                                hscrollbar_policy=Gtk.PolicyType.NEVER)
        sc.set_child(view)
        frame = Gtk.Frame(margin_top=8)
        frame.set_child(sc)
        extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        extra.append(frame)
        dlg.set_extra_child(extra)
        dlg.add_response("close", "Close")
        dlg.add_response("copy", "Copy to clipboard")
        dlg.set_response_appearance("copy", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_d, resp):
            if resp == "copy":
                cb = Gdk.Display.get_default().get_clipboard()
                cb.set_content(Gdk.ContentProvider.new_for_value(armor))
                self.ctx.toast("Armored key copied")

        dlg.connect("response", on_response)
        dlg.present()
        return False

    def bump_pkgver(self):
        try:
            new_ver = self.ctx.builder.bump_pkgver(self.base_dir)
        except RuntimeError as e:
            self.ctx.toast(str(e))
            return
        self.ctx.toast(f"nyarch-keyring pkgver bumped to {new_ver}")

    def rebuild_package(self):
        if self._guard():
            return
        self.btn_build.set_sensitive(False)

        def done(summary):
            failed = summary["failed"]

            def apply():
                if failed:
                    self.ctx.toast("Keyring build failed — see terminal")
                else:
                    self.ctx.toast("nyarch-keyring rebuilt and added to the repo")
                self.ctx.reload_repo()
                self.btn_build.set_sensitive(True)
                return False

            GLib.idle_add(apply)

        self.ctx.builder.build_local(["nyarch-keyring"], done)

    def refresh(self):
        self.refresh_list()
