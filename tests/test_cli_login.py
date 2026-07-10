from pathlib import Path

from wzry_ams import cli_login
from wzry_ams.credentials import CredentialStore
from wzry_ams.login import LoginResult, LoginStatus


def test_login_cli_saves_credentials_without_printing_secret_values(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    path = tmp_path / "credentials.json"
    cookies = {
        "openid": "SENSITIVE-OPENID",
        "access_token": "SENSITIVE-TOKEN",
        "appid": "101491592",
        "acctype": "qc",
        "iegams_milo_proxylogin_qc": "SENSITIVE-PROXY",
        "a20161115tyf_tyinfo": (
            "zf_openid,OFFICIAL@ty_openid,EXPERIENCE@"
            "zf_area,1@zf_partition,1306"
        ),
    }
    monkeypatch.setattr(
        cli_login,
        "scan_login",
        lambda timeout: LoginResult(LoginStatus.SUCCESS, cookies, "登录成功"),
    )

    exit_code = cli_login.main(["--output", str(path), "--timeout", "5"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "access_token: present" in output
    assert "SENSITIVE-TOKEN" not in output
    saved = CredentialStore(path).load(required=True)
    assert saved.values["openid"] == "SENSITIVE-OPENID"
    assert saved.is_exchange_ready is True


def test_login_cli_does_not_write_failed_login(tmp_path: Path, monkeypatch, capsys):
    path = tmp_path / "credentials.json"
    monkeypatch.setattr(
        cli_login,
        "scan_login",
        lambda timeout: LoginResult(LoginStatus.TIMEOUT, {}, "timeout"),
    )

    exit_code = cli_login.main(["--output", str(path)])

    assert exit_code == 1
    assert path.exists() is False
    assert "timeout" in capsys.readouterr().err


def test_login_cli_rejects_incomplete_success_result(tmp_path: Path, monkeypatch, capsys):
    path = tmp_path / "credentials.json"
    monkeypatch.setattr(
        cli_login,
        "scan_login",
        lambda timeout: LoginResult(
            LoginStatus.SUCCESS,
            {
                "openid": "OPENID",
                "access_token": "TOKEN",
                "appid": "101491592",
                "acctype": "qc",
            },
            "登录成功",
        ),
    )

    exit_code = cli_login.main(["--output", str(path)])

    assert exit_code == 2
    assert path.exists() is False
    assert "Credential Bundle" in capsys.readouterr().err
