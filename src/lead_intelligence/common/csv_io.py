from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable


def read_csv(path: Path | str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def iter_csv(path: Path | str):
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        yield from csv.DictReader(f)


def write_csv(path: Path | str, fieldnames: list[str], rows: Iterable[dict[str, str]]) -> int:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
            count += 1
    return count

