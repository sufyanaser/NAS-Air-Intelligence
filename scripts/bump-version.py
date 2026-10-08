"""Safely bumps version across all 5 project declarations and verifies alignment."""
import json
import re
import sys
from pathlib import Path

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def bump_version(new_ver: str) -> None:
    if not SEMVER_RE.match(new_ver):
        raise ValueError(f"Invalid semver version: {new_ver}")

    root = Path(__file__).resolve().parent.parent

    # 1. pyproject.toml
    pyproject_file = root / "pyproject.toml"
    content = pyproject_file.read_text(encoding="utf-8")
    content = re.sub(
        r'(^\[project\][\s\S]*?^version\s*=\s*)["\'][^"\']+["\']',
        rf'\g<1>"{new_ver}"',
        content,
        flags=re.MULTILINE,
    )
    pyproject_file.write_text(content, encoding="utf-8")

    # 2. __init__.py
    init_file = root / "src" / "nas_air_intelligence" / "__init__.py"
    content = init_file.read_text(encoding="utf-8")
    content = re.sub(
        r'__version__\s*=\s*["\'][^"\']+["\']',
        f'__version__ = "{new_ver}"',
        content,
    )
    init_file.write_text(content, encoding="utf-8")

    # 3. desktop/package.json
    pkg_file = root / "desktop" / "package.json"
    pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
    pkg["version"] = new_ver
    pkg_file.write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")

    # 4. Cargo.toml
    cargo_file = root / "desktop" / "src-tauri" / "Cargo.toml"
    content = cargo_file.read_text(encoding="utf-8")
    content = re.sub(
        r'(^\[package\][\s\S]*?^version\s*=\s*)["\'][^"\']+["\']',
        rf'\g<1>"{new_ver}"',
        content,
        flags=re.MULTILINE,
    )
    cargo_file.write_text(content, encoding="utf-8")

    # 5. tauri.conf.json
    tauri_conf_file = root / "desktop" / "src-tauri" / "tauri.conf.json"
    tauri_conf = json.loads(tauri_conf_file.read_text(encoding="utf-8"))
    tauri_conf["version"] = new_ver
    tauri_conf_file.write_text(json.dumps(tauri_conf, indent=2) + "\n", encoding="utf-8")

    print(f"Successfully bumped all files to version {new_ver}")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/bump-version.py <new_version>")
        return 1

    new_ver = sys.argv[1].strip().lstrip("v")
    try:
        bump_version(new_ver)
    except Exception as exc:
        print(f"Error bumping version: {exc}")
        return 1

    # Verify alignment
    import importlib.util

    check_script = Path(__file__).resolve().parent / "check-versions.py"
    spec = importlib.util.spec_from_file_location("check_versions", check_script)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.main()

    return 0


if __name__ == "__main__":
    sys.exit(main())
