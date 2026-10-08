"""Validates that all version declarations across Python and Desktop match."""
import json
import re
import sys
import tomllib
from pathlib import Path


def get_versions(root: Path) -> dict[str, str | None]:
    # 1. pyproject.toml
    pyproject_file = root / "pyproject.toml"
    v_pyproject = None
    if pyproject_file.exists():
        pyproject = tomllib.loads(pyproject_file.read_text(encoding="utf-8"))
        v_pyproject = pyproject.get("project", {}).get("version")

    # 2. __init__.py
    init_file = root / "src" / "nas_air_intelligence" / "__init__.py"
    v_init = None
    if init_file.exists():
        init_text = init_file.read_text(encoding="utf-8")
        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init_text)
        if m:
            v_init = m.group(1)

    # 3. desktop/package.json
    pkg_file = root / "desktop" / "package.json"
    v_pkg = None
    if pkg_file.exists():
        pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
        v_pkg = pkg.get("version")

    # 4. Cargo.toml
    cargo_file = root / "desktop" / "src-tauri" / "Cargo.toml"
    v_cargo = None
    if cargo_file.exists():
        cargo_text = cargo_file.read_text(encoding="utf-8")
        m_cargo = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', cargo_text, re.MULTILINE)
        if m_cargo:
            v_cargo = m_cargo.group(1)

    # 5. tauri.conf.json
    tauri_conf_file = root / "desktop" / "src-tauri" / "tauri.conf.json"
    v_tauri = None
    if tauri_conf_file.exists():
        tauri_conf = json.loads(tauri_conf_file.read_text(encoding="utf-8"))
        v_tauri = tauri_conf.get("version")

    return {
        "pyproject.toml": v_pyproject,
        "src/nas_air_intelligence/__init__.py": v_init,
        "desktop/package.json": v_pkg,
        "desktop/src-tauri/Cargo.toml": v_cargo,
        "desktop/src-tauri/tauri.conf.json": v_tauri,
    }


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    versions = get_versions(root)

    unique = set(versions.values())
    if len(unique) != 1 or None in unique:
        print("[FAIL] Mismatched versions detected across project files:")
        for file, ver in versions.items():
            print(f"  - {file}: {ver}")
        return 1

    common_ver = next(iter(unique))
    print(f"[PASS] All 5 project version declarations are synchronized at {common_ver}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
