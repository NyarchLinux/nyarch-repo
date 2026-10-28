import datetime
import os
import re
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk, Gio

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

        btn_file = Gtk.Button(label="Import file…")
        btn_file.set_tooltip_text(
            "Pick an armored (.asc) or binary (.gpg) public key file "
            "and add it to the keyring"
        )
        btn_file.connect("clicked", lambda *_: self.file_import_dialog())
        bar.append(btn_file)

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

        self.append(Gtk.Separator(margin_top=2, margin_bottom=2))
        prow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ptitle = Gtk.Label(halign=Gtk.Align.START, valign=Gtk.Align.CENTER,
                           wrap=True)
        ptitle.set_markup(
            "<b>My private signing key</b> <span alpha='55%'>"
            "(~/.gnupg — move to another machine)</span>"
        )
        prow.append(ptitle)
        prow.append(Gtk.Box(hexpand=True))
        self.btn_exp_priv = Gtk.Button(label="Export…")
        self.btn_exp_priv.set_tooltip_text(
            "Save an armored private key (.asc) to move your signing key "
            "elsewhere. Its existing passphrase is unchanged. "
            "gpg may ask for it. Keep the file secret."
        )
        self.btn_exp_priv.connect(
            "clicked", lambda *_: self.export_private_dialog()
        )
        prow.append(self.btn_exp_priv)
        self.btn_imp_priv = Gtk.Button(label="Import…")
        self.btn_imp_priv.set_tooltip_text(
            "Add a private key from an .asc/.gpg file to this machine's keyring"
        )
        self.btn_imp_priv.connect(
            "clicked", lambda *_: self.import_private_dialog()
        )
        prow.append(self.btn_imp_priv)
        self.append(prow)

        self._signing_keys = []
        self._signing_fingerprints = [""]
        self._updating_signing_key = False
        self._loading_signing_keys = False
        signing_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        signing_list.add_css_class("boxed-list")
        self.signing_key_row = Adw.ComboRow(
            title="Package signing key",
            enable_search=True,
        )
        self.signing_key_row.set_tooltip_text(
            "Used for manual package signing and automatic signing after builds. "
            "Changes apply to the next signing batch, not existing signatures. "
            "The public key must be trusted by the Nyarch keyring."
        )
        self.signing_key_row.connect(
            "notify::selected", self._on_signing_key_selected,
        )
        self.btn_signing_refresh = Gtk.Button(
            icon_name="view-refresh-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Reload private signing keys",
        )
        self.btn_signing_refresh.add_css_class("flat")
        self.btn_signing_refresh.connect(
            "clicked", lambda *_: self.refresh_signing_keys(),
        )
        self.signing_key_row.add_suffix(self.btn_signing_refresh)
        signing_list.append(self.signing_key_row)
        self.append(signing_list)
        self._render_signing_keys([])

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
        GLib.idle_add(self.refresh_signing_keys)

    def refresh_signing_keys(self):
        if self._loading_signing_keys:
            return False
        self._loading_signing_keys = True
        self.btn_signing_refresh.set_sensitive(False)
        self.signing_key_row.set_sensitive(False)

        def work():
            try:
                keys = ksvc.list_signing_keys()
                err = None
            except Exception as e:
                keys, err = self._signing_keys, str(e)
            GLib.idle_add(self._render_signing_keys, keys, err)

        threading.Thread(target=work, daemon=True).start()
        return False

    def _render_signing_keys(self, keys, err=None):
        self._signing_keys = keys
        selected = self.ctx.builder.signing_key
        fingerprints = [""] + [k["fpr"] for k in keys]
        options = ["GPG default (gpg.conf)"]
        for key in keys:
            uid = key["uids"][0] if key["uids"] else key["keyid"]
            options.append(f"{uid} — {key['fpr']}")
        missing = selected and selected not in fingerprints
        if missing:
            fingerprints.append(selected)
            options.append(f"Unavailable key — {selected}")
        self._updating_signing_key = True
        try:
            self._signing_fingerprints = fingerprints
            self.signing_key_row.set_model(Gtk.StringList.new(options))
            self.signing_key_row.set_selected(fingerprints.index(selected))
        finally:
            self._updating_signing_key = False
        if err:
            subtitle = f"Cannot load signing keys: {err}"
        elif missing:
            subtitle = (
                "Selected key is unavailable. Import it or choose another key; "
                "signing will not fall back to a different key."
            )
        elif selected:
            subtitle = "Used for manual signing and after builds. Choice saved."
        else:
            subtitle = "Let GPG choose the key for manual signing and builds."
            if not keys:
                subtitle += " No usable private signing keys found."
        self.signing_key_row.set_subtitle(subtitle)
        self._loading_signing_keys = False
        self.btn_signing_refresh.set_sensitive(True)
        self.signing_key_row.set_sensitive(True)
        return False

    def _on_signing_key_selected(self, row, _pspec):
        if self._updating_signing_key:
            return
        index = row.get_selected()
        if index >= len(self._signing_fingerprints):
            return
        fingerprint = self._signing_fingerprints[index]
        if fingerprint == self.ctx.builder.signing_key:
            return
        try:
            self.ctx.builder.set_signing_key(fingerprint)
        except (OSError, ValueError) as e:
            self.ctx.toast(f"Cannot save signing key: {e}")
        # Changing ComboRow's model inside notify::selected can re-enter GTK.
        GLib.idle_add(self._render_signing_keys, self._signing_keys)

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

    def file_import_dialog(self):
        if self._guard():
            return
        kf = self.keyring_file()
        if kf is None:
            self.ctx.toast("Fetch the keyring repo first")
            return
        flt = Gtk.FileFilter(name="PGP public keys")
        flt.add_pattern("*.asc")
        flt.add_pattern("*.gpg")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        dlg = Gtk.FileDialog(title="Choose a public key file", filters=filters)
        dlg.open(self.ctx.win, None, self._on_import_file_chosen, kf)

    def _on_import_file_chosen(self, dlg, result, kf):
        try:
            f = dlg.open_finish(result)
        except GLib.Error:
            return
        path = f.get_path()
        if not path:
            return
        self.set_status(f"Importing {os.path.basename(path)}…")

        def work():
            try:
                ksvc.import_key_file(kf, path)
                err = None
            except Exception as e:
                err = str(e)
            GLib.idle_add(
                self._after_modify, err,
                f"key imported from {os.path.basename(path)}",
            )

        threading.Thread(target=work, daemon=True).start()

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
            GLib.idle_add(self._show_export, armor, err, target)

        threading.Thread(target=work, daemon=True).start()

    def _show_export(self, armor, err, target=""):
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
        dlg.add_response("save", "Save to file…")
        dlg.add_response("copy", "Copy to clipboard")
        dlg.set_response_appearance("copy", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_d, resp):
            if resp == "copy":
                cb = Gdk.Display.get_default().get_clipboard()
                cb.set_content(Gdk.ContentProvider.new_for_value(armor))
                self.ctx.toast("Armored key copied")
            elif resp == "save":
                sdlg = Gtk.FileDialog(title="Save public key")
                sdlg.set_initial_name(f"{target}.asc")
                sdlg.save(self.ctx.win, None, self._on_save_done, armor)

        dlg.connect("response", on_response)
        dlg.present()
        return False

    def _on_save_done(self, dlg, result, armor):
        try:
            f = dlg.save_finish(result)
        except GLib.Error:
            return
        path = f.get_path()
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(armor if armor.endswith("\n") else armor + "\n")
        except OSError as e:
            self.ctx.toast(f"Save failed: {e}")
        else:
            self.ctx.toast(f"Key saved to {os.path.basename(path)}")

    # ---- private signing key (~/.gnupg) ---------------------------------

    def export_private_dialog(self):
        if self._guard():
            return
        self._set_priv_buttons(False)
        self.set_status("Listing private keys…")

        def work():
            try:
                keys = ksvc.list_secret_keys()
                err = None
            except Exception as e:
                keys, err = [], str(e)
            GLib.idle_add(self._pick_private_key, keys, err)

        threading.Thread(target=work, daemon=True).start()

    def _set_priv_buttons(self, sensitive):
        self.btn_exp_priv.set_sensitive(sensitive)
        self.btn_imp_priv.set_sensitive(sensitive)

    def _pick_private_key(self, keys, err):
        try:
            if err:
                self.ctx.toast(f"Failed: {err}")
                self.set_status(f"Failed: {err}")
                return False
            if not keys:
                self.ctx.toast("No private keys found in ~/.gnupg")
                self.set_status("")
                return False
            if len(keys) == 1:
                self._do_private_export(keys[0])
                return False
            options = []
            for k in keys:
                uid = k["uids"][0] if k["uids"] else k["keyid"]
                fpr = k["fpr"] or k["keyid"]
                options.append(f"{uid} — {fpr[:16]}")
            dlg = Adw.MessageDialog.new(
                self.ctx.win, "Export which private key?", None
            )
            dropdown = Gtk.DropDown.new_from_strings(options)
            extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            extra.append(dropdown)
            dlg.set_extra_child(extra)
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("ok", "Export")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

            def on_response(_d, resp):
                if resp != "ok":
                    return
                idx = dropdown.get_selected()
                if idx < len(keys):
                    self._do_private_export(keys[idx])

            dlg.connect("response", on_response)
            dlg.present()
            return False
        finally:
            self._set_priv_buttons(True)

    def _do_private_export(self, key):
        target = key["fpr"] or key["keyid"]
        uid = key["uids"][0] if key["uids"] else target
        self.set_status(f"Exporting {uid} — gpg may ask for your passphrase…")

        def work():
            try:
                armor = ksvc.export_secret_key(target)
                err = None
            except Exception as e:
                armor, err = "", str(e)
            GLib.idle_add(self._save_private_export, armor, err, target)

        threading.Thread(target=work, daemon=True).start()

    def _save_private_export(self, armor, err, target):
        self._set_priv_buttons(True)
        if err:
            self.ctx.toast(f"Export failed: {err}")
            self.set_status(f"Export failed: {err}")
            return False
        sdlg = Gtk.FileDialog(title="Save private key")
        sdlg.set_initial_name(f"{target}.private.asc")
        sdlg.save(self.ctx.win, None, self._on_private_saved, armor)
        return False

    def _on_private_saved(self, dlg, result, armor):
        try:
            f = dlg.save_finish(result)
        except GLib.Error:
            return
        path = f.get_path()
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(armor if armor.endswith("\n") else armor + "\n")
            os.chmod(path, 0o600)
        except OSError as e:
            self.ctx.toast(f"Save failed: {e}")
            return
        warn = Adw.MessageDialog.new(
            self.ctx.win,
            "Private key saved",
            f"Saved to <b>{esc(os.path.basename(path))}</b>.\n\n"
            "The export keeps the key's existing passphrase protection "
            "(if any); it does not set a new passphrase. Treat the file as a "
            "secret: transfer it over a secure channel, import it on the "
            "other machine, then delete it (<tt>shred -u</tt>). Never commit "
            "or share it.",
        )
        warn.add_response("close", "OK")
        warn.present()
        self.set_status(
            f"Private key exported to {os.path.basename(path)} — "
            "delete it after importing"
        )

    def import_private_dialog(self):
        if self._guard():
            return
        dlg = Adw.MessageDialog.new(
            self.ctx.win,
            "Import a private key?",
            "This adds the private key from the chosen file to this "
            "machine's GPG keyring (<tt>~/.gnupg</tt>). Only import keys you "
            "control; the file must have been exported with "
            "<tt>--export-secret-keys</tt> (or the GUI export button). "
            "Use the key's existing passphrase; importing does not reset it.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Choose file…")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_d, resp):
            if resp != "ok":
                return
            flt = Gtk.FileFilter(name="PGP keys")
            flt.add_pattern("*.asc")
            flt.add_pattern("*.gpg")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(flt)
            fdlg = Gtk.FileDialog(title="Choose a private key file",
                                  filters=filters)
            fdlg.open(self.ctx.win, None, self._on_private_file_chosen)

        dlg.connect("response", on_response)
        dlg.present()

    def _on_private_file_chosen(self, dlg, result):
        try:
            f = dlg.open_finish(result)
        except GLib.Error:
            return
        path = f.get_path()
        if not path:
            return
        self._set_priv_buttons(False)
        self.set_status(f"Importing private key from {os.path.basename(path)}…")

        def work():
            try:
                ksvc.import_private_key_file(path)
                err = None
            except Exception as e:
                err = str(e)
            GLib.idle_add(self._after_private_import, err, path)

        threading.Thread(target=work, daemon=True).start()

    def _after_private_import(self, err, path):
        self._set_priv_buttons(True)
        name = os.path.basename(path)
        if err:
            self.ctx.toast(f"Failed: {err}")
            self.set_status(f"Failed: {err}")
        else:
            self.ctx.toast(f"Private key imported from {name}")
            self.set_status(f"Private key imported from {name}")
            self.refresh_signing_keys()
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
        self.refresh_signing_keys()
