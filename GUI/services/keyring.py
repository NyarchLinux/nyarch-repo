import glob
import os
import shutil
import subprocess
import tempfile

CLONE_URL = "https://github.com/NyarchLinux/NyarchKeyring.git"
DEFAULT_KEYSERVER = "hkps://keyserver.ubuntu.com"


def _q(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def ensure_clone_script(base_dir):
    base_dir = os.path.abspath(base_dir)
    clone = os.path.join(base_dir, "NyarchKeyring")
    return (
        f"if [ -d {_q(clone + '/.git')} ]; then "
        f"git -C {_q(clone)} pull --ff-only; "
        f"else git clone {_q(CLONE_URL)} {_q(clone)}; fi"
    )


def find_keyring_file(clone_dir):
    if not os.path.isdir(clone_dir):
        return None
    candidates = sorted(glob.glob(os.path.join(clone_dir, "*.gpg")))
    if not candidates:
        for root, dirs, files in os.walk(clone_dir):
            depth = root[len(clone_dir):].count(os.sep)
            if depth >= 2:
                dirs[:] = []
                continue
            for fn in files:
                if fn.endswith(".gpg"):
                    candidates.append(os.path.join(root, fn))
            if candidates:
                break
    return candidates[0] if candidates else None


class _IsolatedHome:
    def __init__(self, keyring_path):
        self.keyring_path = keyring_path
        self.home = tempfile.mkdtemp(prefix="nrm-gpg-")
        os.chmod(self.home, 0o700)
        self.armored = False

    def __enter__(self):
        pub = os.path.join(self.home, "pubring.gpg")
        kf = self.keyring_path
        if os.path.exists(kf) and os.path.getsize(kf) > 0:
            with open(kf, "rb") as f:
                head = f.read(64)
            if head.lstrip().startswith(b"-----BEGIN PGP PUBLIC KEY BLOCK"):
                self.armored = True
                with open(kf, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                rc, out, err = _run(
                    self.env(), ["gpg", "--import"], stdin_text=text,
                )
                if rc != 0:
                    raise RuntimeError(
                        "cannot read existing keyring: "
                        + (err.strip()[-200:] or out.strip()[-200:])
                    )
            else:
                shutil.copyfile(kf, pub)
        else:
            open(pub, "wb").close()
        return self

    def env(self):
        e = dict(os.environ)
        e["GNUPGHOME"] = self.home
        return e

    def write_back(self):
        pub = os.path.join(self.home, "pubring.gpg")
        tmp = self.keyring_path + ".tmp"
        if self.armored:
            rc, armor, err = _run(
                self.env(), ["gpg", "--armor", "--export"],
            )
            if rc != 0 or not armor.strip():
                raise RuntimeError(err.strip() or "re-export failed")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(armor if armor.endswith("\n") else armor + "\n")
        else:
            shutil.copyfile(pub, tmp)
        os.replace(tmp, self.keyring_path)

    def __exit__(self, *exc):
        shutil.rmtree(self.home, ignore_errors=True)


def _run(home_env, argv, timeout=120, stdin_text=None):
    p = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout,
        input=stdin_text, env=home_env,
    )
    return p.returncode, p.stdout, p.stderr


def list_keys(kf):
    with _IsolatedHome(kf) as home:
        rc, out, err = _run(
            home.env(),
            ["gpg", "--list-keys", "--with-colons"],
        )
    if rc != 0:
        raise RuntimeError(err.strip() or "gpg --list-keys failed")
    order = []
    by_keyid = {}
    last = None
    for line in out.splitlines():
        parts = line.split(":")
        tag = parts[0]
        if tag == "pub":
            keyid = parts[4]
            if keyid in by_keyid:
                last = by_keyid[keyid]
            else:
                last = {
                    "keyid": keyid,
                    "expires": parts[6] or "",
                    "fpr": "",
                    "uids": [],
                }
                by_keyid[keyid] = last
                order.append(last)
        elif tag == "fpr" and last is not None and not last["fpr"]:
            last["fpr"] = parts[9]
        elif tag == "uid" and last is not None:
            uid = parts[9]
            if uid not in last["uids"]:
                last["uids"].append(uid)
    return [k for k in order if k["fpr"] or k["uids"]]


def import_armored(kf, armored_text):
    if "BEGIN PGP PUBLIC KEY" not in armored_text:
        raise RuntimeError("input is not an armored public key block")
    with _IsolatedHome(kf) as home:
        rc, out, err = _run(
            home.env(), ["gpg", "--import"], stdin_text=armored_text,
        )
        if rc != 0:
            raise RuntimeError(err.strip() or "import failed")
        home.write_back()
    return True


def recv_keys(kf, ids, keyserver=DEFAULT_KEYSERVER):
    with _IsolatedHome(kf) as home:
        rc, out, err = _run(
            home.env(),
            ["gpg", "--keyserver", keyserver, "--recv-keys", *ids],
            timeout=180,
        )
        if rc != 0:
            raise RuntimeError(err.strip() or "keyserver fetch failed")
        home.write_back()
    return True


def delete_keys(kf, fingerprints):
    with _IsolatedHome(kf) as home:
        rc, out, err = _run(
            home.env(),
            ["gpg", "--batch", "--yes", "--delete-keys", *fingerprints],
        )
        if rc != 0:
            raise RuntimeError(err.strip() or "delete failed")
        home.write_back()
    return True


def export_key(kf, fingerprint):
    with _IsolatedHome(kf) as home:
        rc, out, err = _run(
            home.env(), ["gpg", "--armor", "--export", fingerprint],
        )
    if rc != 0 or not out.strip():
        raise RuntimeError(err.strip() or "nothing to export")
    return out
