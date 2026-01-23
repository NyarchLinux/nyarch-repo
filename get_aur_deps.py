#!/usr/bin/env python3

import requests
import re
from typing import Set, List

AUR_RPC_URL = "https://aur.archlinux.org/rpc/v5"

def get_aur_package_info(pkg_names: List[str]) -> dict:
    """Get package information from AUR RPC API"""
    import time
    max_retries = 3
    for attempt in range(max_retries):
        try:
            time.sleep(0.2)
            response = requests.get(f"{AUR_RPC_URL}/info", params={"arg[]": pkg_names}, timeout=10)
            response.raise_for_status()
            data = response.json()
            return {pkg["Name"]: pkg for pkg in data.get("results", [])}
        except requests.RequestException as e:
            print(f"  Error fetching AUR package info (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
    return {}

def clean_dep_name(dep: str) -> str:
    """Remove version constraints and operators from dependency name"""
    return re.sub(r'[<>=!].*$', '', dep).strip()

def get_all_deps(pkg_name: str, visited: Set[str] | None = None) -> Set[str]:
    """Recursively get all AUR dependencies for a package"""
    if visited is None:
        visited = set()
    
    if pkg_name in visited:
        return visited
    
    print(f"Checking: {pkg_name}")
    
    pkg_info = get_aur_package_info([pkg_name])
    if pkg_name not in pkg_info:
        return visited
    
    visited.add(pkg_name)
    
    pkg_data = pkg_info[pkg_name]
    
    all_deps = []
    for dep_type in ["Depends", "MakeDepends", "CheckDepends"]:
        deps = pkg_data.get(dep_type, [])
        if deps:
            all_deps.extend(deps)
    
    for dep in all_deps:
        clean_name = clean_dep_name(dep)
        if clean_name not in visited:
            dep_info = get_aur_package_info([clean_name])
            if clean_name in dep_info:
                get_all_deps(clean_name, visited)
    
    return visited

def read_aurpkgs(filename: str) -> List[str]:
    """Read AURpkgs file and return list of package names"""
    packages = []
    with open(filename, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                packages.append(line)
    return packages

def write_aurpkgs_full(packages: List[str], filename: str):
    """Write package list to AURpkgs_full"""
    with open(filename, 'w') as f:
        for pkg in sorted(packages):
            f.write(f"{pkg}\n")

def main():
    input_file = "AURpkgs"
    output_file = "AURpkgs_full"
    
    print(f"Reading packages from {input_file}...")
    initial_packages = read_aurpkgs(input_file)
    print(f"Found {len(initial_packages)} packages\n")
    
    print("Finding all AUR dependencies...\n")
    all_aur_deps = set()
    
    for pkg in initial_packages:
        get_all_deps(pkg, all_aur_deps)
    
    all_aur_deps = sorted(all_aur_deps)
    
    print(f"\nTotal AUR packages (including dependencies): {len(all_aur_deps)}")
    print(f"Writing to {output_file}...")
    write_aurpkgs_full(all_aur_deps, output_file)
    print("Done!")

if __name__ == "__main__":
    main()
