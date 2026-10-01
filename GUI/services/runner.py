import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib

from .console import VTE_AVAILABLE

if VTE_AVAILABLE:
    from gi.repository import Vte


class Runner:
    def __init__(self, console):
        self.console = console
        self.queue = []
        self.current = None
        self.busy = False
        self.current_title = None
        self.on_busy_changed = None
        self.on_current_changed = None
        self._pty_broken = False
        if VTE_AVAILABLE and console.using_vte:
            try:
                console.term.connect("child-exited", self._on_vte_exit)
            except Exception:
                self._pty_broken = True

    @property
    def pending(self):
        return len(self.queue) + (1 if self.current else 0)

    def run(self, cwd, argv, title, on_done=None):
        op = {
            "cwd": cwd,
            "argv": [str(a) for a in argv],
            "title": title,
            "on_done": on_done,
        }
        self.queue.append(op)
        GLib.idle_add(self._pump)

    def _pump(self, *_):
        if self.current or not self.queue:
            return False
        op = self.queue.pop(0)
        self.current = op
        self._set_busy(True)
        self.current_title = op["title"]
        if self.on_current_changed:
            self.on_current_changed(op["title"])
        self.console.write(b"\n")
        self.console.write(
            f"\x1b[1;36m\u2554\u2550 {op['title']} \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\x1b[0m\n".encode()
        )
        self.console.write(
            ("\x1b[2m$ " + " ".join(op["argv"]) + "\x1b[0m\n").encode()
        )
        used_pty = False
        if VTE_AVAILABLE and self.console.can_spawn_pty and not self._pty_broken:
            used_pty = self._try_spawn_pty(op)
            if used_pty:
                return False
        self._spawn_pipes(op)
        return False

    def _try_spawn_pty(self, op):
        term = self.console.term
        cwd = op["cwd"]
        argv = op["argv"]

        def on_spawned(term_, pid, _data=None):
            if self.current is not None:
                self.current["_pid"] = int(pid)

        try:
            op["_pty"] = True
            term.spawn_async(
                pty_flags=Vte.PtyFlags.DEFAULT,
                working_directory=cwd,
                argv=argv,
                envv=None,
                spawn_flags=GLib.SpawnFlags.DEFAULT,
                timeout=-1,
                cancellable=None,
                callback=on_spawned,
            )
            return True
        except TypeError as e:
            op.pop("_pty", None)
            self.console.write_line(
                f"[vte spawn_async unsupported ({e}); using pipe mode]"
            )
            self._pty_broken = True
            return False
        except GLib.Error as e:
            op.pop("_pty", None)
            self.console.write_line(f"[spawn failed: {e.message}]")
            return False

    def _on_vte_exit(self, term, status):
        if self.current is not None and self.current.get("_pty"):
            GLib.idle_add(self._finish, status)

    def _spawn_pipes(self, op):
        try:
            spawned = GLib.spawn_async_with_pipes(
                op["cwd"],
                op["argv"],
                None,
                GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD,
            )
            if len(spawned) == 5:
                _, pid, stdin_fd, stdout_fd, stderr_fd = spawned
            else:
                pid, stdin_fd, stdout_fd, stderr_fd = spawned
        except GLib.Error as e:
            self.console.write_line(
                f"\x1b[1;31mcould not run {' '.join(op['argv'])}: {e.message}\x1b[0m"
            )
            GLib.idle_add(self._finish, -1)
            return
        except TypeError as e:
            self.console.write_line(
                f"\x1b[1;31mcould not run command ({e})\x1b[0m"
            )
            GLib.idle_add(self._finish, -1)
            return
        os.close(stdin_fd)
        op["_pid"] = int(pid)
        op["_streams"] = 2
        op["_exit"] = None
        for fd in (stdout_fd, stderr_fd):
            os.set_blocking(fd, False)
            GLib.io_add_watch(
                fd,
                GLib.PRIORITY_DEFAULT,
                GLib.IO_IN | GLib.IO_HUP,
                self._pipe_read,
                op,
            )
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, self._child_watch, op)

    def _pipe_read(self, fd, cond, op):
        data = b""
        while True:
            try:
                chunk = os.read(fd, 65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                data += b""
                break
            if not chunk:
                break
            data += chunk
        if data:
            self.console.write(data)
        if cond & GLib.IO_HUP or not data:
            try:
                one_more = os.read(fd, 65536)
                if one_more:
                    self.console.write(one_more)
            except OSError:
                pass
            op["_streams"] -= 1
            try:
                os.close(fd)
            except OSError:
                pass
            self._maybe_finalize(op)
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _child_watch(self, pid, status, op):
        GLib.spawn_close_pid(pid)
        op["_exit"] = status
        self._maybe_finalize(op)

    def _maybe_finalize(self, op):
        if op.get("_streams", 0) > 0 or op.get("_exit") is None:
            return
        GLib.idle_add(self._finish, op["_exit"])

    def _finish(self, status):
        op = self.current
        if op is None:
            return False
        ok = status == 0
        shown = status
        if status and os.WIFEXITED(status):
            shown = os.WEXITSTATUS(status)
        color = "1;32" if ok else "1;31"
        word = "done" if ok else f"failed (exit {shown})"
        self.console.write(
            f"\x1b[{color}\u255a\u2550 {word}: {op['title']} "
            f"\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\x1b[0m\n".encode()
        )
        cb = op.get("on_done")
        self.current = None
        self.current_title = None
        self._set_busy(False)
        if self.on_current_changed:
            self.on_current_changed(None)
        if cb:
            try:
                cb(ok)
            except Exception as e:
                print(f"callback error after '{op['title']}': {e}")
        GLib.idle_add(self._pump)
        return False

    def _set_busy(self, val):
        self.busy = val
        if self.on_busy_changed:
            try:
                self.on_busy_changed(val)
            except Exception:
                pass
