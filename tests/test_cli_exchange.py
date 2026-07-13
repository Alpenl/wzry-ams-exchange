import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from wzry_ams.cli_exchange import main
from wzry_ams.credentials import Credentials, CredentialStore
from wzry_ams.exchange import ExchangeClient, Reward


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
                "zf_area,1@zf_partition,1306"
            ),
        }
    )


class FakeAmsAdapter:
    def __init__(self, responses: list[Mapping[str, Any]]):
        self.responses = list(responses)
        self.reward_ids: list[str] = []

    def redeem(self, reward: Reward, skey: str) -> Mapping[str, Any]:
        del skey
        self.reward_ids.append(reward.id)
        return self.responses.pop(0)


def response(*, success: bool, message: str) -> dict[str, Any]:
    code = 0 if success else 1
    return {
        "flowRet": {"iRet": str(code)},
        "modRet": {"iRet": code, "sMsg": message},
    }


def write_credentials(path: Path) -> None:
    CredentialStore(path).replace(valid_credentials())


def test_list_does_not_require_credentials(capsys):
    exit_code = main(["--list"])

    assert exit_code == 0
    assert "星币福袋" in capsys.readouterr().out


def test_all_returns_zero_for_redeemed_and_already_satisfied(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    path = tmp_path / "credentials.json"
    summary = tmp_path / "summary.md"
    write_credentials(path)
    adapter = FakeAmsAdapter(
        [response(success=True, message="兑换成功")] * 5
        + [response(success=False, message="每期活动只能兑换一次该奖励")]
    )
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    exit_code = main(
        ["--cookies", str(path), "--all"],
        client_factory=lambda credentials: ExchangeClient(credentials, adapter=adapter),
    )

    assert exit_code == 0
    assert "[SATISFIED]" in capsys.readouterr().out
    assert "**Plan satisfied:** yes" in summary.read_text(encoding="utf-8")


def test_json_output_and_exit_code_are_truthful(tmp_path: Path, capsys):
    path = tmp_path / "credentials.json"
    write_credentials(path)
    adapter = FakeAmsAdapter([response(success=False, message="体验币不足")])

    exit_code = main(
        ["--cookies", str(path), "--reward", "1", "--json"],
        client_factory=lambda credentials: ExchangeClient(credentials, adapter=adapter),
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["satisfied"] is False
    assert payload["outcomes"][0]["kind"] == "rejected"


def test_repeated_reward_options_build_one_ordered_exchange_plan(tmp_path: Path, capsys):
    path = tmp_path / "credentials.json"
    write_credentials(path)
    adapter = FakeAmsAdapter(
        [
            response(success=True, message="星币福袋兑换成功"),
            response(success=True, message="碎片福袋兑换成功"),
        ]
    )

    exit_code = main(
        ["--cookies", str(path), "--reward", "3", "--reward", "4"],
        client_factory=lambda credentials: ExchangeClient(credentials, adapter=adapter),
    )

    assert exit_code == 0
    assert adapter.reward_ids == ["3", "4"]
    assert "Plan: SATISFIED (2/2)" in capsys.readouterr().out


def test_missing_credentials_returns_configuration_error(tmp_path: Path, capsys):
    exit_code = main(["--cookies", str(tmp_path / "missing"), "--all"])

    assert exit_code == 2
    assert "Credential Bundle" in capsys.readouterr().err
