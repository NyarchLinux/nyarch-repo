#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/output"

mkdir -p "$OUTPUT_DIR"

echo "Starting package build process..."
echo "Output directory: $OUTPUT_DIR"
echo

for pkg_dir in "$SCRIPT_DIR"/*/; do
	if [[ ! -d "$pkg_dir" ]]; then
		continue
	fi

	pkg_name=$(basename "$pkg_dir")
	pkgbuild="$pkg_dir/PKGBUILD"

	if [[ ! -f "$pkgbuild" ]]; then
		echo "Skipping $pkg_name (no PKGBUILD found)"
		continue
	fi

	echo "Building $pkg_name..."
	cd "$pkg_dir"

	if makepkg -sf; then
		pkg_files=("$pkg_name"*.pkg.tar.*)
		if [[ -f "${pkg_files[0]}" ]]; then
			mv "${pkg_files[@]}" "$OUTPUT_DIR/"
			echo "✓ $pkg_name built successfully"
		else
			echo "✗ $pkg_name build completed but no package file found"
		fi
	else
		echo "✗ $pkg_name failed to build"
	fi

	echo
done

cd "$SCRIPT_DIR"

echo "Build process completed."
echo "Built packages are available in: $OUTPUT_DIR"
ls -lh "$OUTPUT_DIR"/*.pkg.tar.* 2>/dev/null || echo "No packages built."
