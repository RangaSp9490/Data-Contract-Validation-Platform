"""Load a complete demo through the REST API of a RUNNING server.

    .venv\\Scripts\\python.exe -m uvicorn backend.main:app        (terminal 1)
    .venv\\Scripts\\python.exe scripts\\seed_demo.py              (terminal 2)

Creates the customers + orders contracts (if missing), uploads every sample file in
data/samples (happy path, invalid rows, schema changes, late data, a duplicate batch),
adds a non-breaking contract version, and saves sample outputs to data/samples/outputs/.
"""
import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"
OUT = SAMPLES / "outputs"

UPLOADS = [   # (contract dataset, file, source system, description)
    ("customers", "customers.csv", "crm", "customer master data (8 invalid rows injected)"),
    ("orders", "orders.csv", "order-service", "100 orders, 10 invalid rows injected, out-of-order"),
    ("orders", "orders.json", "order-service", "same orders as JSON (already published -> unchanged)"),
    ("orders", "orders_unexpected_column.csv", "order-service", "NON-BREAKING change: extra column"),
    ("orders", "orders_missing_column.csv", "order-service", "BREAKING change: required column removed"),
    ("orders", "orders_renamed_column.csv", "order-service", "BREAKING change: unmapped rename"),
    ("orders", "orders_late.csv", "order-service-replay", "late-arriving + out-of-order events"),
    ("orders", "orders.csv", "order-service", "exact duplicate batch -> SKIPPED_DUPLICATE"),
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000")
    args = p.parse_args()
    c = httpx.Client(base_url=args.url, timeout=300)
    try:
        health = c.get("/api/health").json()
    except httpx.HTTPError:
        sys.exit(f"Server not reachable at {args.url}. Start it first: .venv\\Scripts\\python.exe -m uvicorn backend.main:app")
    print("health:", health["status"], health["database"].get("target"))

    existing = {x["dataset_name"]: x["id"] for x in c.get("/api/contracts").json()}
    ids = {}
    for name in ("customers", "orders"):
        if name in existing:
            ids[name] = existing[name]
            print(f"contract {name}: exists (id {ids[name]})")
            continue
        body = json.loads((SAMPLES / "contracts" / f"{name}_contract.json").read_text(encoding="utf-8"))
        r = c.post("/api/contracts", json=body)
        r.raise_for_status()
        ids[name] = r.json()["id"]
        print(f"contract {name}: created (id {ids[name]})")

    OUT.mkdir(parents=True, exist_ok=True)
    runs = []
    for dataset, fname, system, what in UPLOADS:
        with open(SAMPLES / fname, "rb") as fh:
            r = c.post("/api/ingestion/upload", data={"contract_id": ids[dataset], "source_system": system, "sync": "true"},
                       files={"file": (fname, fh)})
        r.raise_for_status()
        run = r.json()
        runs.append(run)
        print(f"{fname:32s} {run['status']:22s} in={run['input_count']:>4} valid={run['valid_count']:>4} "
              f"rejected={run['rejected_count']:>3} published={run['published_count']:>4} "
              f"breaking={run['breaking_change_count']}  # {what}")

    # contract evolution: an additive (non-breaking) v2 for orders
    detail = c.get(f"/api/contracts/{ids['orders']}").json()["current"]
    if detail["version"] == 1:
        spec = json.loads((SAMPLES / "contracts" / "orders_contract.json").read_text(encoding="utf-8"))
        for k in ("name", "dataset_name", "description", "owner"):
            spec.pop(k)
        spec["columns"].append({"name": "gift_wrap", "data_type": "boolean", "required": False, "nullable": True,
                                "description": "Added in v2 (optional)"})
        spec["change_notes"] = "v2: optional gift_wrap column"
        r = c.post(f"/api/contracts/{ids['orders']}/versions", json=spec)
        r.raise_for_status()
        print("orders v2 created:", r.json()["compatibility"])

    # sample outputs
    for run in runs[:2] + [runs[4], runs[6]]:
        rep = c.get(f"/api/runs/{run['run_id']}/report").json()
        (OUT / f"report_{run['source_file_name'].replace('.', '_')}.json").write_text(json.dumps(rep, indent=2, default=str))
    export = c.get("/api/rejected/export", params={"run_id": runs[1]["run_id"]})
    (OUT / "rejected_orders.csv").write_text(export.text, encoding="utf-8")
    (OUT / "metrics_summary.json").write_text(json.dumps(c.get("/api/metrics/summary").json(), indent=2, default=str))
    print(f"sample outputs written to {OUT}")


if __name__ == "__main__":
    main()
