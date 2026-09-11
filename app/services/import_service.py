"""Generic CSV/XLSX row parsing — used by every bulk-import endpoint (Products, Suppliers,
Parties, ...). Each entity's own import function maps these plain dict rows onto its schema;
this module only turns bytes into a list of {column_name: value} dicts.
"""
import csv
import io

import openpyxl


class ImportFormatError(Exception):
    def __init__(self, message: str):
        self.message = message


def parse_rows(filename: str, content: bytes) -> list[dict]:
    lower = filename.lower()
    if lower.endswith(".csv"):
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in reader]
    if lower.endswith((".xlsx", ".xlsm")):
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheet = workbook.active
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header = [str(h).strip() if h is not None else "" for h in next(rows_iter)]
        except StopIteration:
            return []
        rows = []
        for raw_row in rows_iter:
            if all(v is None for v in raw_row):
                continue
            rows.append({header[i]: raw_row[i] for i in range(len(header)) if i < len(raw_row)})
        return rows
    raise ImportFormatError(f"Unsupported file type: {filename} (use .csv or .xlsx)")


def cell_str(row: dict, key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def cell_bool(row: dict, key: str, default: bool = False) -> bool:
    value = cell_str(row, key)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y")


def cell_int(row: dict, key: str, default: int = 0) -> int:
    value = cell_str(row, key)
    if value is None:
        return default
    return int(float(value))
