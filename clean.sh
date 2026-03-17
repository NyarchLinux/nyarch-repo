#!/bin/bash

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================="
echo "Cleaning untracked files in PkgBuilds..."
echo "========================================="

# -f: force
# -d: remove untracked directories as well
# PkgBuilds: limit cleaning to this directory
git -C "$SCRIPT_DIR" clean -fd PkgBuilds

echo
echo "Done! PkgBuilds is now clean of untracked files."
echo "========================================="
