"""첫 실행 설정과 연결 상태 (plan 3.2). 화면(`/setup`, `/settings`)이 이것을 부른다.

오래 걸리는 일(GitHub 승인 기다리기, 위키 받기, Claude 설치)은 뒤에서 돌리고, 화면은 상태를 다시
읽어 그린다. 상태는 이 프로세스 메모리에만 있다. 토큰은 키체인에만 둔다 (auth).
"""

import logging
import subprocess
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from context_holder import appsettings, auth, claudesetup, codexsetup, deviceflow
from context_holder.connect import Connected, ConnectError, connect_wiki
from context_holder.tools import tool

log = logging.getLogger(__name__)

STATUS_TTL = 5.0  # claude auth status는 1초쯤 걸린다. 화면이 2초마다 물어도 이만큼은 재사용한다
INSTALL_TIMEOUT = 300

Clock = Callable[[], datetime]


@dataclass
class GitHubLogin:
    code: deviceflow.DeviceCode | None = None
    state: str = "idle"  # idle | waiting | connecting | done | error
    message: str = ""
    connected: Connected | None = None


@dataclass
class ClaudeJob:
    state: str = "idle"  # idle | installing | logging_in | error
    message: str = ""


@dataclass
class SetupFlow:
    wiki_root: Path
    settings_path: Path
    clock: Clock = field(default=lambda: datetime.now(UTC))
    http: Callable[[], httpx.Client] = field(default=lambda: httpx.Client(timeout=30))
    run: claudesetup.Run = claudesetup.run_command
    spawn: Callable[[list[str]], object] = field(default=lambda args: _spawn(args))
    background: Callable[[Callable[[], None]], None] = field(default=lambda work: _thread(work))
    sleep: Callable[[float], None] = time.sleep
    open_url: Callable[[str], object] = webbrowser.open
    copy: Callable[[str], None] = field(default=lambda text: _pbcopy(text))
    claude_config: Path | None = None  # 테스트에서 바꾼다. 없으면 Claude Code 설정 파일
    codex_home: Path | None = None  # 테스트에서 바꾼다. 없으면 $CODEX_HOME 또는 ~/.codex
    on_ready: Callable[[], None] = lambda: None  # 위키 · 로그인이 갖춰졌다 (스케줄러를 깨운다)
    github: GitHubLogin = field(default_factory=GitHubLogin)
    claude_job: ClaudeJob = field(default_factory=ClaudeJob)
    codex_job: ClaudeJob = field(default_factory=ClaudeJob)
    _status: tuple[float, claudesetup.ClaudeStatus] | None = None
    _login: object = None  # 띄운 `claude auth login` 프로세스
    _codex_status: tuple[float, claudesetup.ClaudeStatus] | None = None
    _codex_login: object = None  # 띄운 `codex login` 프로세스

    # ---------- 상태 ----------

    def has_wiki(self) -> bool:
        return (self.wiki_root / ".git").exists()

    def token_source(self) -> str | None:
        return auth.token_source()

    def ready(self) -> bool:
        """수집해도 되나: 위키가 있고 GitHub 토큰을 얻을 수 있다."""
        return self.has_wiki() and self.token_source() is not None

    def claude(self, fresh: bool = False) -> claudesetup.ClaudeStatus:
        now = time.monotonic()
        if fresh or self._status is None or now - self._status[0] > STATUS_TTL:
            self._status = (now, claudesetup.status(self.run))
        return self._status[1]

    # ---------- 고른 에이전트 ----------

    def agent(self) -> str:
        return appsettings.load(self.settings_path).agent

    def choose_agent(self, agent: str) -> None:
        appsettings.update(self.settings_path, agent=agent)

    def agent_status(self, fresh: bool = False) -> claudesetup.ClaudeStatus:
        return self.codex(fresh) if self.agent() == "codex" else self.claude(fresh)

    def agent_cached(self) -> bool:
        """명령을 부르지 않고 마지막으로 확인한 로그인 상태만 본다 (사이드바처럼 자주 그리는 곳)."""
        status = self._codex_status if self.agent() == "codex" else self._status
        return status is not None and status[1].logged_in

    def agent_mcp_ok(self) -> bool:
        return self.codex_mcp_ok() if self.agent() == "codex" else self.mcp_ok()

    def needed(self) -> bool:
        """첫 실행 설정을 보여 줄까. 이미 다 갖춘 기기(다른 방법으로 연결)면 끝난 것으로 남긴다."""
        settings = appsettings.load(self.settings_path)
        if settings.setup_done:
            return False
        if self.ready() and self.agent_status().logged_in and self.agent_mcp_ok():
            appsettings.update(self.settings_path, setup_done=True)
            return False
        return True

    def mcp_ok(self) -> bool:
        return self.registered() == claudesetup.engine_command()

    def registered(self) -> list[str] | None:
        return claudesetup.registered_command(self.claude_config)

    def busy(self) -> bool:
        """화면이 계속 다시 물어야 하나."""
        return (
            self.github.state in ("waiting", "connecting")
            or self.claude_job.state in ("installing", "logging_in")
            or self.codex_job.state in ("installing", "logging_in")
        )

    # ---------- GitHub ----------

    def start_github(self) -> None:
        """로그인돼 있으면 위키만 받고, 아니면 Device Flow를 시작한다."""
        if self.github.state in ("waiting", "connecting"):
            return
        if self.token_source():
            self._connect()
            return
        self._start_device_flow()

    def _start_device_flow(self) -> None:
        try:
            with self.http() as client:
                code = deviceflow.start(client, self.clock())
        except deviceflow.DeviceFlowError as e:
            self.github = GitHubLogin(state="error", message=str(e))
            return
        self.github = GitHubLogin(code=code, state="waiting")
        self.background(lambda: self._wait(code))

    def open_github(self) -> None:
        """코드를 복사하고 승인 페이지를 연다."""
        code = self.github.code
        if code is None:
            return
        self.copy(code.user_code)
        self.open_url(code.verification_uri)

    def relogin_github(self) -> None:
        """기존 토큰은 새 토큰을 받을 때 덮어쓴다. 그동안 돌던 수집도 push할 수 있다.
        GitHub CLI 로그인이 있어도 그 계정으로 넘어가지 않고 브라우저 승인을 받는다."""
        if self.github.state in ("waiting", "connecting"):
            return
        self._start_device_flow()

    def _wait(self, code: deviceflow.DeviceCode) -> None:
        interval = code.interval
        with self.http() as client:
            while self.github.code is code:
                self.sleep(interval)
                try:
                    result = deviceflow.poll(client, code, self.clock())
                except deviceflow.DeviceFlowError as e:
                    self.github = GitHubLogin(state="error", message=str(e))
                    return
                if result.state == "slow_down":
                    interval = result.interval or interval
                elif result.state == "done" and result.token:
                    auth.save_token(result.token)
                    self._connect()
                    return
                elif result.state in ("expired", "denied"):
                    message = (
                        "코드가 만료됐어요." if result.state == "expired" else "승인을 취소했어요."
                    )
                    self.github = GitHubLogin(state="error", message=f"{message} 다시 시도하세요.")
                    return

    def _connect(self) -> None:
        self.github = GitHubLogin(state="connecting")

        def work() -> None:
            try:
                connected = connect_wiki(self.wiki_root, auth.github_token)
            except (ConnectError, auth.TokenMissing) as e:
                self.github = GitHubLogin(state="error", message=str(e))
                return
            self.github = GitHubLogin(state="done", connected=connected)
            self.on_ready()

        self.background(work)

    # ---------- Claude ----------

    def install_claude(self) -> None:
        if self.claude_job.state != "idle" and self.claude_job.state != "error":
            return
        self.claude_job = ClaudeJob("installing")

        def work() -> None:
            code, out = self.run(claudesetup.install_command(), INSTALL_TIMEOUT)
            tool.cache_clear()  # 새로 설치한 claude를 찾게
            self._status = None
            if code != 0:
                self.claude_job = ClaudeJob("error", f"설치하지 못했어요 ({code}). {out[-200:]}")
            else:
                self.claude_job = ClaudeJob()

        self.background(work)

    def login_claude(self) -> None:
        """브라우저 승인을 띄운다. 끝났는지는 화면이 상태를 다시 읽어 안다."""
        try:
            self._login = self.spawn(claudesetup.login_command(tool("claude")))
        except OSError as e:
            self.claude_job = ClaudeJob("error", f"Claude 로그인을 띄우지 못했어요: {e}")
            return
        self._status = None
        self.claude_job = ClaudeJob("logging_in")

    def claude_settled(self) -> None:
        """로그인을 기다리다 끝났으면 작업 표시를 지운다.

        이미 로그인된 채로 다시 연결하면 로그인 상태만으로는 끝났는지 모른다.
        띄운 프로세스가 끝났는지를 먼저 본다 (승인하거나 취소하면 끝난다).
        """
        if self.claude_job.state != "logging_in":
            return
        poll = getattr(self._login, "poll", None)
        done = poll() is not None if poll else self.claude(fresh=True).logged_in
        if done:
            self._login = None
            self._status = None
            self.claude_job = ClaudeJob()

    def register_mcp(self) -> tuple[bool, str]:
        return claudesetup.register_mcp(self.run)

    # ---------- Codex ----------

    def codex(self, fresh: bool = False) -> claudesetup.ClaudeStatus:
        now = time.monotonic()
        if fresh or self._codex_status is None or now - self._codex_status[0] > STATUS_TTL:
            status = codexsetup.status(self.run, home=self.codex_home)
            self._codex_status = (now, status)
        return self._codex_status[1]

    def install_codex(self) -> None:
        if self.codex_job.state not in ("idle", "error"):
            return
        self.codex_job = ClaudeJob("installing")

        def work() -> None:
            code, out = self.run(codexsetup.install_command(), INSTALL_TIMEOUT)
            tool.cache_clear()  # 새로 설치한 codex를 찾게
            self._codex_status = None
            if code != 0:
                self.codex_job = ClaudeJob("error", f"설치하지 못했어요 ({code}). {out[-200:]}")
            else:
                self.codex_job = ClaudeJob()

        self.background(work)

    def login_codex(self) -> None:
        """브라우저 승인을 띄운다. 끝났는지는 화면이 상태를 다시 읽어 안다."""
        try:
            self._codex_login = self.spawn(codexsetup.login_command(tool("codex")))
        except OSError as e:
            self.codex_job = ClaudeJob("error", f"Codex 로그인을 띄우지 못했어요: {e}")
            return
        self._codex_status = None
        self.codex_job = ClaudeJob("logging_in")

    def codex_settled(self) -> None:
        """띄운 `codex login`이 끝났으면 (승인하거나 취소하면) 작업 표시를 지운다."""
        if self.codex_job.state != "logging_in":
            return
        poll = getattr(self._codex_login, "poll", None)
        done = poll() is not None if poll else self.codex(fresh=True).logged_in
        if done:
            self._codex_login = None
            self._codex_status = None
            self.codex_job = ClaudeJob()

    def codex_registered(self) -> list[str] | None:
        return codexsetup.registered_command(self.codex_home)

    def codex_mcp_ok(self) -> bool:
        return self.codex_registered() == codexsetup.engine_command()

    def register_codex_mcp(self) -> tuple[bool, str]:
        return codexsetup.register_mcp(self.run)

    # ---------- 마치기 ----------

    def finish(self, autostart: bool, daily_at: str, notify: bool) -> appsettings.Settings:
        settings = appsettings.update(
            self.settings_path,
            autostart=autostart,
            daily_at=daily_at,
            notify=notify,
            setup_done=True,
        )
        self.on_ready()
        return settings


def model_downloaded(models_dir: Path, model_id: str) -> bool:
    key = model_id.lower().split("/")[-1]
    return any(key in p.name.lower() for p in models_dir.glob("models--*"))


def _spawn(args: list[str]) -> object:  # pragma: no cover - 실제 프로세스
    return subprocess.Popen(
        args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def _thread(work: Callable[[], None]) -> None:  # pragma: no cover - 실제 스레드
    threading.Thread(target=work, daemon=True).start()


def _pbcopy(text: str) -> None:  # pragma: no cover - 실제 클립보드
    subprocess.run(["pbcopy"], input=text, text=True, check=False)
