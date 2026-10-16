#!/usr/bin/env python3
"""Explicitly update the pinned upstream snapshot; never run at container startup."""

import argparse
import json
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = "https://github.com/dmunozv04/iSponsorBlockTV"


def git(*args, cwd=None):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ref", help="Exact upstream tag or commit to vendor")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="isbtv-upstream-") as directory:
        checkout = Path(directory) / "upstream"
        subprocess.run(["git", "clone", "--quiet", REPOSITORY + ".git", str(checkout)], check=True)
        commit = git("rev-parse", "--verify", "--end-of-options", args.ref + "^{commit}", cwd=checkout)
        subprocess.run(["git", "checkout", "--quiet", "--detach", commit], cwd=checkout, check=True)
        version = tomllib.loads((checkout / "pyproject.toml").read_text())["project"]["version"]
        destination = ROOT / "vendor" / "upstream"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(checkout / "src", destination)
        shutil.copy2(checkout / "requirements.txt", ROOT / "vendor" / "requirements.txt")
        shutil.copy2(checkout / "LICENSE.md", ROOT / "vendor" / "LICENSE.md")
        shutil.copy2(checkout / "LICENSE.md", ROOT / "LICENSE")
        metadata = {
            "repository": REPOSITORY,
            "version": version,
            "commit": commit,
            "license": "GPL-3.0",
            "vendored_directory": "vendor/upstream",
        }
        (ROOT / "upstream.json").write_text(json.dumps(metadata, indent=2) + "\n")
        print(f"Pinned iSponsorBlockTV {version} at {commit}")
        print("Review dependency changes, update the web adapter, run tests, then commit.")


if __name__ == "__main__":
    main()
