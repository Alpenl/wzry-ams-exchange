from collections.abc import Mapping
from typing import Any

import pytest

from wzry_ams.credentials import Credentials
from wzry_ams.exchange import (
    REWARDS,
    AmsProtocolError,
    AmsTransportError,
    ExchangeClient,
    OutcomeKind,
    RequestsAmsAdapter,
    Reward,
)


def valid_credentials() -> Credentials:
    return Credentials.from_mapping(
        {
            "openid": "OPENID",
            "access_token": "TOKEN",
            "appid": "101491592",
            "acctype": "qc",
            "iegams_milo_proxylogin_qc": "PROXY",
            "a20161115tyf_tyinfo": (
                "iRet%2C0@sMsg%2Cok@exp_voucher%2C9981@zf_openid%2CZFO@"
                "zf_area%2C1@zf_partition%2C1306@ty_openid%2CTYO"
            ),
        }
    )


class FakeAmsAdapter:
    def __init__(self, responses: list[Mapping[str, Any] | Exception]):
        self.responses = list(responses)
        self.rewards: list[Reward] = []
        self.skeys: list[str] = []

    def redeem(self, reward: Reward, skey: str) -> Mapping[str, Any]:
        self.rewards.append(reward)
        self.skeys.append(skey)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def success_response(message: str = "兑换成功") -> dict[str, Any]:
    return {
        "flowRet": {"iRet": "0"},
        "modRet": {"iRet": 0, "sMsg": message, "sPackageName": "星币福袋"},
    }


def rejected_response(message: str) -> dict[str, Any]:
    return {
        "flowRet": {"iRet": "1", "sMsg": message},
        "modRet": {"iRet": 1, "sMsg": message},
    }


def test_redeem_classifies_success():
    adapter = FakeAmsAdapter([success_response()])
    client = ExchangeClient(valid_credentials(), adapter=adapter)

    outcome = client.redeem("3")

    assert outcome.kind is OutcomeKind.REDEEMED
    assert outcome.satisfied is True
    assert outcome.reward.id == "3"
    assert outcome.package == "星币福袋"


@pytest.mark.parametrize(
    "message",
    [
        "今日已经兑换过该奖励",
        "每期活动只能兑换一次该奖励",
        "您已领取，请勿重复领取",
        "今天已经兑换该奖励",
        "今日已兑换该奖励",
        "今日已领取该奖励",
    ],
)
def test_redeem_classifies_already_satisfied(message: str):
    adapter = FakeAmsAdapter([rejected_response(message)])
    client = ExchangeClient(valid_credentials(), adapter=adapter)

    outcome = client.redeem("1")

    assert outcome.kind is OutcomeKind.ALREADY_SATISFIED
    assert outcome.satisfied is True


@pytest.mark.parametrize(
    "message",
    [
        "礼包已领取完",
        "今日奖品已经兑换完",
        "今日已兑换完",
        "今日已领取完",
        "库存不足",
    ],
)
def test_redeem_does_not_treat_inventory_exhaustion_as_satisfied(message: str):
    adapter = FakeAmsAdapter([rejected_response(message)])
    client = ExchangeClient(valid_credentials(), adapter=adapter)

    outcome = client.redeem("1")

    assert outcome.kind is OutcomeKind.REJECTED
    assert outcome.satisfied is False


def test_redeem_classifies_authentication_failure():
    adapter = FakeAmsAdapter([rejected_response("登录态已失效，请重新登录")])
    client = ExchangeClient(valid_credentials(), adapter=adapter)

    outcome = client.redeem("1")

    assert outcome.kind is OutcomeKind.AUTHENTICATION_FAILED
    assert outcome.satisfied is False


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (AmsTransportError("timeout"), OutcomeKind.TRANSIENT_FAILURE),
        (AmsProtocolError("not json"), OutcomeKind.PROTOCOL_FAILURE),
    ],
)
def test_redeem_classifies_adapter_failures(error: Exception, kind: OutcomeKind):
    adapter = FakeAmsAdapter([error])
    client = ExchangeClient(valid_credentials(), adapter=adapter)

    outcome = client.redeem("2")

    assert outcome.kind is kind
    assert outcome.satisfied is False


def test_redeem_rejects_unknown_reward_without_calling_adapter():
    adapter = FakeAmsAdapter([])
    client = ExchangeClient(valid_credentials(), adapter=adapter)

    outcome = client.redeem("missing")

    assert outcome.kind is OutcomeKind.INVALID_REWARD
    assert outcome.satisfied is False
    assert adapter.rewards == []


def test_redeem_uses_skey_from_credentials_by_default():
    credentials = Credentials.from_mapping(
        {**valid_credentials().to_dict(), "skey": "credential-skey"}
    )
    adapter = FakeAmsAdapter([success_response()])
    client = ExchangeClient(credentials, adapter=adapter)

    client.redeem("1")

    assert adapter.skeys == ["credential-skey"]


def test_redeem_falls_back_to_default_skey_when_credentials_have_none():
    adapter = FakeAmsAdapter([success_response()])
    client = ExchangeClient(valid_credentials(), adapter=adapter)

    client.redeem("1")

    assert adapter.skeys == ["a1b2c3"]


def test_redeem_explicit_skey_overrides_credentials():
    credentials = Credentials.from_mapping(
        {**valid_credentials().to_dict(), "skey": "credential-skey"}
    )
    adapter = FakeAmsAdapter([success_response()])
    client = ExchangeClient(credentials, adapter=adapter, skey="override-skey")

    client.redeem("1")

    assert adapter.skeys == ["override-skey"]


def test_exchange_report_is_satisfied_for_redeemed_and_already_satisfied():
    adapter = FakeAmsAdapter(
        [
            success_response(),
            rejected_response("每期活动只能兑换一次该奖励"),
        ]
    )
    client = ExchangeClient(valid_credentials(), adapter=adapter)

    report = client.redeem_many(["1", "6"])

    assert report.satisfied is True
    assert report.exit_code == 0
    assert [outcome.kind for outcome in report.outcomes] == [
        OutcomeKind.REDEEMED,
        OutcomeKind.ALREADY_SATISFIED,
    ]


def test_exchange_report_fails_when_any_reward_is_rejected():
    adapter = FakeAmsAdapter(
        [
            success_response(),
            rejected_response("体验币不足"),
        ]
    )
    client = ExchangeClient(valid_credentials(), adapter=adapter)

    report = client.redeem_many(["1", "2"])

    assert report.satisfied is False
    assert report.exit_code == 1
    assert report.failed_count == 1


def test_unexpected_programming_errors_are_not_hidden():
    adapter = FakeAmsAdapter([RuntimeError("bug")])
    client = ExchangeClient(valid_credentials(), adapter=adapter)

    with pytest.raises(RuntimeError, match="bug"):
        client.redeem("1")


class FakeCookies:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.scopes: dict[str, tuple[str, str]] = {}

    def set(self, name: str, value: str, domain: str, path: str) -> None:
        self.values[name] = value
        self.scopes[name] = (domain, path)


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def json(self) -> object:
        return self.payload


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.headers: dict[str, str] = {}
        self.cookies = FakeCookies()
        self.response = response
        self.request: dict[str, Any] | None = None

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.request = {"url": url, **kwargs}
        return self.response


def test_requests_adapter_builds_the_ams_request_from_validated_credentials():
    session = FakeSession(FakeResponse(success_response()))
    adapter = RequestsAmsAdapter(
        valid_credentials(),
        session=session,  # type: ignore[arg-type]
        clock=lambda: 1234567890,
        sdid_factory=lambda: "SDID",
    )

    payload = adapter.redeem(REWARDS["3"], "a1b2c3")

    assert payload == success_response()
    assert session.request is not None
    assert session.request["params"]["sSDID"] == "SDID"
    assert session.request["data"] == {
        "iActivityId": "126433",
        "iFlowId": "407553",
        "g_tk": "1842395457",
        "sArea": "1",
        "sPartition": "1306",
        "sPlatId": "1",
        "iUin": "ZFO",
        "ty_openid": "TYO",
        "sAMSTimestamp": "1234567890",
    }
    assert session.cookies.values["access_token"] == "TOKEN"
    assert session.cookies.scopes["access_token"] == (
        "smoba.ams.game.qq.com",
        "/",
    )


def test_requests_adapter_rejects_non_object_json():
    session = FakeSession(FakeResponse(["unexpected"]))
    adapter = RequestsAmsAdapter(
        valid_credentials(),
        session=session,  # type: ignore[arg-type]
    )

    with pytest.raises(AmsProtocolError, match="root"):
        adapter.redeem(REWARDS["1"], "a1b2c3")


def test_requests_adapter_classifies_rate_limit_as_transient():
    session = FakeSession(FakeResponse({}, status_code=429))
    adapter = RequestsAmsAdapter(
        valid_credentials(),
        session=session,  # type: ignore[arg-type]
    )

    with pytest.raises(AmsTransportError, match="429"):
        adapter.redeem(REWARDS["1"], "a1b2c3")
