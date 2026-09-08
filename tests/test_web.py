from pathlib import Path

from fastapi.testclient import TestClient

from wzry_ams.credentials import Credentials, CredentialStore
from wzry_ams.exchange import REWARDS, OutcomeKind, RedemptionOutcome
from wzry_ams.web import create_app


def valid_credentials() -> Credentials:
    return Credentials.from_mapping(
        {
            "openid": "OPENID",
            "access_token": "TOKEN",
            "appid": "101491592",
            "acctype": "qc",
            "iegams_milo_proxylogin_qc": "PROXY",
            "a20161115tyf_tyinfo": (
                "zf_openid,OFFICIAL@ty_openid,EXPERIENCE@"
                "zf_area,1@zf_partition,1306@exp_voucher,9981"
            ),
        }
    )


class FakeClient:
    def __init__(self, outcome: RedemptionOutcome):
        self.outcome = outcome

    def redeem(self, reward_id: str) -> RedemptionOutcome:
        assert reward_id == self.outcome.reward_id
        return self.outcome


def test_homepage_renders_valid_template_and_explicit_button_target(tmp_path: Path):
    app = create_app(
        cookie_file=tmp_path / "credentials.json",
        log_file=tmp_path / "exchange.log",
    )

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "{{" not in response.text
    assert "/static/app.js" in response.text
    script = TestClient(app).get("/static/app.js").text
    assert "async function init() {" in script
    assert "async function exchange(id, btn) {" in script
    assert "const btn = event.target" not in script


def test_status_without_credentials_is_logged_out(tmp_path: Path):
    app = create_app(
        cookie_file=tmp_path / "credentials.json",
        log_file=tmp_path / "exchange.log",
    )

    response = TestClient(app).get("/api/status")

    assert response.status_code == 200
    assert response.json()["has_cookie"] is False
    assert response.json()["exchange_ready"] is False


def test_cookie_save_validates_before_writing(tmp_path: Path):
    path = tmp_path / "credentials.json"
    app = create_app(cookie_file=path, log_file=tmp_path / "exchange.log")

    response = TestClient(app).post("/api/cookies", json={"raw": "openid=ONLY"})

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert path.exists() is False


def test_status_redacts_identity_and_reports_readiness(tmp_path: Path):
    path = tmp_path / "credentials.json"
    CredentialStore(path).replace(valid_credentials())
    app = create_app(cookie_file=path, log_file=tmp_path / "exchange.log")

    response = TestClient(app).get("/api/status")

    assert response.json() == {
        "has_cookie": True,
        "exchange_ready": True,
        "openid": "OPENID...",
        "acctype": "qc",
        "exp_voucher": "9981",
        "area": "1",
        "partition": "1306",
    }


def test_exchange_returns_typed_outcome_without_raw_response(tmp_path: Path):
    credential_path = tmp_path / "credentials.json"
    log_path = tmp_path / "exchange.log"
    CredentialStore(credential_path).replace(valid_credentials())
    outcome = RedemptionOutcome(
        reward_id="3",
        reward=REWARDS["3"],
        kind=OutcomeKind.REDEEMED,
        message="兑换成功",
        package="星币福袋",
        raw={"secret": "upstream payload"},
    )
    app = create_app(
        cookie_file=credential_path,
        log_file=log_path,
        client_factory=lambda credentials: FakeClient(outcome),
    )

    response = TestClient(app).post("/api/exchange", json={"reward": "3"})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["kind"] == "redeemed"
    assert "raw" not in response.json()
    assert "兑换成功" in log_path.read_text(encoding="utf-8")
    assert "upstream payload" not in log_path.read_text(encoding="utf-8")


def test_log_page_uses_text_content_for_untrusted_ams_messages(tmp_path: Path):
    credential_path = tmp_path / "credentials.json"
    log_path = tmp_path / "exchange.log"
    CredentialStore(credential_path).replace(valid_credentials())
    malicious = '<img src=x onerror="document.body.dataset.pwned=1">'
    outcome = RedemptionOutcome(
        reward_id="3",
        reward=REWARDS["3"],
        kind=OutcomeKind.REJECTED,
        message=malicious,
    )
    app = create_app(
        cookie_file=credential_path,
        log_file=log_path,
        client_factory=lambda credentials: FakeClient(outcome),
    )
    client = TestClient(app)

    exchange_response = client.post("/api/exchange", json={"reward": "3"})
    log_response = client.get("/api/log")
    page = client.get("/static/app.js").text

    assert exchange_response.status_code == 409
    assert log_response.json()["logs"][0]["msg"] == malicious
    assert "td.textContent=row.message" in page
    assert "'+l.msg+'</div>'" not in page


def test_exchange_requires_exchange_ready_credentials(tmp_path: Path):
    path = tmp_path / "credentials.json"
    CredentialStore(path).replace(
        Credentials.from_mapping(
            {
                "openid": "OPENID",
                "access_token": "TOKEN",
                "appid": "101491592",
                "acctype": "qc",
            }
        )
    )
    app = create_app(cookie_file=path, log_file=tmp_path / "exchange.log")

    response = TestClient(app).post("/api/exchange", json={"reward": "1"})

    assert response.status_code == 401
    assert response.json()["satisfied"] is False
