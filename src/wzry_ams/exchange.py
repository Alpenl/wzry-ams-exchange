"""Typed reward redemption module for the Tencent AMS activity."""

from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import requests

from .credentials import Credentials

AMS_BASE = "https://smoba.ams.game.qq.com/ams/ame/amesvr"
AMS_COOKIE_DOMAIN = "smoba.ams.game.qq.com"
PVP_PAGE = "https://pvp.qq.com/cp/a20161115tyf/page2.shtml"
ICON_BASE = "//game.gtimg.cn/images/yxzj/cp/a20161115tyf/"
DEFAULT_ACTIVITY_ID = 126433
DEFAULT_SKEY = "a1b2c3"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class Reward:
    id: str
    name: str
    flow_id: int
    cost: int
    icon: str


REWARDS: dict[str, Reward] = {
    "1": Reward("1", "亲密玫瑰", 407551, 40, "pic_dj4.png"),
    "2": Reward("2", "大型钻石福袋", 407552, 50, "pic_dj5.png"),
    "3": Reward("3", "星币福袋", 407553, 60, "pic_dj7.png"),
    "4": Reward("4", "碎片福袋", 407554, 80, "pic_dj6.png"),
    "5": Reward("5", "浓情玫瑰", 407555, 80, "pic_dj8.png"),
    "6": Reward("6", "体验服专属头像框", 407556, 900, "pic_dj9.png"),
}


class OutcomeKind(str, Enum):
    REDEEMED = "redeemed"
    ALREADY_SATISFIED = "already_satisfied"
    REJECTED = "rejected"
    AUTHENTICATION_FAILED = "authentication_failed"
    TRANSIENT_FAILURE = "transient_failure"
    PROTOCOL_FAILURE = "protocol_failure"
    INVALID_REWARD = "invalid_reward"


SATISFIED_KINDS = {OutcomeKind.REDEEMED, OutcomeKind.ALREADY_SATISFIED}


@dataclass(frozen=True, slots=True)
class RedemptionOutcome:
    reward_id: str
    kind: OutcomeKind
    message: str
    reward: Reward | None = None
    package: str = ""
    raw: Mapping[str, Any] | None = field(default=None, repr=False, compare=False)

    @property
    def satisfied(self) -> bool:
        return self.kind in SATISFIED_KINDS

    @property
    def retryable(self) -> bool:
        return self.kind is OutcomeKind.TRANSIENT_FAILURE

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "reward_id": self.reward_id,
            "reward": self.reward.name if self.reward else "",
            "cost": self.reward.cost if self.reward else None,
            "kind": self.kind.value,
            "satisfied": self.satisfied,
            "retryable": self.retryable,
            "message": self.message,
            "package": self.package,
        }
        if include_raw and self.raw is not None:
            data["raw"] = dict(self.raw)
        return data


@dataclass(frozen=True, slots=True)
class ExchangeReport:
    outcomes: tuple[RedemptionOutcome, ...]

    @property
    def satisfied(self) -> bool:
        return bool(self.outcomes) and all(outcome.satisfied for outcome in self.outcomes)

    @property
    def failed_count(self) -> int:
        return sum(not outcome.satisfied for outcome in self.outcomes)

    @property
    def exit_code(self) -> int:
        return 0 if self.satisfied else 1

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "failed_count": self.failed_count,
            "outcomes": [
                outcome.to_dict(include_raw=include_raw) for outcome in self.outcomes
            ],
        }

    def to_markdown(self) -> str:
        rows = [
            "## Daily exchange report",
            "",
            "| Reward | Outcome | Message |",
            "| --- | --- | --- |",
        ]
        for outcome in self.outcomes:
            reward_name = outcome.reward.name if outcome.reward else outcome.reward_id
            message = outcome.message.replace("|", "\\|").replace("\n", " ")
            rows.append(f"| {reward_name} | `{outcome.kind.value}` | {message} |")
        rows.extend(
            [
                "",
                f"**Plan satisfied:** {'yes' if self.satisfied else 'no'}",
                "",
            ]
        )
        return "\n".join(rows)


class AmsError(RuntimeError):
    """Base class for failures at the AMS seam."""


class AmsTransportError(AmsError):
    """The AMS endpoint could not be reached reliably."""


class AmsProtocolError(AmsError):
    """The AMS endpoint returned an unusable protocol response."""


class AmsAuthenticationError(AmsError):
    """The AMS endpoint rejected the HTTP authentication context."""


class AmsPort(Protocol):
    def redeem(self, reward: Reward, skey: str) -> Mapping[str, Any]: ...


def g_tk(skey: str = DEFAULT_SKEY) -> int:
    """Return the AME CSRF token used by flowengine.js."""
    value = 5381
    for char in skey:
        value += (value << 5) + ord(char)
    return value & 0x7FFFFFFF


def _default_sdid() -> str:
    return hashlib.md5(str(random.random()).encode()).hexdigest().upper()


class RequestsAmsAdapter:
    """Production adapter for the external Tencent AMS endpoint."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.time,
        sdid_factory: Callable[[], str] = _default_sdid,
        activity_id: int = DEFAULT_ACTIVITY_ID,
    ):
        credentials.require_exchange_ready()
        self._credentials = credentials
        identity = credentials.activity_identity
        if identity is None:  # require_exchange_ready() guards this invariant.
            raise ValueError("Credential Bundle has no Activity Identity")
        self._identity = identity
        self._clock = clock
        self._sdid_factory = sdid_factory
        self._activity_id = activity_id
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Origin": "https://pvp.qq.com",
                "Referer": PVP_PAGE,
            }
        )
        for key, value in credentials.values.items():
            self._session.cookies.set(key, value, domain=AMS_COOKIE_DOMAIN, path="/")

    def redeem(self, reward: Reward, skey: str) -> Mapping[str, Any]:
        params = {
            "ameVersion": "0.3",
            "sServiceType": "yxzj",
            "iActivityId": str(self._activity_id),
            "sServiceDepartment": "group_g",
            "sSDID": self._sdid_factory(),
            "isXhrPost": "true",
        }
        data = {
            "iActivityId": str(self._activity_id),
            "iFlowId": str(reward.flow_id),
            "g_tk": str(g_tk(skey)),
            "sArea": self._identity.area,
            "sPartition": self._identity.partition,
            "sPlatId": "1",
            "iUin": self._identity.official_openid,
            "ty_openid": self._identity.experience_openid,
            "sAMSTimestamp": str(int(self._clock())),
        }

        try:
            response = self._session.post(
                AMS_BASE,
                params=params,
                data=data,
                timeout=30,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except (requests.Timeout, requests.ConnectionError) as error:
            raise AmsTransportError(str(error)) from error
        except requests.RequestException as error:
            raise AmsTransportError(str(error)) from error

        if response.status_code in {401, 403}:
            raise AmsAuthenticationError(f"AMS HTTP {response.status_code}")
        if response.status_code == 429 or response.status_code >= 500:
            raise AmsTransportError(f"AMS HTTP {response.status_code}")
        if response.status_code >= 400:
            raise AmsProtocolError(f"AMS HTTP {response.status_code}")

        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError) as error:
            raise AmsProtocolError("AMS response is not valid JSON") from error
        if not isinstance(payload, Mapping):
            raise AmsProtocolError("AMS response root is not an object")
        return payload


_ALREADY_SATISFIED_MESSAGES = (
    "已经兑换过",
    "已兑换过",
    "只能兑换一次",
    "已经领取过",
    "已领取过",
    "请勿重复兑换",
    "重复领取",
    "今天已经兑换",
    "今天已兑换",
    "今日已经兑换",
    "今日已兑换",
    "今天已经领取",
    "今天已领取",
    "今日已经领取",
    "今日已领取",
)
_NOT_SATISFIED_MESSAGES = (
    "兑换完",
    "领取完",
    "库存不足",
)
_AUTHENTICATION_MESSAGES = (
    "登录态",
    "未登录",
    "请登录",
    "重新登录",
    "登录失效",
    "access_token",
    "cookie",
    "授权失效",
)


def _message_from_response(response: Mapping[str, Any]) -> str:
    for key in ("modRet", "flowRet"):
        value = response.get(key)
        if isinstance(value, Mapping):
            message = value.get("sMsg")
            if isinstance(message, str) and message:
                return message
    return "AMS returned an unknown result"


def _package_from_response(response: Mapping[str, Any]) -> str:
    mod = response.get("modRet")
    if not isinstance(mod, Mapping):
        return ""
    package = mod.get("sPackageName")
    if isinstance(package, str):
        return package
    data = mod.get("jData")
    if isinstance(data, Mapping) and isinstance(data.get("sPackageName"), str):
        return str(data["sPackageName"])
    return ""


def _successful_response(response: Mapping[str, Any]) -> bool:
    mod = response.get("modRet")
    flow = response.get("flowRet")
    if not isinstance(mod, Mapping) or not isinstance(flow, Mapping):
        raise AmsProtocolError("AMS response is missing modRet or flowRet")
    return str(mod.get("iRet")) == "0" and str(flow.get("iRet")) == "0"


def _already_satisfied_message(message: str) -> bool:
    normalized = message.casefold()
    if any(token.casefold() in normalized for token in _NOT_SATISFIED_MESSAGES):
        return False
    return any(token.casefold() in normalized for token in _ALREADY_SATISFIED_MESSAGES)


class ExchangeClient:
    """Deep module that turns AMS responses into domain outcomes."""

    def __init__(
        self,
        credentials: Credentials | Mapping[str, str],
        *,
        adapter: AmsPort | None = None,
        skey: str | None = None,
    ):
        if not isinstance(credentials, Credentials):
            credentials = Credentials.from_mapping(credentials)
        self.credentials = credentials.require_exchange_ready()
        self.skey = (
            skey
            if skey is not None
            else self.credentials.values.get("skey") or DEFAULT_SKEY
        )
        self._adapter = adapter or RequestsAmsAdapter(self.credentials)

    @property
    def iuin(self) -> str:
        identity = self.credentials.activity_identity
        return identity.official_openid if identity else ""

    @property
    def ty_openid(self) -> str:
        identity = self.credentials.activity_identity
        return identity.experience_openid if identity else ""

    @property
    def area(self) -> str:
        identity = self.credentials.activity_identity
        return identity.area if identity else ""

    @property
    def partition(self) -> str:
        identity = self.credentials.activity_identity
        return identity.partition if identity else ""

    @property
    def exp_voucher(self) -> str:
        return self.credentials.experience_voucher

    def redeem(self, reward_id: str) -> RedemptionOutcome:
        reward = REWARDS.get(str(reward_id))
        if reward is None:
            return RedemptionOutcome(
                reward_id=str(reward_id),
                kind=OutcomeKind.INVALID_REWARD,
                message=f"无效奖励: {reward_id}",
            )

        try:
            response = self._adapter.redeem(reward, self.skey)
            success = _successful_response(response)
        except AmsAuthenticationError as error:
            return RedemptionOutcome(
                reward_id=reward.id,
                reward=reward,
                kind=OutcomeKind.AUTHENTICATION_FAILED,
                message=str(error),
            )
        except AmsTransportError as error:
            return RedemptionOutcome(
                reward_id=reward.id,
                reward=reward,
                kind=OutcomeKind.TRANSIENT_FAILURE,
                message=str(error),
            )
        except AmsProtocolError as error:
            return RedemptionOutcome(
                reward_id=reward.id,
                reward=reward,
                kind=OutcomeKind.PROTOCOL_FAILURE,
                message=str(error),
            )

        message = _message_from_response(response)
        if success:
            kind = OutcomeKind.REDEEMED
        elif _already_satisfied_message(message):
            kind = OutcomeKind.ALREADY_SATISFIED
        elif any(token.casefold() in message.casefold() for token in _AUTHENTICATION_MESSAGES):
            kind = OutcomeKind.AUTHENTICATION_FAILED
        else:
            kind = OutcomeKind.REJECTED

        return RedemptionOutcome(
            reward_id=reward.id,
            reward=reward,
            kind=kind,
            message=message,
            package=_package_from_response(response),
            raw=dict(response),
        )

    def redeem_many(self, reward_ids: Iterable[str]) -> ExchangeReport:
        return ExchangeReport(tuple(self.redeem(str(reward_id)) for reward_id in reward_ids))

    def redeem_all(self) -> ExchangeReport:
        return self.redeem_many(REWARDS)


def reward_catalog() -> tuple[Reward, ...]:
    return tuple(REWARDS.values())


# Compatibility data for callers that only render the catalog.
REWARD_MAP = {
    reward.id: {
        "name": reward.name,
        "flowId": reward.flow_id,
        "cost": reward.cost,
        "icon": reward.icon,
    }
    for reward in REWARDS.values()
}
