from datetime import date
from pathlib import Path

import pytest
import requests

from wzry_ams.watchdog import (
    DailyRunGuard,
    GitHubActionsAdapter,
    GuardDecision,
    WorkflowRun,
)


class FakeWorkflowRuns:
    def __init__(self, runs: list[WorkflowRun]):
        self.runs = runs
        self.dispatch_count = 0

    def list_runs(self, target_date: date) -> list[WorkflowRun]:
        return self.runs

    def dispatch(self) -> None:
        self.dispatch_count += 1


def run(*, status: str = "completed", conclusion: str | None = "success") -> WorkflowRun:
    return WorkflowRun(1, status, conclusion, "schedule", "2026-07-10T01:17:00Z", "url")


def test_guard_accepts_successful_daily_run():
    port = FakeWorkflowRuns([run()])

    result = DailyRunGuard(port).ensure(date(2026, 7, 10), dispatch=True)

    assert result.decision is GuardDecision.SATISFIED
    assert port.dispatch_count == 0


def test_guard_does_not_duplicate_active_run():
    port = FakeWorkflowRuns([run(status="in_progress", conclusion=None)])

    result = DailyRunGuard(port).ensure(date(2026, 7, 10), dispatch=True)

    assert result.decision is GuardDecision.ACTIVE
    assert port.dispatch_count == 0


def test_guard_reports_missing_in_dry_run_mode():
    port = FakeWorkflowRuns([])

    result = DailyRunGuard(port).ensure(date(2026, 7, 10))

    assert result.decision is GuardDecision.MISSING
    assert result.exit_code == 1
    assert port.dispatch_count == 0


def test_guard_dispatches_one_compensating_run():
    port = FakeWorkflowRuns([])

    result = DailyRunGuard(port).ensure(date(2026, 7, 10), dispatch=True)

    assert result.decision is GuardDecision.DISPATCHED
    assert port.dispatch_count == 1


def test_guard_stops_after_attempt_limit():
    port = FakeWorkflowRuns(
        [
            run(conclusion="failure"),
            run(conclusion="failure"),
        ]
    )

    result = DailyRunGuard(port, max_attempts=2).ensure(date(2026, 7, 10), dispatch=True)

    assert result.decision is GuardDecision.EXHAUSTED
    assert result.exit_code == 2
    assert port.dispatch_count == 0


class FakeResponse:
    def __init__(self, payload: object | None = None):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


class FakeGitHubSession:
    def __init__(
        self,
        *,
        workflow_runs: list[dict[str, object]] | None = None,
        get_failures: int = 0,
        post_failures: int = 0,
    ):
        self.headers: dict[str, str] = {}
        self.get_request: tuple[str, dict[str, object]] | None = None
        self.post_request: tuple[str, dict[str, object]] | None = None
        self.get_count = 0
        self.post_count = 0
        self.get_failures = get_failures
        self.post_failures = post_failures
        self.workflow_runs = workflow_runs or [
            {
                "id": 42,
                "status": "completed",
                "conclusion": "success",
                "event": "schedule",
                "created_at": "2026-07-10T01:17:00Z",
                "html_url": "https://example.test/run/42",
            }
        ]

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.get_count += 1
        self.get_request = (url, kwargs)
        if self.get_count <= self.get_failures:
            raise requests.ConnectionError("temporary read failure")
        return FakeResponse({"workflow_runs": self.workflow_runs})

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.post_count += 1
        self.post_request = (url, kwargs)
        if self.post_count <= self.post_failures:
            raise requests.ConnectionError("temporary dispatch failure")
        return FakeResponse()


class HttpErrorGitHubSession(FakeGitHubSession):
    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.get_count += 1
        response = requests.Response()
        response.status_code = 401
        raise requests.HTTPError("unauthorized", response=response)


def test_github_adapter_uses_run_history_as_the_daily_ledger():
    session = FakeGitHubSession()
    adapter = GitHubActionsAdapter(
        "owner/repo",
        "daily-exchange.yml",
        token="TOKEN",
        session=session,  # type: ignore[arg-type]
    )

    runs = adapter.list_runs(date(2026, 7, 10))

    assert runs == [
        WorkflowRun(
            42,
            "completed",
            "success",
            "schedule",
            "2026-07-10T01:17:00Z",
            "https://example.test/run/42",
        )
    ]
    assert session.get_request is not None
    assert session.get_request[1]["params"] == {
        "created": "2026-07-09T16:00:00Z..2026-07-10T16:00:00Z",
        "per_page": 100,
    }
    assert session.headers["Authorization"] == "Bearer TOKEN"


def test_github_adapter_filters_exact_utc_boundaries_for_shanghai_day():
    session = FakeGitHubSession(
        workflow_runs=[
            {"id": 1, "created_at": "2026-07-09T15:59:59Z"},
            {"id": 2, "created_at": "2026-07-09T16:00:00Z"},
            {"id": 3, "created_at": "2026-07-10T15:59:59Z"},
            {"id": 4, "created_at": "2026-07-10T16:00:00Z"},
        ]
    )
    adapter = GitHubActionsAdapter(
        "owner/repo",
        "daily-exchange.yml",
        session=session,  # type: ignore[arg-type]
    )

    runs = adapter.list_runs(date(2026, 7, 10))

    assert [item.id for item in runs] == [2, 3]


def test_github_adapter_retries_transient_read_failures_with_bounded_backoff():
    session = FakeGitHubSession(get_failures=2)
    sleeps: list[float] = []
    adapter = GitHubActionsAdapter(
        "owner/repo",
        "daily-exchange.yml",
        session=session,  # type: ignore[arg-type]
        request_attempts=3,
        retry_delay=0.25,
        sleep=sleeps.append,
    )

    runs = adapter.list_runs(date(2026, 7, 10))

    assert len(runs) == 1
    assert session.get_count == 3
    assert sleeps == [0.25, 0.5]


def test_github_adapter_stops_read_retries_at_attempt_limit():
    session = FakeGitHubSession(get_failures=3)
    sleeps: list[float] = []
    adapter = GitHubActionsAdapter(
        "owner/repo",
        "daily-exchange.yml",
        session=session,  # type: ignore[arg-type]
        request_attempts=2,
        retry_delay=0.25,
        sleep=sleeps.append,
    )

    with pytest.raises(requests.ConnectionError, match="temporary read failure"):
        adapter.list_runs(date(2026, 7, 10))

    assert session.get_count == 2
    assert sleeps == [0.25]


def test_github_adapter_does_not_retry_non_transient_http_errors():
    session = HttpErrorGitHubSession()
    sleeps: list[float] = []
    adapter = GitHubActionsAdapter(
        "owner/repo",
        "daily-exchange.yml",
        session=session,  # type: ignore[arg-type]
        sleep=sleeps.append,
    )

    with pytest.raises(requests.HTTPError, match="unauthorized"):
        adapter.list_runs(date(2026, 7, 10))

    assert session.get_count == 1
    assert sleeps == []


def test_github_adapter_fails_closed_on_invalid_created_at():
    session = FakeGitHubSession(workflow_runs=[{"id": 1, "created_at": "not-a-time"}])
    adapter = GitHubActionsAdapter(
        "owner/repo",
        "daily-exchange.yml",
        session=session,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="invalid created_at"):
        adapter.list_runs(date(2026, 7, 10))


def test_github_adapter_dispatches_the_configured_ref():
    session = FakeGitHubSession()
    adapter = GitHubActionsAdapter(
        "owner/repo",
        "daily-exchange.yml",
        token="TOKEN",
        ref="main",
        session=session,  # type: ignore[arg-type]
    )

    adapter.dispatch()

    assert session.post_request is not None
    assert session.post_request[1]["json"] == {"ref": "main"}


def test_github_adapter_retries_transient_dispatch_failure():
    session = FakeGitHubSession(post_failures=1)
    sleeps: list[float] = []
    adapter = GitHubActionsAdapter(
        "owner/repo",
        "daily-exchange.yml",
        token="TOKEN",
        session=session,  # type: ignore[arg-type]
        request_attempts=2,
        retry_delay=0.25,
        sleep=sleeps.append,
    )

    adapter.dispatch()

    assert session.post_count == 2
    assert sleeps == [0.25]


def test_systemd_timer_runs_only_at_the_two_shanghai_checkpoints():
    timer = (
        Path(__file__).parents[1] / "ops/systemd/wzry-watchdog.timer"
    ).read_text()

    assert timer.count("OnCalendar=") == 2
    assert "OnCalendar=*-*-* 11:30:00 Asia/Shanghai" in timer
    assert "OnCalendar=*-*-* 13:30:00 Asia/Shanghai" in timer
    assert "Persistent=" not in timer
    assert "RandomizedDelaySec=" not in timer


def test_systemd_user_service_has_no_ineffective_network_or_boot_hook():
    service = (
        Path(__file__).parents[1] / "ops/systemd/wzry-watchdog.service"
    ).read_text()

    assert "network-online.target" not in service
    assert "[Install]" not in service
    assert "${WZRY_PROJECT_DIR}/.venv/bin/wzry-watchdog" in service
    assert "%h/.local/bin/wzry-watchdog" not in service


def test_daily_dispatch_timer_runs_once_at_0917_shanghai():
    timer = (
        Path(__file__).parents[1] / "ops/systemd/wzry-daily-dispatch.timer"
    ).read_text()

    assert timer.count("OnCalendar=") == 1
    assert "OnCalendar=*-*-* 09:17:00 Asia/Shanghai" in timer
    assert "Unit=wzry-daily-dispatch.service" in timer
    assert "Persistent=" not in timer
    assert "RandomizedDelaySec=" not in timer


def test_daily_dispatch_service_uses_the_authenticated_gh_cli():
    service = (
        Path(__file__).parents[1] / "ops/systemd/wzry-daily-dispatch.service"
    ).read_text()

    assert "Type=oneshot" in service
    assert "TimeoutStartSec=2min" in service
    assert (
        "ExecStart=/usr/bin/gh workflow run daily-exchange.yml "
        "--repo Alpenl/wzry-ams-exchange --ref master"
    ) in service
    assert "[Install]" not in service


def test_daily_workflow_is_dispatch_only_and_never_sleeps():
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/daily-exchange.yml"
    ).read_text()

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "random_delay" not in workflow
    assert "RANDOM" not in workflow
    assert "sleep " not in workflow
    assert "timeout-minutes: 15" in workflow
    assert "wzry-exchange -c cookies.txt -r 3 -r 4" in workflow
    assert "--all" not in workflow
