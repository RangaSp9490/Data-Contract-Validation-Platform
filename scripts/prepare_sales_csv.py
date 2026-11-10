"""Prepare the 'Sales Data' CSV for the platform (format only - data problems are left in).

Two things in the raw export do not fit a contract:
  1. Headers contain spaces ("Order Id"); contract column names may only use letters,
     digits and "_".  ->  spaces become underscores ("Order_Id").
  2. Dates are US style M/D/YYYY ("6/17/2017"), which the platform would read as D/M/YYYY
     (6/5/2017 -> 6 May) or reject (6/17/2017).  ->  converted to ISO "2017-06-17".
     Empty or unparseable dates are left untouched so the contract can reject those rows.

Everything else (empty Sales, duplicate Row_Id, odd categories, ...) is NOT fixed:
catching those is the contract's job.

Usage:
  .venv\\Scripts\\python.exe scripts\\prepare_sales_csv.py "<input.csv>" "<output.csv>"
"""
import re
import sys

import pandas as pd

DATE_COLUMNS = ["Order Date"]


def clean_header(name: str) -> str:
    return re.sub(r"\s+", "_", name.strip())


def us_date_to_iso(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, format="%m/%d/%Y", errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d").where(parsed.notna(), series)


def main(src: str, dst: str) -> None:
    df = pd.read_csv(src, dtype=str, keep_default_na=False)
    converted = {}
    for col in DATE_COLUMNS:
        if col in df.columns:
            before = df[col].copy()
            df[col] = us_date_to_iso(df[col])
            converted[col] = int((before != df[col]).sum())
    df.columns = [clean_header(c) for c in df.columns]
    df.to_csv(dst, index=False, encoding="utf-8")
    print(f"rows: {len(df)}")
    print(f"headers: {', '.join(df.columns)}")
    print(f"dates converted to ISO: {converted}")
    print(f"written: {dst}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
