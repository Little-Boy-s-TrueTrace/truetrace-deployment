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


def runtime_setting(name: str, default: str) -> str:
    """Read process environment, then the same local .env used by Compose."""
    value = os.getenv(name)
    if value is not None:
        return value
    env_file = ROOT / "truetrace-deployment/.env"
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, candidate = line.split("=", 1)
            if key.strip() == name:
                return candidate.strip().strip("\"'")
    return default


IMAGE_PATH = Path(
    os.getenv("TRUETRACE_TEST_IMAGE", ROOT / "demo-data/kyc-images/selfie.png")
)
ID_FRONT_PATH = Path(
    os.getenv(
        "TRUETRACE_TEST_ID_FRONT",
        ROOT / "demo-data/kyc-images/cccd_front.png",
    )
)
ID_BACK_PATH = Path(
    os.getenv(
        "TRUETRACE_TEST_ID_BACK",
        ROOT / "demo-data/kyc-images/cccd_back.png",
    )
)
SYNTHETIC_PATH = Path(
    os.getenv(
        "TRUETRACE_SYNTHETIC_SAMPLE",
        ROOT / "demo-data/kyc-images/synthetic_deepfake_test.png",
    )
)
POSTGRES_CONTAINER = os.getenv(
    "TRUETRACE_POSTGRES_CONTAINER", "truetrace-postgres"
)
KAFKA_CONTAINER = os.getenv("TRUETRACE_KAFKA_CONTAINER", "truetrace-kafka")
POSTGRES_USER = runtime_setting("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = runtime_setting("POSTGRES_PASSWORD", "postgres")
POSTGRES_DB = runtime_setting("POSTGRES_DB", "truetrace")
INTERNAL_TOKEN = runtime_setting(
    "TRUETRACE_SECURITY_SYNC_TOKEN", "change-me-internal-service-token"
)
COMPLIANCE_OPERATOR = "e2e.compliance.officer"
COMPLIANCE_HEADERS = {
    "X-TrueTrace-Internal-Token": INTERNAL_TOKEN,
    "X-TrueTrace-Operator": COMPLIANCE_OPERATOR,
}
KAFKA_TOPICS = (
    "truetrace.kyc.submissions",
    "truetrace.transactions",
    "truetrace.findings.deepfake",
    "truetrace.findings.money_trail",
    "truetrace.alerts",
    "truetrace.reports.str",
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



def expect_http_status(
    method: str,
    url: str,
    expected: int,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    request_headers = dict(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            actual = response.status
    except HTTPError as exc:
        actual = exc.code
    assert actual == expected, f"{method} {url}: expected {expected}, got {actual}"
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


def create_kyc(
    token: str,
    customer_name: str,
    cccd: str,
    selfie: Path,
    id_front: Path = ID_FRONT_PATH,
    id_back: Path = ID_BACK_PATH,
) -> str:
    body, content_type = multipart_body(
        {"customerName": customer_name, "cccdNumber": cccd},
        {"selfie": selfie, "idFront": id_front, "idBack": id_back},
    )
    result = request_json(
        "POST",
        f"{BANK_API}/kyc/sessions",
        headers={
            "Content-Type": content_type,
            "Authorization": f"Bearer {token}",
        },
        body=body,
    )
    return result["sessionId"]


def wait_for_kyc_status(token: str, session_id: str, expected: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for _ in range(30):
        result = request_json(
            "GET",
            f"{BANK_API}/kyc/sessions/{session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if result.get("status") == expected:
            return result
        time.sleep(1)
    raise AssertionError(f"KYC {session_id} did not reach {expected}: {result}")


def register(
    username: str, password: str, full_name: str | None = None
) -> str:
    result = request_json(
        "POST",
        f"{BANK_API}/auth/register",
        {
            "username": username,
            "password": password,
            "fullName": full_name or f"TrueTrace Demo {username}",
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
        f"PGPASSWORD={POSTGRES_PASSWORD}",
        POSTGRES_CONTAINER,
        "psql",
        "-U",
        POSTGRES_USER,
        "-d",
        POSTGRES_DB,
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


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def assert_db_value(query: str, expected: str, label: str) -> None:
    actual = psql(query)
    assert actual == expected, f"{label}: expected {expected!r}, got {actual!r}"


def kafka_topic_offset(topic: str) -> int:
    command = [
        "docker",
        "exec",
        KAFKA_CONTAINER,
        "/opt/kafka/bin/kafka-get-offsets.sh",
        "--bootstrap-server",
        "localhost:29092",
        "--topic",
        topic,
    ]
    output = subprocess.run(
        command, check=True, capture_output=True, text=True
    ).stdout.strip()
    offsets = []
    for line in output.splitlines():
        try:
            offsets.append(int(line.rsplit(":", 1)[1]))
        except (IndexError, ValueError) as exc:
            raise AssertionError(
                f"Unexpected Kafka offset output for {topic}: {line!r}"
            ) from exc
    if not offsets:
        raise AssertionError(f"Kafka topic {topic} returned no partition offsets")
    return sum(offsets)


def kafka_offsets() -> dict[str, int]:
    return {topic: kafka_topic_offset(topic) for topic in KAFKA_TOPICS}


def wait_for_topic_advance(
    before: dict[str, int], topic: str, minimum: int, timeout_seconds: int = 45
) -> int:
    deadline = time.monotonic() + timeout_seconds
    current = kafka_topic_offset(topic)
    while current - before[topic] < minimum and time.monotonic() < deadline:
        time.sleep(1)
        current = kafka_topic_offset(topic)
    advanced = current - before[topic]
    assert advanced >= minimum, (
        f"Kafka topic {topic} advanced by {advanced}; expected at least {minimum}"
    )
    return advanced


def demo_cccd(seed: int) -> str:
    """Generate a format-valid, run-specific Vietnamese demo CCCD number."""
    return f"001200{seed % 1_000_000:06d}"


def approve_kyc(
    token: str, account_label: str, cccd_seed: int
) -> tuple[str, dict[str, Any]]:
    session_id = create_kyc(
        token,
        f"TrueTrace {account_label}",
        demo_cccd(cccd_seed),
        IMAGE_PATH,
    )
    return session_id, wait_for_kyc_status(token, session_id, "APPROVED")


def wait_for_alert_and_report(
    account_number: str,
    expected_alert_type: str | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    account_status = ""
    for _ in range(45):
        account_status = psql(
            "SELECT status FROM accounts "
            f"WHERE account_number={sql_literal(account_number)};"
        )
        alerts = request_json(
            "GET",
            f"{BANK_API}/aml/alerts",
            headers=COMPLIANCE_HEADERS,
        )
        matching_alerts = [
            item
            for item in alerts
            if item.get("primaryAccountNumber") == account_number
            and (
                expected_alert_type is None
                or item.get("alertType") == expected_alert_type
            )
        ]
        if matching_alerts:
            alert = max(matching_alerts, key=lambda item: item["id"])
            reports = request_json(
                "GET",
                f"{BANK_API}/str/reports",
                headers=COMPLIANCE_HEADERS,
            )
            matching_reports = [
                item for item in reports if item.get("alertId") == alert["id"]
            ]
            if account_status == "FROZEN" and matching_reports:
                report = max(matching_reports, key=lambda item: item["id"])
                return account_status, alert, report
        time.sleep(1)
    raise AssertionError(
        "Timed out waiting for persisted freeze, alert, and linked STR "
        f"for account {account_number}; last status={account_status!r}"
    )


def main() -> None:
    wait_for_url(f"{BASE_URL}/")
    wait_for_url(f"{BASE_URL}/api-bank/health")
    wait_for_url(f"{BASE_URL}/monitor/")

    auth_stamp = str(int(time.time()))
    auth_username = f"kyc_{auth_stamp}"
    auth_password = "Test@12345"
    register(auth_username, auth_password, "Nguyễn Văn An — KYC Demo")
    auth_token = login(auth_username, auth_password)
    kafka_before = kafka_offsets()

    expect_http_status("GET", f"{BANK_API}/aml/alerts", 401)

    approved_id = create_kyc(
        auth_token, "TrueTrace E2E Customer", "001090123457", IMAGE_PATH
    )
    approved = wait_for_kyc_status(auth_token, approved_id, "APPROVED")
    assert approved["cccdValid"] is True
    assert approved["deepfakeScore"] < 50

    rejected_id = create_kyc(
        auth_token, "TrueTrace Synthetic Test", "001090123458", SYNTHETIC_PATH
    )
    rejected = wait_for_kyc_status(auth_token, rejected_id, "REJECTED")
    assert rejected["deepfakeScore"] >= 80
    assert rejected["recommendedAction"] == "BLOCK_ONBOARDING"
    assert_db_value(
        "SELECT status FROM kyc_sessions "
        f"WHERE session_id={sql_literal(approved_id)};",
        "APPROVED",
        "approved KYC persistence",
    )
    assert_db_value(
        "SELECT status FROM kyc_sessions "
        f"WHERE session_id={sql_literal(rejected_id)};",
        "REJECTED",
        "rejected KYC persistence",
    )

    stamp = str(int(time.time()))
    cccd_seed = int(stamp[-6:])
    password = "Test@12345"
    funder_one = f"fund1_{stamp}"
    funder_two = f"fund2_{stamp}"
    mule_user = f"mule_{stamp}"
    funder_one_account = register(funder_one, password, "Demo Funding Customer One")
    funder_two_account = register(funder_two, password, "Demo Funding Customer Two")
    mule_account = register(mule_user, password, "Demo Monitored Mule Account")
    targets = [
        register(
            f"target{index}_{stamp}",
            password,
            f"Demo Rapid-Movement Beneficiary {index:02d}",
        )
        for index in range(1, 21)
    ]

    funder_one_token = login(funder_one, password)
    funder_two_token = login(funder_two, password)
    mule_token = login(mule_user, password)
    for index, (token, label) in enumerate(
        (
            (funder_one_token, "AML Funder One"),
            (funder_two_token, "AML Funder Two"),
            (mule_token, "AML Mule"),
        ),
        start=1,
    ):
        _, result = approve_kyc(token, label, cccd_seed + index)
        assert result["cccdValid"] is True

    for account in (funder_one_account, funder_two_account):
        balance = float(
            psql(
                "SELECT balance FROM accounts "
                f"WHERE account_number={sql_literal(account)};"
            )
        )
        assert balance >= 500_000_000, (
            "DEMO_INITIAL_BALANCE must fund the AML flow; "
            f"{account} has {balance:.0f} VND"
        )

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

    account_status, alert, report = wait_for_alert_and_report(
        mule_account,
        "RAPID_MOVEMENT",
    )

    assert account_status == "FROZEN"
    assert alert["riskScore"] == 10
    assert float(alert["totalAmount"]) == 1_000_000_000
    assert alert["timeWindowSeconds"] == 60
    assert report["status"] == "DRAFT"
    assert report["riskScore"] == 10
    assert float(report["totalAmount"]) == 1_000_000_000
    assert_db_value(
        "SELECT status FROM accounts "
        f"WHERE account_number={sql_literal(mule_account)};",
        "FROZEN",
        "mule freeze persistence",
    )
    assert_db_value(
        "SELECT COUNT(*) FROM transactions "
        f"WHERE source_account_number={sql_literal(mule_account)};",
        "20",
        "rapid-dispersion transaction persistence",
    )
    assert_db_value(
        "SELECT COUNT(*) FROM aml_alerts "
        f"WHERE alert_id={sql_literal(alert['alertId'])};",
        "1",
        "AML alert persistence",
    )
    assert_db_value(
        "SELECT COUNT(*) FROM str_reports "
        f"WHERE report_id={sql_literal(report['reportId'])} "
        f"AND alert_id={int(alert['id'])} AND status='DRAFT';",
        "1",
        "draft STR persistence and alert linkage",
    )

    # Two near-threshold transfers form one repeated-structuring case. The
    # second event crosses the real freeze threshold and creates an alert/STR.
    structurer = f"structurer_{stamp}"
    structurer_account = register(
        structurer, password, "Nguyễn Văn An — Verified Structuring"
    )
    structurer_token = login(structurer, password)
    structure_targets = [
        register(
            f"struct_target{index}_{stamp}",
            password,
            f"Demo Structuring Recipient {index}",
        )
        for index in range(1, 3)
    ]
    approve_kyc(structurer_token, "Structuring Demo", cccd_seed + 10)
    findings_before = kafka_topic_offset("truetrace.findings.money_trail")
    for index, target in enumerate(structure_targets, start=1):
        transfer(
            structurer_token,
            structurer_account,
            target,
            190_000_000,
            f"Manual demo structuring transfer {index}",
        )
    structuring_offsets = {
        "truetrace.findings.money_trail": findings_before,
    }
    wait_for_topic_advance(
        structuring_offsets,
        "truetrace.findings.money_trail",
        2,
    )
    struct_status, struct_alert, struct_report = wait_for_alert_and_report(
        structurer_account,
        "STRUCTURING",
    )
    assert struct_status == "FROZEN"
    assert struct_alert["riskScore"] >= 7
    assert float(struct_alert["totalAmount"]) == 380_000_000
    assert struct_alert["timeWindowSeconds"] == 60
    assert struct_report["status"] == "DRAFT"
    assert struct_report["riskScore"] >= 7
    assert float(struct_report["totalAmount"]) == 380_000_000

    structuring_chain = json.loads(struct_alert["transactionChainJson"])
    assert len(structuring_chain) == 2
    assert all(
        item["from"] == structurer_account for item in structuring_chain
    )
    assert {item["to"] for item in structuring_chain} == set(structure_targets)
    assert_db_value(
        "SELECT COUNT(*) FROM transactions "
        f"WHERE source_account_number={sql_literal(structurer_account)} "
        "AND amount=190000000;",
        "2",
        "repeated-structuring transaction persistence",
    )
    assert_db_value(
        "SELECT status FROM accounts "
        f"WHERE account_number={sql_literal(structurer_account)};",
        "FROZEN",
        "repeated-structuring freeze persistence",
    )
    assert_db_value(
        "SELECT COUNT(*) FROM aml_alerts "
        f"WHERE alert_id={sql_literal(struct_alert['alertId'])} "
        "AND alert_type='STRUCTURING' AND total_amount=380000000;",
        "1",
        "repeated-structuring alert persistence",
    )
    assert_db_value(
        "SELECT COUNT(*) FROM str_reports "
        f"WHERE report_id={sql_literal(struct_report['reportId'])} "
        f"AND alert_id={int(struct_alert['id'])} AND status='DRAFT' "
        "AND total_amount=380000000;",
        "1",
        "repeated-structuring STR persistence and linkage",
    )

    # Prepare a second approved but untouched account for the two live clicks
    # in the recording. Its behavior is covered by the verified case above.
    manual_structurer = f"manual_structurer_{stamp}"
    manual_structurer_account = register(
        manual_structurer, password, "Nguyễn Văn An — Live AML Recording"
    )
    manual_structurer_token = login(manual_structurer, password)
    manual_targets = [
        register(
            f"manual_target{index}_{stamp}",
            password,
            f"Live Demo Recipient {index}",
        )
        for index in range(1, 3)
    ]
    approve_kyc(
        manual_structurer_token,
        "Manual Structuring Recording",
        cccd_seed + 11,
    )

    report_url = f"{BANK_API}/str/reports/{report['reportId']}"
    expect_http_status(
        "POST", f"{report_url}/submit", 409, headers=COMPLIANCE_HEADERS
    )
    review_payload = dict(report)
    review_payload["status"] = "READY_FOR_REVIEW"
    reviewed = request_json(
        "PUT", f"{report_url}/status", review_payload, COMPLIANCE_HEADERS
    )
    assert reviewed["status"] == "READY_FOR_REVIEW"
    assert reviewed["reviewedBy"] == COMPLIANCE_OPERATOR
    submitted = request_json(
        "POST", f"{report_url}/submit", headers=COMPLIANCE_HEADERS
    )
    assert submitted["status"] == "SUBMITTED"
    assert submitted["submittedBy"] == COMPLIANCE_OPERATOR
    assert_db_value(
        "SELECT status FROM str_reports "
        f"WHERE report_id={sql_literal(report['reportId'])};",
        "SUBMITTED",
        "submitted STR persistence",
    )

    topic_advances = {
        "truetrace.kyc.submissions": wait_for_topic_advance(
            kafka_before, "truetrace.kyc.submissions", 7
        ),
        "truetrace.transactions": wait_for_topic_advance(
            kafka_before, "truetrace.transactions", 24
        ),
        "truetrace.findings.deepfake": wait_for_topic_advance(
            kafka_before, "truetrace.findings.deepfake", 7
        ),
        "truetrace.findings.money_trail": wait_for_topic_advance(
            kafka_before, "truetrace.findings.money_trail", 3
        ),
        "truetrace.alerts": wait_for_topic_advance(
            kafka_before, "truetrace.alerts", 2
        ),
        "truetrace.reports.str": wait_for_topic_advance(
            kafka_before, "truetrace.reports.str", 2
        ),
    }

    print("TrueTrace full-stack smoke test passed")
    print(f"KYC approved={approved_id} rejected={rejected_id}")
    print(
        f"AML mule={mule_account} elapsed={elapsed:.2f}s "
        f"alert={alert['alertId']} report={report['reportId']}"
    )
    print(f"Kafka topic advances={topic_advances}")
    print(
        "Verified structuring "
        f"source={structurer_account} alert={struct_alert['alertId']} "
        f"report={struct_report['reportId']}"
    )
    print(
        "Recording-ready structuring "
        f"username={manual_structurer} password={password} "
        f"source={manual_structurer_account} targets={manual_targets}"
    )


if __name__ == "__main__":
    main()
