#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
AURPKGS_FILE="$REPO_ROOT/AURpkgs"
REPO_DIR="$REPO_ROOT/x86_64"
BUILD_DIR="$SCRIPT_DIR/build"

mkdir -p "$BUILD_DIR"

if [ ! -f "$AURPKGS_FILE" ]; then
    echo "Error: AURpkgs file not found at $AURPKGS_FILE"
    exit 1
fi

cd "$BUILD_DIR" || exit 1

while read -r pkg; do
    pkg=$(echo "$pkg" | xargs)
    
    if [ -z "$pkg" ] || [[ "$pkg" == \#* ]]; then
        continue
    fi
    
    echo "========================================="
    echo "Building package: $pkg"
    echo "========================================="
    
    if [ -d "$pkg" ]; then
        echo "Removing existing $pkg directory..."
        rm -rf "$pkg"
    fi
    
    echo "Cloning $pkg from AUR..."
    git clone "https://aur.archlinux.org/$pkg.git" || {
        echo "Failed to clone $pkg"
        continue
    }
    
    cd "$pkg" || continue
    
    echo "Building $pkg..."
    makepkg -s --noconfirm || {
        echo "Failed to build $pkg"
        cd "$BUILD_DIR" || exit 1
        continue
    }
    
    echo "Moving built packages to repository..."
    mv *.pkg.tar.zst "$REPO_DIR/" 2>/dev/null || {
        echo "No package files found for $pkg"
        cd "$BUILD_DIR" || exit 1
        continue
    }
    
    cd "$BUILD_DIR" || exit 1
    
    echo "Successfully built and added $pkg"
    echo
done < "$AURPKGS_FILE"

echo "========================================="
echo "All AUR packages built successfully!"
echo "========================================="
