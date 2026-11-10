"""Shared fixtures.

Isolation: before `backend` is imported we point DATABASE_URL at TEST_DATABASE_URL
(from .env or the environment), force the mock LLM and use a temp upload folder.
Every test starts from empty tables (TRUNCATE). A guard refuses to run against a
database whose name does not contain 'test', so production data can't be wiped.
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_test_url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get("TEST_DATABASE_URL")
if not _test_url:
    raise RuntimeError("TEST_DATABASE_URL is not configured (.env or environment)")
os.environ["DATABASE_URL"] = _test_url
os.environ["LLM_PROVIDER"] = "mock"
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="dcvp_uploads_")
os.environ["LOG_LEVEL"] = "WARNING"

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from backend.database import Base, get_engine, init_db, new_session  # noqa: E402

if "test" not in (make_url(_test_url).database or ""):
    raise RuntimeError("Refusing to run tests: TEST_DATABASE_URL database name must contain 'test'")

SAMPLES = ROOT / "data" / "samples"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    init_db()
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    with get_engine().begin() as conn:
        conn.execute(text("SET SESSION lock_wait_timeout = 30"))   # fail fast instead of hanging
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f"TRUNCATE TABLE `{table.name}`"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    yield


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db():
    session = new_session()
    yield session
    session.close()


def load_contract(name: str) -> dict:
    return json.loads((SAMPLES / "contracts" / f"{name}_contract.json").read_text(encoding="utf-8"))


@pytest.fixture()
def customers_contract(client):
    resp = client.post("/api/contracts", json=load_contract("customers"))
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture()
def orders_contract(client, customers_contract):
    resp = client.post("/api/contracts", json=load_contract("orders"))
    assert resp.status_code == 201, resp.text
    return resp.json()


def upload(client, contract_id: int, filename: str, content, **params):
    """POST a file to the ingestion endpoint and return the JSON response (sync mode)."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    params.setdefault("sync", "true")
    resp = client.post(
        "/api/ingestion/upload",
        data={"contract_id": str(contract_id), **{k: str(v) for k, v in params.items()}},
        files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
    )
    return resp


CUSTOMER_HEADER = "customer_id,full_name,email,country,segment,signup_ts,updated_ts"


def customers_csv(*rows: str) -> str:
    return "\n".join([CUSTOMER_HEADER, *rows]) + "\n"


def good_customer(n: int, **overrides) -> str:
    values = {
        "customer_id": f"CUST-{n:05d}", "full_name": f"Customer {n}", "email": f"c{n}@example.com",
        "country": "IN", "segment": "RETAIL", "signup_ts": "2024-01-01T10:00:00Z",
        "updated_ts": "2024-01-02T10:00:00Z",
    }
    values.update(overrides)
    return ",".join(str(values[k]) for k in CUSTOMER_HEADER.split(","))


ORDER_HEADER = ("order_id,customer_id,product_id,quantity,unit_price,total_amount,currency,status,"
                "order_ts,ship_ts,updated_ts,channel")


def good_order(n: int, **overrides) -> dict:
    values = {
        "order_id": f"ORD-{n:06d}", "customer_id": "CUST-00001", "product_id": f"PRD-{n % 10000:04d}",
        "quantity": 2, "unit_price": "10.50", "total_amount": "21.00", "currency": "INR",
        "status": "PLACED", "order_ts": f"2024-03-01T10:{n % 60:02d}:00Z", "ship_ts": "",
        "updated_ts": f"2024-03-01T11:{n % 60:02d}:00Z", "channel": "WEB",
    }
    values.update(overrides)
    return values


def orders_csv(*rows: dict, header: str = ORDER_HEADER) -> str:
    cols = header.split(",")
    lines = [header] + [",".join(str(r.get(c, "")) for c in cols) for r in rows]
    return "\n".join(lines) + "\n"
