"""Verify frozen wheel package contents, including their installed file hashes."""

import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import re
import sys
from pathlib import Path


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def verify():
    manifest = json.loads(Path(__file__).with_name("science-provenance.json").read_text())
    if sys.version_info[:3] != (3, 11, 15):
        raise ValueError("scientific Python differs from the deployed 3.11.15")
    expected = {canonical(package["name"]): package for package in manifest["packages"]}
    actual = {canonical(dist.metadata["Name"]): dist for dist in importlib.metadata.distributions()}
    allowed = {"pip", "setuptools", "wheel", "aiscientist-mcp-common", "aiscientist-mcp-scrna-analysis"}
    if set(actual) - set(expected) - allowed or set(expected) - set(actual):
        raise ValueError("scientific package closure differs from the verified manifest")
    files = 0
    for name, package in expected.items():
        dist = actual[name]
        if dist.version != package["version"]:
            raise ValueError(f"scientific package version differs: {name}")
        records = {
            path: digest
            for path, digest, *_ in csv.reader(io.StringIO(dist.read_text("RECORD")))
            if digest
            and "/__pycache__/" not in path
            and not path.startswith("../")
            and ".data/" not in path
            and not path.endswith(("/INSTALLER", "/REQUESTED", "/direct_url.json"))
        }
        record_hash = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
        if record_hash != package["package_record_sha256"]:
            raise ValueError(f"scientific wheel contents differ: {name}")
        for path, digest in records.items():
            algorithm, encoded = digest.split("=", 1)
            calculated = hashlib.new(algorithm, Path(dist.locate_file(path)).read_bytes()).digest()
            if base64.urlsafe_b64encode(calculated).decode().rstrip("=") != encoded:
                raise ValueError(f"installed scientific file differs: {name}/{path}")
            files += 1
    print(f"Verified Python 3.11.15, {len(expected)} exact scientific/worker packages and {files} installed files")


if __name__ == "__main__":
    verify()
