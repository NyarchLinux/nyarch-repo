import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


GUI_DIR = Path(__file__).resolve().parents[1]
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

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


class BuilderWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nyarch-gui-build-")
        self.addCleanup(self.tmp.cleanup)
        self.ctx = SimpleNamespace(
            repo_root=self.tmp.name, x86_64_dir=self.tmp.name,
            toast=mock.Mock(), runner=mock.Mock(), repo_db=mock.Mock(),
        )
        with mock.patch("GUI.services.builder.keyring.load_signing_key", return_value=""):
            self.builder = Builder(self.ctx)

    def test_single_target_uses_shared_builder_and_explicit_source(self):
        self.builder._finalize = mock.Mock()
        done = mock.Mock()
        self.builder.build_aur_packages(["single"], done)
        cwd, argv, _title, callback = self.ctx.runner.run.call_args.args
        self.assertEqual(cwd, self.tmp.name)
        self.assertEqual(argv, [
            sys.executable, os.path.join(self.tmp.name, "package_builder.py"),
            "--source", "aur", "--no-update-db", "--", "single",
        ])
        callback(False)
        self.builder._finalize.assert_called_once_with([], ["single"], done)
        self.assertEqual(self.ctx.runner.run.call_count, 1)

    def test_local_failure_is_not_reported_as_success(self):
        self.builder._finalize = mock.Mock()
        done = mock.Mock()
        self.builder.build_local(["local"], done)
        argv = self.ctx.runner.run.call_args.args[1]
        self.assertIn("local", argv)
        self.ctx.runner.run.call_args.args[3](False)
        self.builder._finalize.assert_called_once_with([], ["local"], done)

    def test_new_artifacts_are_scanned_before_signing_and_db_failure_is_reported(self):
        self.builder.sign_unsigned = mock.Mock(side_effect=lambda done: done(1, []))
        self.builder.update_db = mock.Mock(side_effect=lambda done: done(False))
        done = mock.Mock()
        self.builder._finalize(["pkg"], [], done)
        self.ctx.repo_db.load.assert_called_once()
        done.assert_called_once_with({"built": ["pkg"], "failed": ["repository database"]})

    def test_fetch_editor_callback_reads_and_cleans_path_file(self):
        package = Path(self.tmp.name) / "PKGBUILD"
        package.write_text("pkgname=example\n")
        done = mock.Mock()
        self.builder.prepare_aur_pkgbuild("example", done)
        argv = self.ctx.runner.run.call_args.args[1]
        path_file = Path(argv[argv.index("--path-file") + 1])
        self.addCleanup(lambda: path_file.unlink(missing_ok=True))
        path_file.write_text(str(package))
        self.ctx.runner.run.call_args.args[3](True)
        done.assert_called_once_with(str(package))
        self.assertFalse(path_file.exists())


@unittest.skipUnless(GTK_AVAILABLE, "GTK/Adwaita or display unavailable")
class EditorAndSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nyarch-editor-test-")
        self.addCleanup(self.tmp.cleanup)
        self.ctx = SimpleNamespace(
            win=mock.Mock(), toast=mock.Mock(), open_terminal=mock.Mock(),
            open_pkgbuild=mock.Mock(), repo_root=self.tmp.name,
            runner=SimpleNamespace(busy=False), builder=mock.Mock(),
        )
        self.editor_module = importlib.import_module("GUI.pages.editor")
        self.editor = self.editor_module.EditorPage(self.ctx)
        self.package = Path(self.tmp.name) / "PKGBUILD"
        self.package.write_text("pkgname=example\n")

    def test_editor_opens_saves_and_tracks_unsaved_changes(self):
        self.editor.open(str(self.package))
        self.assertFalse(self.editor.dirty)
        self.ctx.win.show_page.assert_called_once_with("editor")
        self.editor.buffer.set_text("pkgname=edited\n")
        self.assertTrue(self.editor.dirty)
        self.assertTrue(self.editor.save_button.get_sensitive())
        self.assertTrue(self.editor.save())
        self.assertEqual(self.package.read_text(), "pkgname=edited\n")
        self.assertFalse(self.editor.dirty)

    def test_editor_refuses_to_overwrite_external_changes(self):
        self.editor.open(str(self.package))
        self.editor.buffer.set_text("my changes")
        self.package.write_text("external changes")
        self.assertFalse(self.editor.save())
        self.assertEqual(self.package.read_text(), "external changes")
        self.assertTrue(self.editor.dirty)

    def test_cancel_preserves_unsaved_edits_and_save_continues(self):
        self.editor.open(str(self.package))
        self.editor.buffer.set_text("edited")
        action = mock.Mock()
        dialog = mock.Mock()
        with mock.patch.object(self.editor_module.Adw.MessageDialog, "new", return_value=dialog):
            self.editor.confirm_discard(action)
        response = dialog.connect.call_args.args[1]
        response(dialog, "cancel")
        action.assert_not_called()
        self.assertTrue(self.editor.dirty)
        response(dialog, "save")
        action.assert_called_once()
        self.assertEqual(self.package.read_text(), "edited")

    def test_aur_selection_enables_update_and_editor_for_one_package(self):
        module = importlib.import_module("GUI.pages.updates")
        page = module.UpdatesPage(self.ctx)
        page.store.append(module.UpdateRow("one", "1", "2", "update"))
        page.store.append(module.UpdateRow("two", "1", "2", "update"))
        page.sel_model.select_item(0, True)
        self.assertTrue(page.btn_selected.get_sensitive())
        self.assertTrue(page.btn_edit.get_sensitive())
        page.edit_selected()
        self.ctx.builder.prepare_aur_pkgbuild.assert_called_once_with("one", self.ctx.open_pkgbuild)
        page.sel_model.select_item(1, False)
        self.assertTrue(page.btn_selected.get_sensitive())
        self.assertFalse(page.btn_edit.get_sensitive())

    def test_selected_current_package_can_be_rebuilt_alone(self):
        module = importlib.import_module("GUI.pages.updates")
        page = module.UpdatesPage(self.ctx)
        page.store.append(module.UpdateRow("one", "1", "1", "current"))
        page.store.append(module.UpdateRow("two", "1", "2", "update"))
        page.sel_model.select_item(0, True)
        with mock.patch.object(page, "_confirm_build") as confirm:
            page.update_selected()
        confirm.assert_called_once_with(["one"])

    def test_local_row_opens_editor_and_updates_only_that_directory(self):
        module = importlib.import_module("GUI.pages.local_builds")
        self.ctx.pkgbuilds_dir = self.tmp.name
        self.ctx.repo_db = SimpleNamespace(local_version=lambda _name: None)
        directory = Path(self.tmp.name) / "Example"
        directory.mkdir()
        (directory / "PKGBUILD").write_text("pkgname=example\npkgver=1\n")
        page = module.LocalBuildsPage(self.ctx)
        page.refresh()
        row = page.listbox.get_first_child()
        # Exercise callbacks through the actual row's buttons.
        buttons = []

        def visit(widget):
            if isinstance(widget, Gtk.Button):
                buttons.append(widget)
            child = widget.get_first_child()
            while child:
                visit(child)
                child = child.get_next_sibling()

        visit(row)
        next(b for b in buttons if b.get_label() == "Edit PKGBUILD").emit("clicked")
        self.ctx.open_pkgbuild.assert_called_once_with(str(directory / "PKGBUILD"))
        with mock.patch.object(page, "_start_build") as build:
            next(b for b in buttons if b.get_label() == "Update").emit("clicked")
        build.assert_called_once_with(["Example"])


@unittest.skipUnless(GTK_AVAILABLE, "GTK/Adwaita or display unavailable")
class VteIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.console_module = importlib.import_module("GUI.services.console")
        if not self.console_module.VTE_AVAILABLE:
            self.skipTest("GTK 4 VTE unavailable")
        self.runner_module = importlib.import_module("GUI.services.runner")
        self.console = self.console_module.ConsolePanel(cwd="/tmp")
        self.addCleanup(self.console.close_shells)
        self.window = Gtk.Window()
        self.window.set_child(self.console)
        self.window.present()
        self.addCleanup(self.window.destroy)

    def iterate_until(self, predicate):
        loop = GLib.MainLoop()
        timed_out = []

        def check():
            if predicate():
                loop.quit()
                return False
            return True

        def timeout():
            timed_out.append(True)
            loop.quit()
            return False

        check_id = GLib.timeout_add(10, check)
        timeout_id = GLib.timeout_add_seconds(5, timeout)
        loop.run()
        if timed_out:
            GLib.source_remove(check_id)
        else:
            GLib.source_remove(timeout_id)
        self.assertFalse(timed_out, "VTE operation timed out")

    def test_real_vte_build_and_interactive_shell_are_independent(self):
        runner = self.runner_module.Runner(self.console)
        results = []
        # Do not let personal shell startup files affect the integration test.
        with mock.patch.dict(os.environ, {"SHELL": "/bin/sh", "ENV": "/dev/null"}):
            self.console.open_shell("/tmp")
        self.iterate_until(lambda: self.console.shells["/tmp"]["pid"] is not None)
        shell = self.console.shells["/tmp"]
        self.assertFalse(runner.busy)
        self.assertTrue(self.console.open_shell("/tmp"))
        self.assertEqual(len(self.console.shells), 1)
        runner.run("/tmp", ["/bin/sh", "-c", "test -t 0 && printf 'VTE test\\n'"],
                   "VTE test", results.append)
        self.assertTrue(runner.busy)
        self.iterate_until(lambda: len(results) == 1)
        self.assertEqual(results, [True])
        self.assertFalse(runner.busy)
        self.assertIsNotNone(shell["pid"])
        shell["term"].feed_child(b"exit\r")
        self.iterate_until(lambda: shell["pid"] is None)

    def test_async_spawn_failure_does_not_stall_next_command(self):
        runner = self.runner_module.Runner(self.console)
        results = []
        runner.run("/tmp", ["/does/not/exist"], "Missing command", results.append)
        runner.run("/tmp", ["/bin/true"], "Next command", results.append)
        self.iterate_until(lambda: len(results) == 2)
        self.assertEqual(results, [False, True])
        self.assertFalse(runner.busy)

    def test_without_vte_output_fallback_is_usable_and_shell_is_disabled(self):
        with mock.patch.object(self.console_module, "VTE_AVAILABLE", False):
            console = self.console_module.ConsolePanel()
        console.write_line("\x1b[31moutput\x1b[0m")
        self.assertEqual(console.buf.get_text(
            console.buf.get_start_iter(), console.buf.get_end_iter(), True), "output\n")
        self.assertFalse(console.shell_button.get_sensitive())
        self.assertFalse(console.open_shell())


if __name__ == "__main__":
    unittest.main()
