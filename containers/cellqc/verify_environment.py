"""Verify installed conda archive identities against an explicit environment lock."""

import json
import sys
from pathlib import Path


def verify(prefix: Path, lock: Path) -> None:
    expected = {tuple(line.rsplit("#", 1)) for line in lock.read_text().splitlines() if line.startswith("https://")}
    if not expected:
        raise ValueError("environment lock contains no package archives")
    actual = set()
    for record in (prefix / "conda-meta").glob("*.json"):
        package = json.loads(record.read_text())
        actual.add((package["url"], package["sha256"]))
    if actual != expected:
        raise ValueError(
            f"environment differs from lock: {len(expected - actual)} missing, "
            f"{len(actual - expected)} unexpected archives"
        )
    print(f"Verified {len(expected)} exact conda package archives in {prefix}")


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
