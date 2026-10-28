import datetime
import glob
import os
import re
import shlex

from .repo_db import vercmp
from . import keyring


class Builder:
    def __init__(self, ctx):
        self.ctx = ctx
        self.signing_key = ""
        try:
            self.signing_key = keyring.load_signing_key()
        except (OSError, ValueError) as e:
            ctx.toast(f"Cannot load signing key; using GPG default: {e}")

    def set_signing_key(self, fingerprint):
        self.signing_key = keyring.save_signing_key(fingerprint)

    @property
    def runner(self):
        return self.ctx.runner

    def read_list(self, path):
        names = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s and not s.startswith("#"):
                        names.append(s)
        except FileNotFoundError:
            pass
        return names

    def append_unique(self, path, names):
        existing = set(self.read_list(path))
        added = [n for n in names if n not in existing]
        if not added:
            return []
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n# added by nyarch-repo-manager\n")
            for n in added:
                f.write(f"{n}\n")
        return added

    def merge_into_full(self, names):
        path = self.ctx.aur_pkgs_full_file
        existing = set(self.read_list(path))
        merged = sorted(set(names) | existing)
        with open(path, "w", encoding="utf-8") as f:
            for n in merged:
                f.write(f"{n}\n")
        return [n for n in sorted(set(names)) if n not in existing]

    def build_aur_packages(self, names, on_done):
        names = list(dict.fromkeys(names))
        if not names:
            on_done({"built": [], "failed": []})
            return
        build_dir = self.ctx.aur_build_dir
        repo_dir = self.ctx.x86_64_dir
        steps = []
        built = []
        failed = []

        for name in names:
            dst = os.path.join(build_dir, name)
            clone_cmd = (
                f"rm -rf {shlex.quote(dst)} && "
                f"git clone --depth 1 {shlex.quote('https://aur.archlinux.org/' + name + '.git')} "
                f"{shlex.quote(dst)}"
            )
            steps.append((f"Cloning {name} from AUR", ["bash", "-c", clone_cmd], build_dir))
            steps.append(
                (f"Building {name}", ["makepkg", "-sf", "--noconfirm"], dst)
            )
            steps.append(
                (
                    f"Installing {name} into repository",
                    ["bash", "-c", self._move_script(repo_dir)],
                    dst,
                )
            )

        state = {"i": 0}

        def run_next():
            i = state["i"]
            if i >= len(steps):
                self._finalize(built, failed, on_done)
                return
            title, argv, cwd = steps[i]
            step_kind = title.split(" ")[0].lower()
            pkg = title.split(" ", 1)[-1]

            def step_done(ok, kind=step_kind, p=pkg):
                if kind == "building":
                    (built if ok else failed).append(p)
                state["i"] += 1
                run_next()

            self.runner.run(cwd, argv, title, step_done)

        run_next()

    def build_local(self, dirs, on_done):
        dirs = list(dict.fromkeys(dirs))
        if not dirs:
            on_done({"built": [], "failed": []})
            return
        base = self.ctx.pkgbuilds_dir
        repo_dir = self.ctx.x86_64_dir
        steps = []
        built = []
        failed = []
        for d in dirs:
            cwd = os.path.join(base, d)
            steps.append((f"Building {d}", ["makepkg", "-sf", "--noconfirm"], cwd))
            steps.append(
                (
                    f"Installing {d} into repository",
                    ["bash", "-c", self._move_script(repo_dir)],
                    cwd,
                )
            )
        state = {"i": 0}

        def run_next():
            i = state["i"]
            if i >= len(steps):
                self._finalize(built, failed, on_done)
                return
            title, argv, cwd = steps[i]

            def step_done(ok, idx=i):
                kind = steps[idx][0].split(" ")[0].lower()
                pkg = steps[idx][0].split(" ", 1)[-1]
                if kind == "building":
                    (built if ok else failed).append(pkg)
                state["i"] += 1
                run_next()

            self.runner.run(cwd, argv, title, step_done)

        run_next()

    @staticmethod
    def _move_script(repo_dir):
        return (
            "shopt -s nullglob\n"
            "files=(./*.pkg.tar.*)\n"
            "if [ ${#files[@]} -eq 0 ]; then\n"
            "  echo 'No package artifact produced'\n"
            "  exit 1\n"
            "fi\n"
            "mv -f \"${files[@]}\" " + shlex.quote(repo_dir) + "/\n"
            "echo \"Moved ${#files[@]} artifact(s) into repository\""
        )

    def sign_unsigned(self, on_done):
        targets = self.ctx.repo_db.unsigned_files()
        if not targets:
            on_done(0, [])
            return
        failures = []
        state = {"i": 0}
        # Keep one signer throughout this batch, even if the GUI selection changes.
        signing_key = self.signing_key

        def nxt(prev_ok=None, idx=None):
            if idx is not None and not prev_ok:
                failures.append(os.path.basename(targets[idx]))
            i = state["i"]
            if i >= len(targets):
                on_done(len(targets) - len(failures), failures)
                return
            path = os.path.abspath(targets[i])
            name = os.path.basename(path)

            def done(ok, j=i):
                state["i"] += 1
                nxt(ok, j)

            argv = ["gpg", "--detach-sign", "--use-agent"]
            if signing_key:
                argv.extend(["--local-user", signing_key])
            argv.append(path)
            self.runner.run(
                self.ctx.x86_64_dir,
                argv,
                f"Signing {name}",
                done,
            )

        nxt()

    def update_db(self, on_done):
        self.runner.run(
            self.ctx.x86_64_dir,
            ["bash", "update_repo.sh"],
            "Rebuilding repository database",
            lambda ok: on_done(ok),
        )

    def _finalize(self, built, failed, on_done):
        def after_sign(signed, sign_failures):
            if sign_failures:
                self.ctx.toast(
                    "Warning: failed to sign: " + ", ".join(sign_failures[:3])
                )

            def after_db(ok):
                on_done({"built": built, "failed": failed})

            self.update_db(after_db)

        self.sign_unsigned(after_sign)

    def compare_versions(self, a, b):
        return vercmp(a, b)

    def get_prune_candidates(self):
        db = self.ctx.repo_db
        candidates = []
        for name, recs in db.duplicates.items():
            keep = db.latest_record(name)
            if keep is None:
                continue
            for r in recs:
                if r["file"] != keep["file"] and r.get("split"):
                    candidates.append(r)
        return candidates

    def prune_old_versions(self, on_done):
        candidates = self.get_prune_candidates()
        if not candidates:
            on_done({"deleted": 0, "freed": 0, "db_ok": True})
            return
        paths = []
        for r in candidates:
            p = r["path"]
            paths.append(p)
            sig = p + ".sig"
            if os.path.exists(sig):
                paths.append(sig)
        freed = sum(
            os.path.getsize(r["path"])
            for r in candidates
            if os.path.exists(r["path"])
        )

        def after_rm(ok):
            self.update_db(lambda db_ok: on_done({
                "deleted": len(candidates) if ok else 0,
                "freed": freed if ok else 0,
                "db_ok": db_ok,
            }))

        self.runner.run(
            self.ctx.x86_64_dir,
            ["rm", "-fv", *paths],
            f"Removing {len(candidates)} old package version(s)",
            after_rm,
        )

    @staticmethod
    def bump_pkgver(pkg_dir):
        pb = os.path.join(pkg_dir, "PKGBUILD")
        try:
            with open(pb, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            raise RuntimeError(f"cannot read {pb}: {e}")
        m = re.search(r"^pkgver=(.*)$", text, re.M)
        if not m:
            raise RuntimeError("pkgver not found in PKGBUILD")
        val = m.group(1).strip().strip("'\"")
        if re.fullmatch(r"\d{8}", val):
            new_ver = datetime.date.today().strftime("%Y%m%d")
        elif val.isdigit():
            new_ver = str(int(val) + 1)
        else:
            raise RuntimeError(f"cannot bump pkgver='{val}'")
        new_text = text[: m.start()] + f"pkgver={new_ver}" + text[m.end():]
        with open(pb, "w", encoding="utf-8") as f:
            f.write(new_text)
        return new_ver

    def find_artifacts(self, directory):
        return sorted(glob.glob(os.path.join(directory, "*.pkg.tar.*")))
