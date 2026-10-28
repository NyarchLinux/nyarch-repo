import importlib
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from GUI import services
from GUI.services import keyring
from GUI.services.builder import Builder


try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib

    GTK_AVAILABLE = Gtk.init_check()
    if GTK_AVAILABLE:
        Adw.init()
except (ImportError, ValueError):
    GTK_AVAILABLE = False


@unittest.skipUnless(GTK_AVAILABLE, "GTK/Adwaita or a display is unavailable")
class SigningKeySelectorTests(unittest.TestCase):
    def setUp(self):
        with mock.patch.dict(
            sys.modules, {"services": services, "services.keyring": keyring},
        ):
            self.module = importlib.import_module("GUI.pages.keyring")
        tmp = tempfile.TemporaryDirectory(prefix="nrm-signing-gui-")
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.ctx = SimpleNamespace(
            pkgbuilds_dir=tmp.name,
            runner=SimpleNamespace(busy=False),
            toast=mock.Mock(),
        )
        self.ctx.builder = Builder(self.ctx)
        with mock.patch.object(self.module.GLib, "idle_add", return_value=0):
            self.page = self.module.KeyringPage(self.ctx)
        self.keys = [
            {"fpr": "A" * 40, "keyid": "A" * 16, "uids": ["First signer"]},
            {"fpr": "B" * 40, "keyid": "B" * 16, "uids": ["Second signer"]},
        ]

    def _select(self, index):
        self.page.signing_key_row.set_selected(index)
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)

    def test_selecting_key_saves_it_and_default_clears_selection(self):
        self.page._render_signing_keys(self.keys)
        self._select(2)
        self.assertEqual(self.ctx.builder.signing_key, self.keys[1]["fpr"])
        self.assertEqual(Builder(self.ctx).signing_key, self.keys[1]["fpr"])
        self._select(0)
        self.assertEqual(self.ctx.builder.signing_key, "")
        self.assertEqual(keyring.load_signing_key(), "")

    def test_refresh_preserves_selection_when_key_order_changes(self):
        self.ctx.builder.set_signing_key(self.keys[1]["fpr"])
        self.page._render_signing_keys(self.keys)
        self.page._render_signing_keys(list(reversed(self.keys)))
        self.assertEqual(self.page.signing_key_row.get_selected(), 1)
        self.assertEqual(self.ctx.builder.signing_key, self.keys[1]["fpr"])

    def test_unavailable_selected_key_is_not_replaced_by_gpg_default(self):
        self.ctx.builder.set_signing_key(self.keys[0]["fpr"])
        with mock.patch.object(self.ctx.builder, "set_signing_key") as save:
            self.page._render_signing_keys([])
        save.assert_not_called()
        self.assertEqual(self.page.signing_key_row.get_selected(), 1)
        self.assertEqual(self.ctx.builder.signing_key, self.keys[0]["fpr"])
        self.assertIn("unavailable", self.page.signing_key_row.get_subtitle())

    def test_failed_save_restores_previous_selection(self):
        self.page._render_signing_keys(self.keys)
        with mock.patch.object(
            keyring, "save_signing_key", side_effect=OSError("read-only"),
        ):
            self._select(1)
        self.assertEqual(self.page.signing_key_row.get_selected(), 0)
        self.assertEqual(self.ctx.builder.signing_key, "")
        self.ctx.toast.assert_called_once()

    def test_key_loading_error_keeps_selection_and_reenables_controls(self):
        self.ctx.builder.set_signing_key(self.keys[0]["fpr"])
        self.page._render_signing_keys(self.keys)
        self.page._render_signing_keys(self.keys, "gpg unavailable")
        self.assertEqual(self.page.signing_key_row.get_selected(), 1)
        self.assertIn("gpg unavailable", self.page.signing_key_row.get_subtitle())
        self.assertTrue(self.page.signing_key_row.get_sensitive())
        self.assertTrue(self.page.btn_signing_refresh.get_sensitive())

    def test_private_key_import_refreshes_signing_choices(self):
        with mock.patch.object(self.page, "refresh_signing_keys") as refresh:
            self.page._after_private_import(None, "/tmp/example.private.asc")
        refresh.assert_called_once()


if __name__ == "__main__":
    unittest.main()
