"""Build a source ZIP from a clean Git commit; no runtime files can enter it."""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist")
    args = parser.parse_args()
    if git("status", "--porcelain").strip():
        parser.error("Commit or remove untracked source changes before packaging")
    commit = git("rev-parse", "HEAD").decode().strip()
    destination = (ROOT / args.output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"observable-ai-pipeline-{commit[:12]}.zip"
    subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "archive",
            "--format=zip",
            "--prefix=observable-ai-pipeline/",
            "-o",
            str(archive),
            commit,
        ],
        check=True,
    )
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for name in z.namelist():
            assert not {".venv", ".local", ".git", "node_modules", ".env"}.intersection(
                Path(name).parts
            ), name
    result = {
        "commit": commit,
        "archive": archive.name,
        "bytes": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    archive.with_suffix(".manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
