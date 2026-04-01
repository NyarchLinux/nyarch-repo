#!/bin/bash

REPO_DIR="$(cd "$(dirname "$0")/x86_64" && pwd)"

unsigned=()

for pkg in "$REPO_DIR"/*.pkg.tar.zst; do
    [ -f "$pkg" ] || continue
    [ -f "${pkg}.sig" ] || unsigned+=("$pkg")
done

if [ ${#unsigned[@]} -eq 0 ]; then
    echo "All packages are already signed."
    exit 0
fi

echo "Found ${#unsigned[@]} unsigned package(s):"
printf '  %s\n' "${unsigned[@]}"
echo

for pkg in "${unsigned[@]}"; do
    echo "Signing $(basename "$pkg")..."
    gpg --detach-sign --use-agent "$pkg"
done

echo
echo "Done. Signed ${#unsigned[@]} package(s)."
