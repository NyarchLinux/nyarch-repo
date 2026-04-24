#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
AURPKGS_FILE="$REPO_ROOT/AURpkgs_full"
REPO_DIR="$REPO_ROOT/x86_64"

if [ ! -f "$AURPKGS_FILE" ]; then
    echo "Error: AURpkgs_full file not found at $AURPKGS_FILE"
    exit 1
fi

if [ ! -d "$REPO_DIR" ]; then
    echo "Error: Repository directory not found at $REPO_DIR"
    exit 1
fi

packages=()
while read -r pkg; do
    pkg=$(echo "$pkg" | xargs)
    if [ -z "$pkg" ] || [[ "$pkg" == \#* ]]; then
        continue
    fi
    packages+=("$pkg")
done < "$AURPKGS_FILE"

aur_json=$(mktemp)
trap 'rm -f "$aur_json"' EXIT

batch_size=100
for ((i = 0; i < ${#packages[@]}; i += batch_size)); do
    batch=("${packages[@]:i:batch_size}")
    query=""
    for pkg in "${batch[@]}"; do
        query+="&arg[]=$pkg"
    done
    curl -s "https://aur.archlinux.org/rpc/v5/info?${query:1}" >> "$aur_json"
done

echo "Checking AUR package updates..."
echo "================================="
printf "%-30s %-20s %-20s %s\n" "PACKAGE" "LOCAL" "AUR" "STATUS"
printf "%-30s %-20s %-20s %s\n" "-------" "-----" "---" "------"

REPO_DIR_ESC="${REPO_DIR//\"/\\\"}"

pkg_list_file=$(mktemp)
trap 'rm -f "$aur_json" "$pkg_list_file"' EXIT
printf '%s\n' "${packages[@]}" > "$pkg_list_file"

result=$(python3 -c "
import json, subprocess, os, glob, sys

with open('$aur_json') as f:
    raw = f.read()

aur_versions = {}
try:
    all_data = json.loads(raw)
    aur_versions = {pkg['Name']: pkg.get('Version', '') for pkg in all_data.get('results', [])}
except json.JSONDecodeError:
    pass

with open('$pkg_list_file') as f:
    packages = [line.strip() for line in f if line.strip()]

repo_dir = '$REPO_DIR_ESC'

updates_needed = 0
missing_local = 0

for pkg in packages:
    pkg_files = sorted(glob.glob(os.path.join(repo_dir, pkg + '-[0-9]*.pkg.tar.zst')))
    pkg_files = [f for f in pkg_files if 'debug' not in os.path.basename(f)]
    local_file = pkg_files[-1] if pkg_files else None

    if local_file:
        base = os.path.basename(local_file)
        trimmed = base[len(pkg)+1:]
        for suffix in ['-x86_64.pkg.tar.zst', '-any.pkg.tar.zst', '.pkg.tar.zst']:
            if trimmed.endswith(suffix):
                trimmed = trimmed[:-len(suffix)]
                break
        local_ver = trimmed
    else:
        local_ver = ''

    aur_ver = aur_versions.get(pkg, '')

    if not local_ver:
        status = 'NEEDS BUILD'
        missing_local += 1
        print(f'{pkg}|NOT BUILT|{aur_ver or \"UNKNOWN\"}|{status}')
    elif not aur_ver:
        status = 'FETCH ERROR'
        print(f'{pkg}|{local_ver}|UNKNOWN|{status}')
    else:
        r = subprocess.run(['vercmp', aur_ver, local_ver], capture_output=True, text=True)
        if int(r.stdout.strip()) > 0:
            status = 'UPDATE AVAILABLE'
            updates_needed += 1
            print(f'{pkg}|{local_ver}|{aur_ver}|{status}')
        else:
            status = 'up to date'
            print(f'{pkg}|{local_ver}|{aur_ver}|{status}')

print(f'SUMMARY|{updates_needed}|{missing_local}')
")

updates_needed=0
missing_local=0

while IFS='|' read -r pkg local aur status; do
    if [ "$pkg" = "SUMMARY" ]; then
        updates_needed="$local"
        missing_local="$aur"
        continue
    fi
    printf "%-30s %-20s %-20s %s\n" "$pkg" "$local" "$aur" "$status"
done <<< "$result"

echo ""
echo "================================="
echo "Summary: $updates_needed package(s) need updating, $missing_local package(s) not yet built"
