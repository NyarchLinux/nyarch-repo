import re
import subprocess
import time

import requests

RPC_URL = "https://aur.archlinux.org/rpc/v5"
DEP_CLEAN_RE = re.compile(r"[<>=!].*$")


def clean_dep(dep):
    return DEP_CLEAN_RE.sub("", dep).strip()


def vercmp(a, b):
    if a == b:
        return 0
    try:
        r = subprocess.run(
            ["vercmp", str(a), str(b)], capture_output=True, text=True, timeout=10
        )
        return int((r.stdout or "0").strip())
    except Exception:
        return 0


class AurClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "nyarch-repo-manager/1.0"})

    def info(self, names):
        names = list(dict.fromkeys(n for n in names if n))
        out = {}
        for i in range(0, len(names), 100):
            batch = names[i : i + 100]
            last_exc = None
            for attempt in range(3):
                try:
                    r = self.session.get(
                        RPC_URL + "/info", params={"arg[]": batch}, timeout=20
                    )
                    r.raise_for_status()
                    for p in r.json().get("results", []):
                        out[p["Name"]] = p
                    last_exc = None
                    break
                except Exception as e:
                    last_exc = e
                    time.sleep(0.8 * (attempt + 1))
            if last_exc is not None:
                raise last_exc
        return out

    def info_one(self, name):
        return self.info([name]).get(name)

    def providers(self, name):
        response = self.session.get(
            RPC_URL + "/search/" + name, params={"by": "provides"}, timeout=20
        )
        response.raise_for_status()
        return sorted(response.json().get("results", []), key=lambda p: p["Name"])

    def resolve_closure(self, roots):
        visited = {}
        missing = []
        seen = set()
        frontier = []
        for r in roots:
            if r and r not in seen:
                seen.add(r)
                frontier.append(r)
        while frontier:
            got = self.info(frontier)
            nxt = []
            for name in frontier:
                rec = got.get(name)
                if rec is None:
                    if name not in missing:
                        missing.append(name)
                    continue
                visited[name] = rec
                deps = []
                for key in ("Depends", "MakeDepends", "CheckDepends"):
                    for d in rec.get(key) or []:
                        cd = clean_dep(d)
                        if cd and cd not in seen:
                            seen.add(cd)
                            deps.append(cd)
                nxt.extend(deps)
            frontier = nxt
        return visited, missing
