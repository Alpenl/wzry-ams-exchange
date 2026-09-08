from pathlib import Path

from fastapi.testclient import TestClient

from wzry_ams.auth import AdminAuth
from wzry_ams.scheduler import DailyScheduler
from wzry_ams.web import create_app


def test_setup_login_logout_and_persisted_hash(tmp_path: Path):
    path = tmp_path / "auth.json"
    client = TestClient(create_app(cookie_file=tmp_path / "cookies.txt", auth=AdminAuth(path)))
    assert client.get("/").status_code == 200
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/auth/status").json()["configured"] is False
    assert client.post("/api/auth/setup", json={"password": "short"}).status_code == 400
    password = "test-admin-password"
    assert client.post("/api/auth/setup", json={"password": password}).status_code == 200
    assert password not in path.read_text()
    assert client.get("/api/status").status_code == 200
    assert client.post("/api/auth/setup", json={"password": password}).status_code == 409
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/status").status_code == 401
    restarted = TestClient(create_app(cookie_file=tmp_path / "cookies.txt", auth=AdminAuth(path)))
    assert restarted.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert restarted.post("/api/auth/login", json={"password": password}).status_code == 200
    assert restarted.get("/api/status").status_code == 200


def test_cross_origin_setup_rejected(tmp_path: Path):
    client = TestClient(create_app(auth=AdminAuth(tmp_path / "auth.json")))
    assert (
        client.post(
            "/api/auth/setup",
            json={"password": "test-admin-password"},
            headers={"Origin": "https://elsewhere.invalid"},
        ).status_code
        == 403
    )
    assert not (tmp_path / "auth.json").exists()


def test_schedule_configuration_survives_restart(tmp_path: Path):
    scheduler = DailyScheduler(tmp_path / "cookies.txt", tmp_path / "state.db")
    client = TestClient(create_app(scheduler=scheduler))
    assert client.put("/api/schedule", json={"enabled": False, "time": "25:99"}).status_code == 400
    assert client.put("/api/schedule", json={"enabled": False, "time": "11:20"}).status_code == 200
    restarted = DailyScheduler(tmp_path / "cookies.txt", tmp_path / "state.db")
    assert restarted.status()["enabled"] is False
    assert restarted.status()["time"] == "11:20"
