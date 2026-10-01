import gzip
import hashlib
import os
import subprocess


def gpg_verify(pkg_path, sig_path):
    res = {
        "present": os.path.exists(sig_path),
        "ok": False,
        "key_id": "",
        "key_uid": "",
        "fingerprint": "",
        "detail": "",
    }
    if not res["present"]:
        res["detail"] = "signature file missing"
        return res
    try:
        p = subprocess.run(
            ["gpg", "--with-colons", "--status-fd", "1",
             "--verify", sig_path, pkg_path],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as e:
        res["detail"] = f"gpg failed: {e}"
        return res
    uid = ""
    for line in p.stdout.splitlines():
        if not line.startswith("[GNUPG:]"):
            continue
        parts = line.split()
        tag = parts[1] if len(parts) > 1 else ""
        if tag == "GOODSIG":
            res["key_id"] = parts[2] if len(parts) > 2 else ""
            uid = " ".join(parts[3:])
        elif tag == "BADSIG" and len(parts) > 2:
            res["key_id"] = parts[2]
            res["detail"] = (
                "BAD signature: file content does not match signature"
            )
        elif tag == "VALIDSIG" and len(parts) > 2:
            res["fingerprint"] = parts[2]
        elif tag == "ERRSIG" and len(parts) > 2:
            res["key_id"] = parts[2]
            reason = parts[-1] if parts else "?"
            hints = {
                "1": "key not available",
                "2": "unsupported algorithm",
                "3": "corrupted signature",
                "4": "expired key",
                "5": "revoked key",
            }
            res["detail"] = "cannot verify: " + hints.get(reason, "unknown error")
        elif tag == "EXPKEYSIG":
            uid = " ".join(parts[3:])
            res["detail"] = "signed with an expired key"
        elif tag == "REVKEYSIG":
            uid = " ".join(parts[3:])
            res["detail"] = "good signature from a revoked key"
    res["key_uid"] = uid
    if p.returncode == 0:
        res["ok"] = True
        res["detail"] = f"valid signature from {uid}" if uid else "valid signature"
    elif not res["detail"]:
        res["detail"] = "invalid signature"
    return res


def _norm_member(name):
    n = name.replace("\\", "/")
    while n.startswith("./"):
        n = n[2:]
    if n.startswith("/"):
        n = n.lstrip("/")
    return n


def tar_member_hashes(pkg_path, capture_names=None):
    """Stream the raw uncompressed tar stream of a package (via `zstd -dc`),
    hashing every regular member and optionally capturing raw bytes of named
    members (.MTREE)."""
    capture_names = set(capture_names or [])
    hashes = {}
    captured = {}
    proc = subprocess.Popen(
        ["zstd", "-dc", pkg_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=512 * 1024,
    )

    def parse_header(block):
        name = block[0:100].split(b"\0", 1)[0].decode("utf-8", "replace")
        prefix = block[345:500].split(b"\0", 1)[0].decode("utf-8", "replace")
        if prefix:
            name = prefix + "/" + name
        link = block[157:257].split(b"\0", 1)[0].decode("utf-8", "replace")
        try:
            size = int(block[124:136].rstrip(b"\0 ") or b"0", 8)
        except ValueError:
            size = 0
        typ = block[156:157]
        return _norm_member(name), size, typ, _norm_member(link)

    buf = bytearray()
    state = "header"
    saw_zeros = 0
    cur_name = None
    cur_size = 0
    cur_actual = 0
    cur_taken = 0
    cur_hash = None
    cur_capture = False
    skip_remaining = 0
    hardlinks = []

    try:
        while state != "done":
            chunk = proc.stdout.read(256 * 1024)
            if not chunk:
                break
            buf += chunk
            progressed = True
            while progressed and state != "done":
                progressed = False
                if state == "header":
                    if len(buf) < 512:
                        break
                    block = bytes(buf[:512])
                    del buf[:512]
                    progressed = True
                    if not block.strip(b"\0"):
                        saw_zeros += 1
                        if saw_zeros >= 2:
                            state = "done"
                        continue
                    saw_zeros = 0
                    name, size, typ, link = parse_header(block)
                    padded = (size + 511) // 512 * 512
                    if typ == b"1":
                        hardlinks.append((name, link))
                    elif typ in (b"\x00", b"0"):
                        if size > 0:
                            state = "content"
                            cur_name, cur_size = name, size
                            cur_actual, cur_taken = 0, 0
                            cur_hash = hashlib.sha256()
                            cur_capture = (
                                name in capture_names and name not in captured
                            )
                            if cur_capture:
                                captured[name] = bytearray()
                        else:
                            hashes[name] = hashlib.sha256(b"").hexdigest()
                    elif padded > 0:
                        state = "skip"
                        skip_remaining = padded
                elif state == "content":
                    padded_total = (cur_size + 511) // 512 * 512
                    take = min(len(buf), padded_total - cur_taken)
                    if take == 0:
                        break
                    data = bytes(buf[:take])
                    del buf[:take]
                    progressed = True
                    actual = min(take, cur_size - cur_actual)
                    if actual > 0:
                        part = data[:actual]
                        cur_hash.update(part)
                        if cur_capture:
                            captured[cur_name] += part
                        cur_actual += actual
                    cur_taken += take
                    if cur_taken >= padded_total:
                        hashes[cur_name] = cur_hash.hexdigest()
                        state = "header"
                    else:
                        break
                elif state == "skip":
                    take = min(len(buf), skip_remaining)
                    if take == 0:
                        break
                    del buf[:take]
                    skip_remaining -= take
                    progressed = True
                    if skip_remaining == 0:
                        state = "header"
        rc = proc.wait(timeout=300)
        if rc != 0:
            err = ""
            try:
                err = proc.stderr.read().decode("utf-8", "replace").strip()
            except Exception:
                pass
            raise RuntimeError(f"zstd exited with status {rc}: {err[-300:]}")
        for name, target in hardlinks:
            if target in hashes:
                hashes[name] = hashes[target]
    finally:
        if proc.poll() is None:
            proc.kill()
        for s in (proc.stdout, proc.stderr):
            try:
                s.close()
            except Exception:
                pass
    return hashes, captured


def parse_mtree(data):
    try:
        text = gzip.decompress(bytes(data)).decode("utf-8", "replace")
    except OSError:
        text = bytes(data).decode("utf-8", "replace")
    entries = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if not parts:
            continue
        kw = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                kw[k] = v
        entries[parts[0]] = kw
    return entries


def verify_mtree(mtree_entries, hashes):
    issues = []
    checked = 0
    for path, kw in mtree_entries.items():
        if "sha256digest" not in kw:
            continue
        np = _norm_member(path)
        h = hashes.get(np)
        if h is None:
            issues.append(f"{np}: listed in .MTREE but missing from archive")
        elif h.lower() != kw["sha256digest"].lower():
            issues.append(f"{np}: checksum mismatch")
        checked += 1
    return checked, issues


SEV_RANK = {"ok": 0, "warn": 1, "fail": 2}


def full_check(repo_db, progress=None):
    results = []
    files = sorted(repo_db.files.values(), key=lambda r: r["file"])
    total = len(files)
    for i, rec in enumerate(files):
        if progress:
            progress(i, total, rec["file"])
        path = rec["path"]
        item = {
            "file": rec["file"],
            "name": rec["name"],
            "severity": "ok",
            "messages": [],
            "gpg": None,
            "mtree_checked": 0,
            "mtree_issues": [],
            "readable": True,
        }

        def bump(level, msg):
            item["severity"] = max(
                item["severity"], level, key=lambda x: SEV_RANK[x]
            )
            item["messages"].append(msg)

        gv = gpg_verify(path, path + ".sig")
        item["gpg"] = gv
        if not gv["present"]:
            bump("warn", "package is unsigned")
        elif not gv["ok"]:
            bump("fail", gv["detail"])
        try:
            hashes, captured = tar_member_hashes(path, capture_names=[".MTREE"])
        except Exception as e:
            item["readable"] = False
            bump("fail", f"archive unreadable: {e}")
            results.append(item)
            continue
        if ".PKGINFO" not in hashes:
            bump("warn", ".PKGINFO metadata missing")
        if ".MTREE" in captured:
            mtree = parse_mtree(captured[".MTREE"])
            checked, issues = verify_mtree(mtree, hashes)
            item["mtree_checked"] = checked
            item["mtree_issues"] = issues
            if issues:
                bump(
                    "warn",
                    f".MTREE: {len(issues)} mismatch(es): "
                    + "; ".join(issues[:3])
                    + ("…" if len(issues) > 3 else ""),
                )
        else:
            bump("warn", ".MTREE manifest missing")
        results.append(item)
    if progress:
        progress(total, total, "")
    return results
