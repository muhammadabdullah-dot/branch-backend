"""Generic CSV/XLSX row parsing — used by every bulk-import endpoint (Products, Suppliers,
Parties, ...). Each entity's own import function maps these plain dict rows onto its schema;
this module only turns bytes into a list of {column_name: value} dicts.
"""
import csv
import io

import openpyxl
import xlrd


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
    if lower.endswith(".xls"):
        # Legacy binary Excel format — real exports from the old system come out this way.
        workbook = xlrd.open_workbook(file_contents=content)
        sheet = workbook.sheet_by_index(0)
        if sheet.nrows == 0:
            return []
        header = [str(h).strip() for h in sheet.row_values(0)]
        rows = []
        for r in range(1, sheet.nrows):
            values = sheet.row_values(r)
            if all(v == "" for v in values):
                continue
            rows.append({header[i]: values[i] for i in range(len(header)) if i < len(values)})
        return rows
    raise ImportFormatError(f"Unsupported file type: {filename} (use .csv, .xlsx, or .xls)")


def cell_str_any(row: dict, *keys: str) -> str | None:
    """Tries each header spelling in order — the two real catalog exports disagree on
    'SALES PRICE' vs 'SALE PRICE', 'SUBCLASS' vs 'SUB CLASS', etc."""
    for key in keys:
        value = cell_str(row, key)
        if value is not None:
            return value
    return None


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
