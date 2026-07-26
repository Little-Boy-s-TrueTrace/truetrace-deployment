#!/usr/bin/env python3
"""End-to-end verifier for the TrueTrace Docker Compose stack."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = os.getenv("TRUETRACE_BASE_URL", "http://localhost").rstrip("/")
BANK_API = f"{BASE_URL}/api-bank/api"
ROOT = Path(__file__).resolve().parents[2]
IMAGE_PATH = Path(
    os.getenv("TRUETRACE_TEST_IMAGE", ROOT / "truetrace-web-client/public/logo.png")
)
SYNTHETIC_PATH = Path(
    os.getenv("TRUETRACE_SYNTHETIC_SAMPLE", ROOT / "README.md")
)
POSTGRES_CONTAINER = os.getenv(
    "TRUETRACE_POSTGRES_CONTAINER", "truetrace-postgres"
)


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> Any:
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode()
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else {}
    except HTTPError as exc:
        details = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url} returned {exc.code}: {details}") from exc


def wait_for_url(url: str) -> None:
    last_error: Exception | None = None
    for _ in range(60):
        try:
            with urlopen(url, timeout=5) as response:
                if response.status < 400:
                    return
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def multipart_body(fields: dict[str, str], files: dict[str, Path]) -> tuple[bytes, str]:
    boundary = f"----TrueTrace{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )
    for name, path in files.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{path.name}"\r\n'
                ).encode(),
                b"Content-Type: image/png\r\n\r\n",
                path.read_bytes(),
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def create_kyc(customer_name: str, cccd: str, image: Path) -> str:
    body, content_type = multipart_body(
        {"customerName": customer_name, "cccdNumber": cccd},
        {"selfie": image, "idFront": image, "idBack": image},
    )
    result = request_json(
        "POST",
        f"{BANK_API}/kyc/sessions",
        headers={"Content-Type": content_type},
        body=body,
    )
    return result["sessionId"]


def wait_for_kyc_status(session_id: str, expected: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for _ in range(30):
        result = request_json("GET", f"{BANK_API}/kyc/sessions/{session_id}")
        if result.get("status") == expected:
            return result
        time.sleep(1)
    raise AssertionError(f"KYC {session_id} did not reach {expected}: {result}")


def register(username: str, password: str) -> str:
    result = request_json(
        "POST",
        f"{BANK_API}/auth/register",
        {
            "username": username,
            "password": password,
            "fullName": username,
            "email": f"{username}@truetrace.test",
        },
    )
    return result["accountNumber"]


def login(username: str, password: str) -> str:
    result = request_json(
        "POST",
        f"{BANK_API}/auth/login",
        {"username": username, "password": password},
    )
    return result["token"]


def transfer(
    token: str, source: str, target: str, amount: int, description: str
) -> None:
    request_json(
        "POST",
        f"{BANK_API}/transactions/transfer",
        {
            "sourceAccountNumber": source,
            "targetAccountNumber": target,
            "amount": amount,
            "description": description,
        },
        {"Authorization": f"Bearer {token}"},
    )


def psql(query: str) -> str:
    command = [
        "docker",
        "exec",
        "-e",
        "PGPASSWORD=postgres",
        POSTGRES_CONTAINER,
        "psql",
        "-U",
        "postgres",
        "-d",
        "truetrace",
        "-t",
        "-A",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        query,
    ]
    return subprocess.run(
        command, check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> None:
    wait_for_url(f"{BASE_URL}/")
    wait_for_url(f"{BASE_URL}/api-bank/health")
    wait_for_url(f"{BASE_URL}/soc/")

    approved_id = create_kyc(
        "TrueTrace E2E Customer", "001090123457", IMAGE_PATH
    )
    approved = wait_for_kyc_status(approved_id, "APPROVED")
    assert approved["cccdValid"] is True
    assert approved["deepfakeScore"] < 50

    rejected_id = create_kyc(
        "TrueTrace Synthetic Test", "001090123458", SYNTHETIC_PATH
    )
    rejected = wait_for_kyc_status(rejected_id, "REJECTED")
    assert rejected["deepfakeScore"] >= 80
    assert rejected["recommendedAction"] == "BLOCK_ONBOARDING"

    stamp = str(int(time.time()))
    password = "Test@12345"
    funder_one = f"fund1_{stamp}"
    funder_two = f"fund2_{stamp}"
    mule_user = f"mule_{stamp}"
    funder_one_account = register(funder_one, password)
    funder_two_account = register(funder_two, password)
    mule_account = register(mule_user, password)
    targets = [
        register(f"target{index}_{stamp}", password) for index in range(1, 21)
    ]

    psql(
        "UPDATE accounts SET balance=600000000 "
        f"WHERE account_number IN ('{funder_one_account}','{funder_two_account}');"
    )
    funder_one_token = login(funder_one, password)
    funder_two_token = login(funder_two, password)
    mule_token = login(mule_user, password)

    started = time.monotonic()
    transfer(
        funder_one_token,
        funder_one_account,
        mule_account,
        500_000_000,
        "E2E inflow 1",
    )
    transfer(
        funder_two_token,
        funder_two_account,
        mule_account,
        500_000_000,
        "E2E inflow 2",
    )
    for index, target in enumerate(targets, start=1):
        transfer(
            mule_token,
            mule_account,
            target,
            40_000_000,
            f"E2E fanout {index}",
        )
    elapsed = time.monotonic() - started
    assert elapsed <= 60, f"AML scenario exceeded 60 seconds: {elapsed:.2f}s"

    alert: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    account_status = ""
    for _ in range(30):
        account_status = psql(
            "SELECT status FROM accounts "
            f"WHERE account_number='{mule_account}';"
        )
        alerts = request_json("GET", f"{BANK_API}/aml/alerts")
        matching_alerts = [
            item
            for item in alerts
            if item.get("primaryAccountNumber") == mule_account
        ]
        if matching_alerts:
            alert = max(matching_alerts, key=lambda item: item["id"])
            reports = request_json("GET", f"{BANK_API}/str/reports")
            matching_reports = [
                item for item in reports if item.get("alertId") == alert["id"]
            ]
            if account_status == "FROZEN" and matching_reports:
                report = max(matching_reports, key=lambda item: item["id"])
                break
        time.sleep(1)

    assert account_status == "FROZEN"
    assert alert is not None
    assert alert["riskScore"] == 10
    assert float(alert["totalAmount"]) == 1_000_000_000
    assert alert["timeWindowSeconds"] == 60
    assert report is not None
    assert report["status"] == "DRAFT"
    assert report["riskScore"] == 10
    assert float(report["totalAmount"]) == 1_000_000_000

    print("TrueTrace full-stack smoke test passed")
    print(f"KYC approved={approved_id} rejected={rejected_id}")
    print(
        f"AML mule={mule_account} elapsed={elapsed:.2f}s "
        f"alert={alert['alertId']} report={report['reportId']}"
    )


if __name__ == "__main__":
    main()
