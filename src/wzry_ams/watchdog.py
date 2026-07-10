"""External Daily Run Guard for GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import requests

GITHUB_API = "https://api.github.com"
SHANGHAI = ZoneInfo("Asia/Shanghai")
ACTIVE_STATUSES = {"queued", "in_progress", "requested", "waiting", "pending"}
DEFAULT_REQUEST_ATTEMPTS = 3
DEFAULT_RETRY_DELAY = 1.0


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    id: int
    status: str
    conclusion: str | None
    event: str
    created_at: str
    url: str


class GuardDecision(str, Enum):
    SATISFIED = "satisfied"
    ACTIVE = "active"
    MISSING = "missing"
    DISPATCHED = "dispatched"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class GuardResult:
    decision: GuardDecision
    target_date: date
    observed_runs: int
    message: str

    @property
    def exit_code(self) -> int:
        if self.decision in {
            GuardDecision.SATISFIED,
            GuardDecision.ACTIVE,
            GuardDecision.DISPATCHED,
        }:
            return 0
        return 2 if self.decision is GuardDecision.EXHAUSTED else 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "date": self.target_date.isoformat(),
            "observed_runs": self.observed_runs,
            "message": self.message,
        }


class WorkflowRunsPort(Protocol):
    def list_runs(self, target_date: date) -> Sequence[WorkflowRun]: ...

    def dispatch(self) -> None: ...


class GitHubActionsAdapter:
    def __init__(
        self,
        repository: str,
        workflow: str,
        *,
        token: str | None = None,
        ref: str = "master",
        session: requests.Session | None = None,
        request_attempts: int = DEFAULT_REQUEST_ATTEMPTS,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if "/" not in repository:
            raise ValueError("repository must be OWNER/REPO")
        if request_attempts < 1:
            raise ValueError("request_attempts must be at least 1")
        if retry_delay < 0:
            raise ValueError("retry_delay must not be negative")
        self.repository = repository
        self.workflow = workflow
        self.ref = ref
        self._token = token
        self._session = session or requests.Session()
        self._request_attempts = request_attempts
        self._retry_delay = retry_delay
        self._sleep = sleep
        self._session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "wzry-watchdog",
            }
        )
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"

    @property
    def _runs_url(self) -> str:
        return (
            f"{GITHUB_API}/repos/{self.repository}/actions/workflows/"
            f"{self.workflow}/runs"
        )

    @staticmethod
    def _is_retryable(error: requests.RequestException) -> bool:
        if not isinstance(error, requests.HTTPError) or error.response is None:
            return True
        status = error.response.status_code
        return status in {408, 429} or status >= 500

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        request = self._session.get if method == "GET" else self._session.post
        for attempt in range(self._request_attempts):
            try:
                response = request(url, **kwargs)
                response.raise_for_status()
                return response
            except requests.RequestException as error:
                if attempt + 1 >= self._request_attempts or not self._is_retryable(error):
                    raise
                self._sleep(self._retry_delay * (2**attempt))
        raise AssertionError("request retry loop exited unexpectedly")

    def list_runs(self, target_date: date) -> Sequence[WorkflowRun]:
        start_utc, end_utc = _shanghai_day_utc_window(target_date)
        response = self._request(
            "GET",
            self._runs_url,
            params={
                "created": f"{_format_utc(start_utc)}..{_format_utc(end_utc)}",
                "per_page": 100,
            },
            timeout=20,
        )
        payload = response.json()
        if not isinstance(payload, Mapping) or not isinstance(payload.get("workflow_runs"), list):
            raise RuntimeError("GitHub returned an invalid workflow runs response")
        runs: list[WorkflowRun] = []
        for item in payload["workflow_runs"]:
            if not isinstance(item, Mapping):
                continue
            created_at = str(item.get("created_at", ""))
            created_at_utc = _parse_github_datetime(created_at)
            if not start_utc <= created_at_utc < end_utc:
                continue
            runs.append(
                WorkflowRun(
                    id=int(item.get("id", 0)),
                    status=str(item.get("status", "")),
                    conclusion=(
                        str(item["conclusion"]) if item.get("conclusion") is not None else None
                    ),
                    event=str(item.get("event", "")),
                    created_at=created_at,
                    url=str(item.get("html_url", "")),
                )
            )
        return runs

    def dispatch(self) -> None:
        if not self._token:
            raise RuntimeError("GITHUB_TOKEN is required to dispatch a workflow")
        self._request(
            "POST",
            f"{GITHUB_API}/repos/{self.repository}/actions/workflows/{self.workflow}/dispatches",
            json={"ref": self.ref},
            timeout=20,
        )


class DailyRunGuard:
    def __init__(self, port: WorkflowRunsPort, *, max_attempts: int = 2):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._port = port
        self._max_attempts = max_attempts

    def ensure(self, target_date: date, *, dispatch: bool = False) -> GuardResult:
        runs = list(self._port.list_runs(target_date))
        if any(run.conclusion == "success" for run in runs):
            return GuardResult(
                GuardDecision.SATISFIED,
                target_date,
                len(runs),
                "The Daily Run already has a successful Exchange Report.",
            )
        if any(run.status in ACTIVE_STATUSES for run in runs):
            return GuardResult(
                GuardDecision.ACTIVE,
                target_date,
                len(runs),
                "A Daily Run is already active.",
            )
        if len(runs) >= self._max_attempts:
            return GuardResult(
                GuardDecision.EXHAUSTED,
                target_date,
                len(runs),
                "The Daily Run attempt limit has been reached.",
            )
        if not dispatch:
            return GuardResult(
                GuardDecision.MISSING,
                target_date,
                len(runs),
                "No successful or active Daily Run exists; dispatch is required.",
            )
        self._port.dispatch()
        return GuardResult(
            GuardDecision.DISPATCHED,
            target_date,
            len(runs),
            "A compensating Daily Run was dispatched.",
        )


def _shanghai_day_utc_window(target_date: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(target_date, datetime.min.time(), SHANGHAI)
    end_local = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), SHANGHAI)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_github_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError(f"GitHub returned an invalid created_at timestamp: {value!r}") from error
    if parsed.tzinfo is None:
        raise RuntimeError(f"GitHub returned a timezone-naive created_at timestamp: {value!r}")
    return parsed.astimezone(timezone.utc)


def _parse_date(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(SHANGHAI).date()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ensure today's daily exchange workflow exists")
    parser.add_argument(
        "--repo",
        default=os.environ.get("WZRY_GITHUB_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY"),
        help="GitHub repository as OWNER/REPO",
    )
    parser.add_argument(
        "--workflow",
        default=os.environ.get("WZRY_GITHUB_WORKFLOW", "daily-exchange.yml"),
    )
    parser.add_argument("--ref", default=os.environ.get("WZRY_GITHUB_REF", "master"))
    parser.add_argument("--date", help="Asia/Shanghai date (YYYY-MM-DD)")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--dispatch", action="store_true", help="Dispatch when the run is missing")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.repo:
        print("--repo or WZRY_GITHUB_REPOSITORY is required")
        return 2
    adapter = GitHubActionsAdapter(
        args.repo,
        args.workflow,
        token=os.environ.get("WZRY_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN"),
        ref=args.ref,
    )
    try:
        result = DailyRunGuard(adapter, max_attempts=args.max_attempts).ensure(
            _parse_date(args.date),
            dispatch=args.dispatch,
        )
    except (requests.RequestException, RuntimeError, ValueError) as error:
        print(f"watchdog error: {error}")
        return 2
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False))
    else:
        print(f"[{result.decision.value}] {result.message}")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
