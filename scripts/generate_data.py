"""Reproducible sample-data generator (fixed seed).

    .venv\\Scripts\\python.exe scripts\\generate_data.py                       # 100 orders -> data/samples
    .venv\\Scripts\\python.exe scripts\\generate_data.py --orders 100000 --out data/generated

Deliberately invalid rows are chosen HERE (by row number, recorded in manifest.json) - the
production validation rules know nothing about them. Each anomaly row has exactly one injected
defect, and clean rows are guaranteed clean, so `expected_rejected` in the manifest is exact.

Files written
  customers.csv            parent dataset (clean customers + anomaly customers)
  orders.csv               main batch, rows shuffled (out-of-order events) with injected anomalies
  orders.json              first 50 clean orders as JSON (JSON ingestion demo)
  orders_late.csv          late-arriving batch: older versions of published orders + new late orders
  orders_unexpected_column.csv   non-breaking schema change (extra column)
  orders_missing_column.csv      breaking schema change (required column removed)
  orders_renamed_column.csv      breaking schema change (unmapped rename)
  manifest.json            seed, sizes, anomaly row numbers per type, expected counts
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

ORDER_ANOMALIES = [
    "null_required", "duplicate_primary_key", "invalid_type", "invalid_allowed_value", "negative_quantity",
    "invalid_foreign_key", "invalid_timestamp", "total_mismatch", "ship_before_order", "duplicate_business_event",
]
CUSTOMER_ANOMALIES = ["invalid_email", "invalid_country", "null_name", "duplicate_primary_key"]
STATUSES = ["PLACED", "PAID", "SHIPPED", "DELIVERED", "CANCELLED"]
COUNTRIES = ["IN", "US", "GB", "DE", "SG"]
START = pd.Timestamp("2024-01-01T00:00:00Z")
WINDOW_SECONDS = 180 * 24 * 3600


def _iso(ts: pd.Series) -> pd.Series:
    return ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_customers(n: int, rng: np.random.Generator, anomalies_per_type: int):
    ids = np.arange(1, n + 1)
    signup = START + pd.to_timedelta(rng.integers(0, WINDOW_SECONDS, n), unit="s")
    df = pd.DataFrame({
        "customer_id": [f"CUST-{i:05d}" for i in ids],
        "full_name": [f"Customer {i}" for i in ids],
        "email": [f"customer{i}@example.com" for i in ids],
        "country": rng.choice(COUNTRIES, n),
        "segment": rng.choice(["RETAIL", "SMB", "ENTERPRISE", ""], n, p=[0.6, 0.2, 0.1, 0.1]),
        "signup_ts": _iso(pd.Series(signup)),
        "updated_ts": _iso(pd.Series(signup + pd.to_timedelta(rng.integers(0, 30 * 86400, n), unit="s"))),
    })
    # Anomaly customers get NEW ids (n+1...) so no order ever references an invalid customer.
    extra, manifest, next_id = [], {}, n + 1
    for kind in CUSTOMER_ANOMALIES:
        rows = []
        for _ in range(anomalies_per_type):
            base = df.iloc[int(rng.integers(0, n))].copy()
            base["customer_id"] = f"CUST-{next_id:05d}"
            base["email"] = f"customer{next_id}@example.com"
            next_id += 1
            if kind == "invalid_email":
                base["email"] = "not-an-email"
            elif kind == "invalid_country":
                base["country"] = "XX"
            elif kind == "null_name":
                base["full_name"] = ""
            elif kind == "duplicate_primary_key":
                base["customer_id"] = df.iloc[int(rng.integers(0, n))]["customer_id"]
                base["email"] = f"dup{next_id}@example.com"
            extra.append(base)
            rows.append(n + len(extra))           # 1-based row number in the file
        manifest[kind] = rows
    return pd.concat([df, pd.DataFrame(extra)], ignore_index=True), manifest


def generate_orders(n: int, n_customers: int, rng: np.random.Generator, anomaly_rate: float):
    order_ts = pd.Series(START + pd.to_timedelta(
        np.sort(rng.choice(WINDOW_SECONDS, n, replace=False)), unit="s"))      # unique -> no accidental dup events
    qty = rng.integers(1, 10, n)
    price = np.round(rng.uniform(5, 500, n), 2)
    status = rng.choice(STATUSES, n, p=[0.2, 0.25, 0.25, 0.25, 0.05])
    shipped = pd.Series(np.isin(status, ["SHIPPED", "DELIVERED"]))
    ship_ts = order_ts + pd.to_timedelta(rng.integers(3600, 3 * 86400, n), unit="s")
    updated = ship_ts.where(shipped, order_ts) + pd.to_timedelta(rng.integers(60, 86400, n), unit="s")
    df = pd.DataFrame({
        "order_id": [f"ORD-{i:06d}" for i in range(1, n + 1)],
        "customer_id": [f"CUST-{c:05d}" for c in rng.integers(1, n_customers + 1, n)],
        "product_id": [f"PRD-{p:04d}" for p in rng.integers(1, 501, n)],
        "quantity": qty.astype(str),
        "unit_price": [f"{p:.2f}" for p in price],
        "total_amount": [f"{q * p:.2f}" for q, p in zip(qty, price)],
        "currency": rng.choice(["INR", "USD", "EUR", "GBP", "inr"], n, p=[0.5, 0.2, 0.1, 0.1, 0.1]),
        "status": status,
        "order_ts": _iso(order_ts),
        "ship_ts": _iso(ship_ts).where(shipped, ""),
        "updated_ts": _iso(updated),
        "channel": rng.choice(["WEB", "MOBILE", "STORE", ""], n, p=[0.5, 0.3, 0.1, 0.1]),
    })
    # Out-of-order events: shuffle FIRST, so the file is not sorted by time; anomalies are then
    # injected at known file positions (manifest row numbers stay exact).
    df = df.iloc[rng.permutation(n)].reset_index(drop=True)

    per_type = max(1, int(round(n * anomaly_rate / len(ORDER_ANOMALIES))))
    per_type = min(per_type, (n // 2) // len(ORDER_ANOMALIES)) or 1
    # position 0 is never an anomaly, so every duplicate has an earlier clean source
    candidates = rng.choice(np.arange(1, n), per_type * len(ORDER_ANOMALIES), replace=False)
    is_clean = np.ones(n, dtype=bool)
    is_clean[candidates] = False
    manifest: Dict[str, List[int]] = {}
    for k, kind in enumerate(ORDER_ANOMALIES):
        idx = np.sort(candidates[k * per_type:(k + 1) * per_type])
        manifest[kind] = [int(i) + 1 for i in idx]                    # 1-based data row numbers
        for i in idx:
            earlier_clean = np.flatnonzero(is_clean[:i])
            if kind == "null_required":
                df.at[i, "product_id"] = ""
            elif kind == "duplicate_primary_key":
                df.at[i, "order_id"] = df.at[int(rng.choice(earlier_clean)), "order_id"]
            elif kind == "invalid_type":
                df.at[i, "quantity"] = "two"
            elif kind == "invalid_allowed_value":
                df.at[i, "status"] = "LOST"
            elif kind == "negative_quantity":
                df.at[i, "quantity"] = str(-int(df.at[i, "quantity"]))
                df.at[i, "total_amount"] = "0.00"
            elif kind == "invalid_foreign_key":
                df.at[i, "customer_id"] = "CUST-99999"
            elif kind == "invalid_timestamp":
                df.at[i, "order_ts"] = "2024-02-30T25:61:00"
            elif kind == "total_mismatch":
                df.at[i, "total_amount"] = f"{float(df.at[i, 'total_amount']) + 10:.2f}"
            elif kind == "ship_before_order":
                df.at[i, "status"] = "SHIPPED"
                df.at[i, "ship_ts"] = _iso(pd.Series([pd.Timestamp(df.at[i, "order_ts"]) - pd.Timedelta(days=2)])).iloc[0]
            elif kind == "duplicate_business_event":
                src = int(rng.choice(earlier_clean))
                for c in ("customer_id", "product_id", "order_ts", "quantity", "unit_price", "total_amount"):
                    df.at[i, c] = df.at[src, c]
                # keep the row otherwise valid (ISO strings compare chronologically)
                if df.at[i, "ship_ts"] and df.at[i, "ship_ts"] < df.at[i, "order_ts"]:
                    df.at[i, "ship_ts"] = df.at[i, "order_ts"]
                if df.at[i, "updated_ts"] < df.at[i, "order_ts"]:
                    df.at[i, "updated_ts"] = df.at[i, "order_ts"]
    return df, manifest


def generate(n_orders: int = 100, n_customers: int = 50, seed: int = 42, anomaly_rate: float = 0.1,
             customer_anomalies: int = 2):
    rng = np.random.default_rng(seed)
    customers, cust_manifest = generate_customers(n_customers, rng, customer_anomalies)
    orders, order_manifest = generate_orders(n_orders, n_customers, rng, anomaly_rate)
    rejected_orders = sum(len(v) for v in order_manifest.values())
    manifest = {
        "seed": seed, "orders": n_orders, "customers": n_customers, "anomaly_rate": anomaly_rate,
        "customer_anomalies": cust_manifest, "order_anomalies": order_manifest,
        "expected": {
            "customers_rejected": sum(len(v) for v in cust_manifest.values()),
            "customers_valid": n_customers,
            "orders_rejected": rejected_orders,
            "orders_valid": n_orders - rejected_orders,
        },
    }
    return customers, orders, manifest

def scenario_files(orders: pd.DataFrame, manifest: dict, rng_seed: int) -> Dict[str, pd.DataFrame]:
    bad_rows = {r - 1 for rows in manifest["order_anomalies"].values() for r in rows}
    clean = orders.drop(index=list(bad_rows)).reset_index(drop=True)
    sample = clean.head(20).copy()
    unexpected = sample.assign(gift_wrap="no")
    missing = sample.drop(columns=["currency"])
    renamed = sample.rename(columns={"unit_price": "unit_price_amount"})
    # late file: (a) older versions of already-published orders, (b) brand-new orders dated in the past
    older = clean.head(10).copy()
    older["status"] = "PLACED"
    older["ship_ts"] = ""
    older["updated_ts"] = older["order_ts"]                     # older than the published updated_ts
    new_late = clean.head(10).copy()
    new_late["order_id"] = [f"ORD-9{i:06d}" for i in range(1, 11)]
    new_late["order_ts"] = "2023-12-01T08:00:00Z"
    new_late["updated_ts"] = "2023-12-01T09:00:00Z"
    new_late["ship_ts"] = ""
    new_late["status"] = "PLACED"
    return {"orders_unexpected_column.csv": unexpected, "orders_missing_column.csv": missing,
            "orders_renamed_column.csv": renamed, "orders_late.csv": pd.concat([older, new_late])}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--orders", type=int, default=100)
    p.add_argument("--customers", type=int, default=50)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--anomaly-rate", type=float, default=0.1, help="share of order rows made invalid")
    p.add_argument("--out", default="data/samples")
    args = p.parse_args()
    if not 0 <= args.anomaly_rate <= 0.5:
        sys.exit("--anomaly-rate must be between 0 and 0.5")
    out = (ROOT / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    customers, orders, manifest = generate(args.orders, args.customers, args.seed, args.anomaly_rate)
    customers.to_csv(out / "customers.csv", index=False)
    orders.to_csv(out / "orders.csv", index=False)
    bad = {r - 1 for rows in manifest["order_anomalies"].values() for r in rows}
    clean_json = orders.drop(index=list(bad)).head(50)
    (out / "orders.json").write_text(json.dumps(clean_json.to_dict("records"), indent=1), encoding="utf-8")
    for name, frame in scenario_files(orders, manifest, args.seed).items():
        frame.to_csv(out / name, index=False)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {len(customers)} customers and {len(orders)} orders to {out}")
    print(json.dumps(manifest["expected"]))


if __name__ == "__main__":
    main()
