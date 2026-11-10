"""시작하기 탭의 진행률 (plan 3.2). 필수 단계만 센다.

사이드바 맨 위에 "시작하기 n/5"로 보이고, 다 끝나면 기기 설정에 남겨 다시 보이지 않는다.
상태는 실제 연결 · 위키에서 읽는다. 사용자가 따로 체크하지 않는다.
"""

from dataclasses import dataclass
from pathlib import Path

from context_holder import appsettings
from context_holder.scheduler import needs_init
from context_holder.topics import TOPICS_FILE
from context_holder.wiki import Wiki


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    done: bool


@dataclass(frozen=True)
class Onboarding:
    steps: tuple[Step, ...]

    @property
    def done(self) -> int:
        return sum(1 for s in self.steps if s.done)

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def complete(self) -> bool:
        return self.done == self.total

    @property
    def percent(self) -> int:
        return round(100 * self.done / self.total)

    def is_done(self, key: str) -> bool:
        return any(s.done for s in self.steps if s.key == key)


def build(flow, wiki: Wiki, settings: appsettings.Settings, check: bool = True) -> Onboarding:
    """flow: setupflow.SetupFlow. 설정을 마쳤으면 GitHub · Claude는 그때 갖춘 것으로 본다.

    check가 거짓이면 `claude auth status`를 부르지 않고 마지막으로 확인한 상태만 쓴다
    (사이드바는 화면마다 그린다).
    """
    started = settings.setup_done
    github = started or flow.ready()
    claude = started or (flow.claude().logged_in if check else flow.claude_cached())
    collected = github and not needs_init(wiki)
    return Onboarding(
        (
            Step("github", "GitHub 로그인과 위키 레포", github),
            Step("claude", "Claude 연결", claude),
            Step("start", "수집 시작", started),
            Step("first_sync", "첫 수집", collected),
            Step("topics", "주제 정리", (wiki.root / TOPICS_FILE).exists()),
        )
    )


def pending(flow, wiki: Wiki, settings_path: Path) -> Onboarding | None:
    """아직 남았으면 진행 상태, 다 끝났으면 None. 처음 다 끝난 때를 기기 설정에 남긴다."""
    settings = appsettings.load(settings_path)
    if settings.onboarding_done:
        return None
    progress = build(flow, wiki, settings, check=False)
    if not progress.complete:
        return progress
    appsettings.update(settings_path, onboarding_done=True)
    return None
