"""첫 실행 설정과 연결 상태 (plan 3.2). 화면(`/setup`, `/settings`)이 이것을 부른다.

오래 걸리는 일(GitHub 승인 기다리기, 위키 받기, Claude 설치)은 뒤에서 돌리고, 화면은 상태를 다시
읽어 그린다. 상태는 이 프로세스 메모리에만 있다. 토큰은 키체인에만 둔다 (auth).
"""

import logging
import os
import signal
import subprocess
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from context_holder import agentaccounts, appsettings, auth, claudesetup, codexsetup, deviceflow
from context_holder.connect import Connected, ConnectError, connect_wiki
from context_holder.llm import AGENTS
from context_holder.tools import tool

log = logging.getLogger(__name__)

STATUS_TTL = 5.0  # claude auth status는 1초쯤 걸린다. 화면이 2초마다 물어도 이만큼은 재사용한다
INSTALL_TIMEOUT = 300
# 브라우저를 그냥 닫으면 로그인 프로세스가 승인 응답을 끝없이 기다린다. 이만큼 지나면 멈춘다
LOGIN_TIMEOUT = timedelta(minutes=5)

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


@dataclass(frozen=True)
class Login:
    account: str  # system 또는 계정 id
    new: bool  # 계정 추가로 시작한 로그인
    process: object  # 띄운 `claude auth login` · `codex login` 프로세스
    started: datetime


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
    kill: Callable[[int], None] = field(default=lambda pid: os.kill(pid, signal.SIGTERM))
    open_url: Callable[[str], object] = webbrowser.open
    copy: Callable[[str], None] = field(default=lambda text: _pbcopy(text))
    claude_config: Path | None = None  # 테스트에서 바꾼다. 없으면 Claude Code 설정 파일
    codex_home: Path | None = None  # 테스트에서 바꾼다. 없으면 $CODEX_HOME 또는 ~/.codex
    accounts_root: Path | None = None  # 테스트에서 바꾼다. 없으면 ~/.context-holder/accounts
    on_ready: Callable[[], None] = lambda: None  # 위키 · 로그인이 갖춰졌다 (스케줄러를 깨운다)
    github: GitHubLogin = field(default_factory=GitHubLogin)
    claude_job: ClaudeJob = field(default_factory=ClaudeJob)
    codex_job: ClaudeJob = field(default_factory=ClaudeJob)
    _status: tuple[float, claudesetup.ClaudeStatus] | None = None  # 활성 계정 (Claude)
    _codex_status: tuple[float, claudesetup.ClaudeStatus] | None = None  # 활성 계정 (Codex)
    _system: dict[str, tuple[float, claudesetup.ClaudeStatus]] = field(default_factory=dict)
    _logins: dict[str, "Login"] = field(default_factory=dict)  # 승인을 기다리는 로그인

    # ---------- 상태 ----------

    def has_wiki(self) -> bool:
        return (self.wiki_root / ".git").exists()

    def token_source(self) -> str | None:
        return auth.token_source()

    def ready(self) -> bool:
        """수집해도 되나: 위키가 있고 GitHub 토큰을 얻을 수 있다."""
        return self.has_wiki() and self.token_source() is not None

    def claude(self, fresh: bool = False) -> claudesetup.ClaudeStatus:
        """활성 계정의 상태."""
        now = time.monotonic()
        if fresh or self._status is None or now - self._status[0] > STATUS_TTL:
            self._status = (now, self._read_status("claude", self.account("claude")))
        return self._status[1]

    # ---------- 고른 에이전트 ----------

    def agent(self) -> str:
        return appsettings.load(self.settings_path).agent

    def choose_agent(self, agent: str) -> None:
        appsettings.update(self.settings_path, agent=agent)

    # ---------- 계정 ----------

    def account(self, agent: str) -> str:
        """활성 계정: system 또는 추가한 계정 id. 지워진 계정이면 system."""
        account = appsettings.load(self.settings_path).account(agent)
        if account != agentaccounts.SYSTEM and not self._exists(agent, account):
            return agentaccounts.SYSTEM
        return account

    def prefix(self, agent: str, account: str | None = None) -> list[str]:
        """그 계정의 설정 폴더로 에이전트 명령을 돌리게 앞에 붙일 것. 없으면 활성 계정."""
        chosen = account or self.account(agent)
        return agentaccounts.command_prefix(agent, chosen, self.accounts_root)

    def accounts(self, agent: str) -> list[agentaccounts.Account]:
        """추가한 계정들. 승인 전에 끝난 계정(로그인한 적 없음)은 지운다 (앱을 껐다 켠 경우 등)."""
        found = []
        for account in agentaccounts.list_accounts(agent, self.accounts_root):
            if account.email or account.plan or account.id == self.logging_in(agent):
                found.append(account)
            else:
                agentaccounts.remove(agent, account.id, self.accounts_root)
        return found

    def system_status(self, agent: str) -> claudesetup.ClaudeStatus:
        """터미널 로그인(시스템 기본값)의 상태. 활성이면 활성 상태를 그대로 쓴다."""
        if self.account(agent) == agentaccounts.SYSTEM:
            return self._active(agent)
        now = time.monotonic()
        cached = self._system.get(agent)
        if cached is None or now - cached[0] > STATUS_TTL:
            cached = (now, self._read_status(agent, agentaccounts.SYSTEM))
            self._system[agent] = cached
        return cached[1]

    def choose_account(self, agent: str, account: str) -> None:
        if account != agentaccounts.SYSTEM and not self._exists(agent, account):
            raise ValueError(f"없는 계정: {account}")
        appsettings.update(self.settings_path, **{f"{agent}_account": account})
        self._forget(agent)
        self._set_job(agent, ClaudeJob())

    def add_account(self, agent: str) -> None:
        """새 설정 폴더를 만들고 거기로 로그인한다. 승인하면 활성 계정이 된다."""
        if self._busy(agent):
            return
        account = agentaccounts.create(agent, self.clock(), self.accounts_root)
        self._start_login(agent, account, new=True)

    def relogin_account(self, agent: str, account: str) -> None:
        if account != agentaccounts.SYSTEM and not self._exists(agent, account):
            return
        if not self._busy(agent):
            self._start_login(agent, account, new=False)

    def remove_account(self, agent: str, account: str) -> None:
        """로그아웃해 키체인 · 토큰을 지우고 폴더를 지운다. 활성이면 시스템 기본값으로."""
        if account == agentaccounts.SYSTEM or not self._exists(agent, account):
            return
        logout = claudesetup.logout_command if agent == "claude" else codexsetup.logout_command
        self.run(logout(tool(agent), self.prefix(agent, account)), claudesetup.TIMEOUT)
        if self.account(agent) == account:
            appsettings.update(self.settings_path, **{f"{agent}_account": agentaccounts.SYSTEM})
        agentaccounts.remove(agent, account, self.accounts_root)
        self._forget(agent)

    def logging_in(self, agent: str) -> str | None:
        """승인을 기다리는 계정 (새로 추가하는 계정이면 그 id)."""
        login = self._logins.get(agent)
        return login.account if login else None

    def _exists(self, agent: str, account: str) -> bool:
        return (
            agentaccounts.valid(account)
            and agentaccounts.account_dir(agent, account, self.accounts_root).is_dir()
        )

    def _read_status(self, agent: str, account: str) -> claudesetup.ClaudeStatus:
        prefix = self.prefix(agent, account)
        if agent == "claude":
            status = claudesetup.status(self.run, prefix=prefix)
        else:
            folder = agentaccounts.account_dir(agent, account, self.accounts_root)
            status = codexsetup.status(
                self.run, home=folder if prefix else self.codex_home, prefix=prefix
            )
        if prefix and status.logged_in:
            agentaccounts.remember(
                agent, account, status.account, status.detail, status.org, self.accounts_root
            )
        return status

    def _active(self, agent: str) -> claudesetup.ClaudeStatus:
        return self.codex() if agent == "codex" else self.claude()

    def _forget(self, agent: str) -> None:
        self._system.pop(agent, None)
        if agent == "codex":
            self._codex_status = None
        else:
            self._status = None

    def job(self, agent: str) -> ClaudeJob:
        return self.codex_job if agent == "codex" else self.claude_job

    def _set_job(self, agent: str, job: ClaudeJob) -> None:
        if agent == "codex":
            self.codex_job = job
        else:
            self.claude_job = job

    def _busy(self, agent: str) -> bool:
        return self.job(agent).state in ("installing", "logging_in")

    def _start_login(self, agent: str, account: str, new: bool) -> None:
        """브라우저 승인을 띄운다. 끝났는지는 화면이 상태를 다시 읽어 안다 (settle)."""
        login = claudesetup.login_command if agent == "claude" else codexsetup.login_command
        if agent == "codex":
            reason = codexsetup.free_login_port(self.run, self.kill, self.sleep)
            if reason:
                if new:
                    agentaccounts.remove(agent, account, self.accounts_root)
                self._set_job(agent, ClaudeJob("error", reason))
                return
        try:
            process = self.spawn(login(tool(agent), self.prefix(agent, account)))
        except OSError as e:
            if new:
                agentaccounts.remove(agent, account, self.accounts_root)
            name = AGENTS[agent]
            self._set_job(agent, ClaudeJob("error", f"{name} 로그인을 띄우지 못했어요: {e}"))
            return
        self._logins[agent] = Login(account, new, process, self.clock())
        self._forget(agent)
        self._set_job(agent, ClaudeJob("logging_in"))

    def cancel_login(self, agent: str) -> None:
        """승인을 기다리던 로그인을 멈춘다. 새로 추가하던 계정이면 지운다."""
        if agent in self._logins:
            self._stop_login(agent)

    def _stop_login(self, agent: str, message: str = "") -> None:
        login = self._logins.pop(agent)
        terminate = getattr(login.process, "terminate", None)
        if terminate:
            terminate()
        if login.new:
            agentaccounts.remove(agent, login.account, self.accounts_root)
        self._forget(agent)
        self._set_job(agent, ClaudeJob("error", message) if message else ClaudeJob())

    def settle(self, agent: str) -> None:
        """로그인을 기다리다 끝났으면 정리한다.

        이미 로그인된 채로 다시 연결하면 로그인 상태만으로는 끝났는지 모른다.
        띄운 프로세스가 끝났는지를 먼저 본다 (승인하거나 취소하면 끝난다).
        새로 추가한 계정은 승인했으면 활성으로 바꾸고, 취소했으면 폴더를 지운다.
        """
        login = self._logins.get(agent)
        if login is None or self.job(agent).state != "logging_in":
            return
        poll = getattr(login.process, "poll", None)
        status = None
        if poll is None:
            status = self._read_status(agent, login.account)
            done = status.logged_in
        else:
            done = poll() is not None
        if not done:
            if self.clock() - login.started > LOGIN_TIMEOUT:
                self._stop_login(agent, "로그인 시간이 지났어요. 다시 시도해 주세요.")
            return
        del self._logins[agent]
        if login.new:
            status = status or self._read_status(agent, login.account)
            if status.logged_in:
                appsettings.update(self.settings_path, **{f"{agent}_account": login.account})
            else:
                agentaccounts.remove(agent, login.account, self.accounts_root)
        self._forget(agent)
        self._set_job(agent, ClaudeJob())

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
        ready = self.ready() and self.agent_status().logged_in
        if ready and not self.agent_status().outdated and self.agent_mcp_ok():
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
        """활성 계정으로 로그인한다."""
        self._start_login("claude", self.account("claude"), new=False)

    def claude_settled(self) -> None:
        self.settle("claude")

    def register_mcp(self) -> tuple[bool, str]:
        return claudesetup.register_mcp(self.run)

    # ---------- Codex ----------

    def codex(self, fresh: bool = False) -> claudesetup.ClaudeStatus:
        """활성 계정의 상태."""
        now = time.monotonic()
        if fresh or self._codex_status is None or now - self._codex_status[0] > STATUS_TTL:
            self._codex_status = (now, self._read_status("codex", self.account("codex")))
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
        """활성 계정으로 로그인한다."""
        self._start_login("codex", self.account("codex"), new=False)

    def codex_settled(self) -> None:
        self.settle("codex")

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
