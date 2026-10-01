import re

import requests


def read_servers(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return []
    servers = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^Server\s*=\s*(.+)$", s)
        if m:
            servers.append(m.group(1).strip())
    return servers


def write_servers(path, servers):
    body = "# Nyarch Linux mirrorlist, managed by nyarch-repo-manager\n"
    for url in servers:
        body += f"Server = {url}\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    os_replace(tmp, path)


def os_replace(src, dst):
    import os

    os.replace(src, dst)


def check_mirror(url, arch="x86_64", timeout=6):
    probe = (
        url.replace("$arch", arch)
        .replace("$repo", "core")
        .rstrip("/")
        + "/nyarch-repo.db"
    )
    try:
        t0 = __import__("time").perf_counter()
        try:
            r = requests.head(probe, timeout=timeout, allow_redirects=True)
            if r.status_code >= 400:
                r = requests.get(probe, timeout=timeout, stream=True)
                r.close()
        except Exception:
            raise
        if r.status_code >= 400:
            return None, f"HTTP {r.status_code}"
        ms = (__import__("time").perf_counter() - t0) * 1000
        return ms, "ok"
    except Exception as e:
        return None, str(e)[:40] or "unreachable"
