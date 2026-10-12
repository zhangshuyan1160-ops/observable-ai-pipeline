"""Report or remove ONLY named, rebuildable project-local files; never delete DB/source."""

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = [
    ".local/pip-cache",
    ".local/pytest",
    ".local/archive-check",
    ".pytest_cache",
    ".ruff_cache",
    "build",
]


def size(path):
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    return (
        sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())
        if path.exists()
        else 0
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Default is dry-run")
    parser.add_argument(
        "--dependencies",
        action="store_true",
        help="Also remove .venv after supervisor is stopped; bootstrap restores it",
    )
    args = parser.parse_args()
    if args.dependencies and (ROOT / ".local/runtime.json").exists():
        parser.error("Stop scripts/dev.py before removing .venv")
    total = 0
    for name in CANDIDATES + ([".venv"] if args.dependencies else []):
        path = ROOT / name
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            parser.error("Refusing symlink/outside path: " + name)
        if path.exists() and any(path.rglob("postmaster.pid")):
            parser.error("PostgreSQL may still be running inside: " + name)
        count = size(path)
        total += count
        print(f"{name}: {count / 1024**2:.2f} MiB")
        if args.apply and path.exists():
            shutil.rmtree(path)
    print(
        f"{'Removed' if args.apply else 'Would remove'} {total / 1024**2:.2f} MiB. Source, archives, PostgreSQL and artifacts are preserved."
    )


if __name__ == "__main__":
    main()
