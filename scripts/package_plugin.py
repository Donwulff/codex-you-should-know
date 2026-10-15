#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

FILES = (
    ".agents/plugins/marketplace.json",
    ".codex-plugin/plugin.json",
    "hooks/hooks.json",
    "resources/finding.schema.json",
    "resources/confirmation.schema.json",
    "scripts/you_should_know.py",
    "skills/you-should-know/SKILL.md",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
)
MARKER = ".you-should-know-package"


def reject_symlinks(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f"Refusing symlink path: {part}")


def build_package(source: Path, destination: Path) -> Path:
    source = source.absolute()
    destination = destination.absolute()
    reject_symlinks(source)
    reject_symlinks(destination)
    if destination == source or destination in source.parents:
        raise ValueError("Package output cannot replace the source or its parents")
    marker = json.dumps({"package": "you-should-know", "source": str(source)}) + "\n"
    if destination.exists():
        ownership = destination / MARKER
        if (
            not destination.is_dir()
            or ownership.is_symlink()
            or not ownership.is_file()
            or ownership.read_text(encoding="utf-8") != marker
        ):
            raise ValueError(f"Refusing to replace an unowned output directory: {destination}")
    files = [source / name for name in FILES]
    module_directory = source / "src/you_should_know"
    reject_symlinks(module_directory)
    modules = sorted(module_directory.glob("*.py"))
    if not modules:
        raise ValueError("Package source has no Python modules")
    files.extend(modules)
    for path in files:
        reject_symlinks(path)
        if not path.is_file():
            raise ValueError(f"Missing package file: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ysk-build-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "package"
        staged.mkdir()
        for path in files:
            target = staged / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        (staged / MARKER).write_text(marker, encoding="utf-8")
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(staged, destination)
    return destination


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Build a native Codex plugin without local state.")
    parser.add_argument("--output", type=Path, default=root / "dist/you-should-know")
    args = parser.parse_args()
    try:
        destination = build_package(root, args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(destination)


if __name__ == "__main__":
    main()
