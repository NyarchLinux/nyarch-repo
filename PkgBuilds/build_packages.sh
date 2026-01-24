#!/bin/bash

PKGBUILDS_DIR="."
REPO_DIR="../x86_64"

build_package() {
    local pkg_name=$1
    local pkg_dir="$PKGBUILDS_DIR/$pkg_name"
    
    if [[ ! -d "$pkg_dir" ]]; then
        echo "Error: Package '$pkg_name' not found in $PKGBUILDS_DIR"
        return 1
    fi
    
    echo "Building $pkg_name..."
    pushd "$pkg_dir" > /dev/null || return 1
    
    if makepkg -s; then
        echo "Successfully built $pkg_name"
        mv *.pkg.tar.zst "../x86_64/" 2>/dev/null || true
        popd > /dev/null
        return 0
    else
        echo "Failed to build $pkg_name"
        popd > /dev/null
        return 1
    fi
}

if [[ $# -gt 0 ]]; then
    for pkg in "$@"; do
        build_package "$pkg"
    done
else
    for pkg_dir in "$PKGBUILDS_DIR"/*/; do
        if [[ -d "$pkg_dir" ]]; then
            pkg_name=$(basename "$pkg_dir")
            build_package "$pkg_name"
        fi
    done
fi

echo "Updating repository database..."
pushd "../x86_64" > /dev/null || exit 1
bash update_repo.sh
popd > /dev/null

echo "Done!"
