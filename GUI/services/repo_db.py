import os
import re
import subprocess
import tarfile
from dataclasses import dataclass, field

PKG_SUFFIX_RE = re.compile(r"-(any|x86_64|i686|aarch64)\.pkg\.tar\.(zst|xz|gz)$")


def split_pkg_filename(fn):
    m = PKG_SUFFIX_RE.search(fn)
    if not m:
        return None
    stem = fn[: m.start()]
    parts = stem.rsplit("-", 2)
    if len(parts) != 3:
        return None
    name, ver, rel = parts
    return name, f"{ver}-{rel}", m.group(1)


def vercmp(a, b):
    if a == b:
        return 0
    if a is None:
        return -1
    if b is None:
        return 1
    try:
        r = subprocess.run(
            ["vercmp", str(a), str(b)], capture_output=True, text=True, timeout=10
        )
        return int((r.stdout or "0").strip())
    except Exception:
        return 0


@dataclass
class DbEntry:
    name: str = ""
    base: str = ""
    version: str = ""
    arch: str = ""
    desc: str = ""
    url: str = ""
    packager: str = ""
    builddate: str = ""
    filename: str = ""
    csize: int = 0
    isize: int = 0
    license: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    depends: list = field(default_factory=list)
    makedepends: list = field(default_factory=list)
    checkdepends: list = field(default_factory=list)
    optdepends: list = field(default_factory=list)
    provides: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    replaces: list = field(default_factory=list)


class RepoDatabase:
    def __init__(self, repo_root):
        self.repo_root = os.path.abspath(repo_root)
        self.repo_dir = os.path.join(self.repo_root, "x86_64")
        self.entries = []
        self.by_name = {}
        self.files = {}
        self.orphans = []
        self.stale = []
        self.duplicates = {}
        self.error = None

    def find_db(self):
        for cand in ("nyarch-repo.db", "nyarch-repo.db.tar.gz"):
            p = os.path.join(self.repo_dir, cand)
            if os.path.exists(p) and not os.path.islink(p):
                return p
            if os.path.exists(p):
                return p
        return None

    def load(self):
        self.error = None
        self.entries = []
        self.by_name = {}
        self.files = {}
        self.orphans = []
        self.stale = []
        self.duplicates = {}
        db = self.find_db()
        if db:
            try:
                self._load_db(db)
            except Exception as e:
                self.error = f"Failed to parse repository database: {e}"
        else:
            self.error = "Repository database not found in x86_64/"
        self._scan_disk()

    def _load_db(self, path):
        descs = {}
        with tarfile.open(path, "r:*") as tf:
            for m in tf.getmembers():
                if not m.isfile():
                    continue
                parts = m.name.split("/")
                if len(parts) < 2 or parts[-1] != "desc":
                    continue
                f = tf.extractfile(m)
                if f is None:
                    continue
                descs[parts[-2]] = f.read().decode("utf-8", "replace")
        for body in descs.values():
            e = self._parse_desc(body)
            if e and e.name:
                self.entries.append(e)
                self.by_name[e.name] = e

    @staticmethod
    def _parse_desc(text):
        e = DbEntry()
        cur = None
        multi = {"%LICENSE%", "%GROUPS%", "%DEPENDS%", "%MAKEDEPENDS%",
                 "%CHECKDEPENDS%", "%OPTDEPENDS%", "%PROVIDES%",
                 "%CONFLICTS%", "%REPLACES%"}
        for raw in text.splitlines():
            s = raw.strip()
            if s.startswith("%") and s.endswith("%") and len(s) > 2:
                cur = s.upper()
                continue
            if not s or cur is None:
                continue
            if cur == "%NAME%":
                e.name = s
            elif cur == "%BASE%":
                e.base = s
            elif cur == "%VERSION%":
                e.version = s
            elif cur == "%ARCH%":
                e.arch = s
            elif cur == "%DESC%":
                e.desc = f"{e.desc} {s}".strip() if e.desc else s
            elif cur == "%URL%":
                e.url = s
            elif cur == "%PACKAGER%":
                e.packager = s
            elif cur == "%BUILDDATE%":
                e.builddate = s
            elif cur == "%FILENAME%":
                e.filename = s
            elif cur == "%CSIZE%":
                try:
                    e.csize = int(s)
                except ValueError:
                    pass
            elif cur == "%ISIZE%":
                try:
                    e.isize = int(s)
                except ValueError:
                    pass
            elif cur in multi:
                getattr(e, {
                    "%LICENSE%": "license", "%GROUPS%": "groups",
                    "%DEPENDS%": "depends", "%MAKEDEPENDS%": "makedepends",
                    "%CHECKDEPENDS%": "checkdepends",
                    "%OPTDEPENDS%": "optdepends", "%PROVIDES%": "provides",
                    "%CONFLICTS%": "conflicts", "%REPLACES%": "replaces",
                }[cur]).append(s)
        return e

    def _scan_disk(self):
        try:
            names = sorted(os.listdir(self.repo_dir))
        except OSError as ex:
            self.error = self.error or str(ex)
            names = []
        by_fn = {e.filename: e for e in self.entries if e.filename}
        groups = {}
        for fn in names:
            if not fn.endswith(".pkg.tar.zst"):
                continue
            path = os.path.join(self.repo_dir, fn)
            entry = by_fn.get(fn)
            sp = split_pkg_filename(fn)
            name = entry.name if entry else (sp[0] if sp else None)
            rec = {
                "file": fn,
                "path": path,
                "sig": os.path.exists(path + ".sig"),
                "entry": entry,
                "name": name,
                "split": sp,
            }
            self.files[fn] = rec
            if name:
                groups.setdefault(name, []).append(rec)
        self.duplicates = {k: v for k, v in groups.items() if len(v) > 1}
        self.orphans = [
            r["file"] for r in self.files.values() if r["entry"] is None
        ]
        self.stale = [
            e.name for e in self.entries
            if e.filename and e.filename not in self.files
        ]

    def records_named(self, name):
        return [r for r in self.files.values() if r["name"] == name]

    def local_version(self, name):
        best = None
        for r in self.records_named(name):
            sp = r.get("split")
            if not sp:
                continue
            v = sp[1]
            if best is None or vercmp(v, best) > 0:
                best = v
        return best

    def latest_record(self, name):
        recs = self.records_named(name)
        if not recs:
            return None

        def key(r):
            return r["split"][1] if r.get("split") else ""

        best = recs[0]
        for r in recs[1:]:
            if vercmp(key(r), key(best)) > 0:
                best = r
        return best

    def unsigned_files(self):
        return sorted(
            r["path"] for r in self.files.values() if not r["sig"]
        )

    def all_known_names(self):
        names = set(self.by_name.keys())
        for r in self.files.values():
            if r["name"]:
                names.add(r["name"])
        return names
