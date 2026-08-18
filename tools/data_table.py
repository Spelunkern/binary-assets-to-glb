#!/usr/bin/env python3
"Writing the project's flat tables (the Godot side is\nscripts/data_table.gd, which explains why the format is CSV and why the\nfile is called .txt)."

import csv
from pathlib import Path


def write_table(path: Path, columns: tuple, rows: list) -> None:
    'Write a table with a header.\n\n    newline="" is required: without that the Python csv module emits CRLF in\n    Windows and there are blank lines between rows.\n\n    Bools come out in lowercase ("true"/"false"), which is what you expect\n    DataTable._parse_value -- Python would write "True" on its own.'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: _cell(row.get(c, "")) for c in columns})


def _cell(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return value


def read_table(path: Path) -> list:
    'Reads a table written by write_table and returns a row by dict.\n\n    The values remain as STRING, unlike the Godot reader\n    (scripts/data_table.gd), which converts according to the form. On the side of\n    pipeline is not necessary: the only thing done with these fields is\n    Compare them and put together routes.'
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))
