#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_BUILDS_SCRIPT="$SCRIPT_DIR/PkgBuilds/build_pkgs.sh"
AUR_BUILDER_SCRIPT="$SCRIPT_DIR/AURBuilder/build_aur.sh"
REPO_DIR="$SCRIPT_DIR/x86_64"
PKG_BUILDS_OUTPUT="$SCRIPT_DIR/PkgBuilds/output"

echo "========================================="
echo "Starting full build process"
echo "========================================="
echo

echo "Step 1: Building local packages..."
echo "-----------------------------------"
bash "$PKG_BUILDS_SCRIPT"
echo

echo "Step 2: Finding all AUR dependencies..."
echo "-----------------------------------------"
python3 "$SCRIPT_DIR/get_aur_deps.py"
echo

echo "Step 3: Building AUR packages..."
echo "-----------------------------------"
bash "$AUR_BUILDER_SCRIPT"
echo

echo "Step 3: Moving local packages to repository..."
echo "-----------------------------------------------"
if [ -d "$PKG_BUILDS_OUTPUT" ] && [ -n "$(ls -A $PKG_BUILDS_OUTPUT/*.pkg.tar.* 2>/dev/null)" ]; then
    mv "$PKG_BUILDS_OUTPUT"/*.pkg.tar.* "$REPO_DIR/"
    echo "✓ Local packages moved to repository"
else
    echo "No local packages to move"
fi
echo

echo "Step 4: Updating repository database..."
echo "---------------------------------------"
cd "$REPO_DIR" || exit 1
bash update_repo.sh
echo

echo "========================================="
echo "Build process completed successfully!"
echo "Packages are in: $REPO_DIR"
echo "========================================="
