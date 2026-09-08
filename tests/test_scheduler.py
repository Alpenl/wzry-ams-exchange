import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from tests.test_web import valid_credentials
from wzry_ams.credentials import CredentialStore
from wzry_ams.exchange import OutcomeKind, RedemptionOutcome
from wzry_ams.scheduler import DailyScheduler
from wzry_ams.web import create_app


def now(day=8, hour=10, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_restart_preserves_success_and_only_catches_up_today(tmp_path):
    cookies = tmp_path / "cookies.txt"
    CredentialStore(cookies).replace(valid_credentials())
    calls = []

    def redeem(credentials, reward):
        calls.append(reward)
        return RedemptionOutcome(reward, OutcomeKind.REDEEMED, "ok")

    scheduler = DailyScheduler(cookies, tmp_path / "state.db", redeem=redeem)
    scheduler.tick(now(hour=9, minute=16))
    assert calls == []
    scheduler.tick(now())
    restarted = DailyScheduler(cookies, tmp_path / "state.db", redeem=redeem)
    restarted.tick(now())
    assert calls == ["3", "4"]
    restarted.tick(now(day=10))
    assert calls == ["3", "4", "3", "4"]
    assert {r["day"] for r in restarted.status()["runs"]} == {"2026-09-08", "2026-09-10"}


def test_network_retries_bounded_and_auth_retries_after_rotation(tmp_path):
    cookies = tmp_path / "cookies.txt"
    CredentialStore(cookies).replace(valid_credentials())
    calls = []

    def redeem(credentials, reward):
        calls.append(reward)
        kind = OutcomeKind.TRANSIENT_FAILURE if reward == "3" else OutcomeKind.AUTHENTICATION_FAILED
        return RedemptionOutcome(reward, kind, "failed")

    scheduler = DailyScheduler(cookies, tmp_path / "state.db", redeem=redeem)
    for minute in (0, 1, 5, 10, 15, 20):
        scheduler.tick(now(minute=minute))
    assert calls.count("3") == 4
    assert calls.count("4") == 1
    credentials = valid_credentials().to_dict()
    credentials["access_token"] = "ROTATED"
    CredentialStore(cookies).replace(json.dumps(credentials))
    scheduler.tick(now(minute=21))
    assert calls.count("4") == 2


def test_missing_credentials_recovers_after_upload(tmp_path):
    cookies = tmp_path / "cookies.txt"
    scheduler = DailyScheduler(
        cookies,
        tmp_path / "state.db",
        redeem=lambda c, r: RedemptionOutcome(r, OutcomeKind.REDEEMED, "ok"),
    )
    scheduler.tick(now())
    assert scheduler.status()["error"]
    CredentialStore(cookies).replace(valid_credentials())
    scheduler.tick(now())
    assert scheduler.status()["error"] is None
    assert len(scheduler.status()["runs"]) == 2


def test_password_protects_page_and_mutations_but_not_health(tmp_path: Path):
    app = create_app(cookie_file=tmp_path / "cookies.txt", password="test-password")
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        for path in ("/", "/api/status", "/api/schedule", "/api/log"):
            assert client.get(path).status_code == 401
            assert client.get(path, auth=("admin", "test-password")).status_code == 200
        assert client.post("/api/cookies", json={"raw": "x=y"}).status_code == 401
        assert client.delete("/api/cookies").status_code == 401
