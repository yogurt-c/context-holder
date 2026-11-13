"""Sources 화면의 "소스 추가": 블로그 주소로 feed를 찾고, 모을 수 있으면 수집 대상에 넣는다.

확인(probe)은 요청이 여러 번이라 뒤에서 돌리고, 창은 상태만 조회한다.
넣는 것은 sources.yaml 한 곳을 고치는 일이라 바로 끝낸다. 다음 수집이 커밋 · push하고 모은다.
수집 대상에서 뺀 소스의 feed를 다시 찾으면 새로 넣지 않고 제외만 푼다.
"""

import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from context_holder import discover, sourcesedit
from context_holder.discover import NotCollectable, Probe
from context_holder.screens.addarticle import checked_url
from context_holder.sources import REGIONS, Source, SourceConfigError

if TYPE_CHECKING:
    from context_holder.screens.app import ScreenContext

log = logging.getLogger(__name__)

BUSY = "수집이 끝난 뒤 다시 시도해 주세요."
BAD_URL = "블로그나 피드의 http(s) 주소를 입력해 주세요."


@dataclass
class AddSourceJob:
    ctx: "ScreenContext"
    write_lock: AbstractContextManager
    background: Callable = field(
        default=lambda work: threading.Thread(target=work, daemon=True).start()
    )
    state: str = "idle"  # idle | checking | ready | error
    url: str = ""
    message: str = ""  # 확인 중인 단계나 실패 이유
    form_error: str = ""  # ready에서 넣지 못한 이유 (이름 · 수집 중)
    probe: Probe | None = None
    restore: Source | None = None  # 수집 대상에서 뺐던 같은 feed의 소스
    _lock: AbstractContextManager = field(default_factory=threading.Lock)

    def check(self, value: str) -> None:
        with self._lock:
            if self.state == "checking":
                return
            self.url, self.probe, self.restore, self.form_error = value.strip(), None, None, ""
            try:
                url = checked_url(value, BAD_URL)
            except ValueError as e:
                self.state, self.message = "error", str(e)
                return
            self.url = url
            self.state, self.message = "checking", "피드를 찾는 중…"
        self.background(self._run)

    def add(self, name: str, region: str, busy: bool) -> Source | None:
        """수집 대상에 넣는다. 넣었으면 그 소스, 못 넣었으면 None (이유는 form_error)."""
        with self._lock:
            if self.state != "ready" or self.probe is None:
                return None
            self.form_error = ""
            if busy:
                self.form_error = BUSY
                return None
            try:
                with self.write_lock:
                    source = self._save(name, region)
            except ValueError as e:  # 이름 · 지역 (SourceConfigError도 ValueError다)
                self.form_error = str(e)
                return None
            except OSError:
                log.exception("소스 추가 실패")
                self.form_error = "설정 파일에 쓰지 못했어요. 다시 시도해 주세요."
                return None
            self._reset()
            return source

    def dismiss(self) -> None:
        """끝난 결과를 비운다. 확인 중이면 그대로 둔다 (창을 다시 열면 이어서 보인다)."""
        with self._lock:
            if self.state != "checking":
                self._reset()

    def _reset(self) -> None:
        self.state, self.url, self.message, self.form_error = "idle", "", "", ""
        self.probe, self.restore = None, None

    def _save(self, name: str, region: str) -> Source:
        assert self.probe is not None
        current = sourcesedit.find_by_feed(self.ctx.wiki.sources(), self.probe.feed_url)
        if current is not None and current.excluded:
            self.ctx.wiki.include_source(current.id)
            return current
        if region not in REGIONS:
            raise ValueError("국내 · 해외 중 하나를 골라 주세요.")
        return self.ctx.wiki.add_source(sourcesedit.clean_name(name), region, self.probe.feed_url)

    def _step(self, message: str) -> None:
        self.message = message

    def _run(self) -> None:
        try:
            with httpx.Client() as client:
                found = discover.probe(client, self.url, self.ctx.clock(), self._step)
            self.ctx.wiki.ensure_config()
            existing = sourcesedit.find_by_feed(self.ctx.wiki.sources(), found.feed_url)
            if existing is not None and not existing.excluded:
                self.state = "error"
                self.message = f"이미 수집하고 있는 소스예요: {existing.name}"
                return
            self.probe, self.restore = found, existing
            self.state, self.message = "ready", ""
        except NotCollectable as e:
            self.state, self.message = "error", str(e)
        except (SourceConfigError, OSError):
            log.exception("sources.yaml을 읽지 못했다")
            self.state, self.message = "error", "수집 설정을 읽지 못했어요."
        except Exception:
            log.exception("소스 확인 실패")
            self.state, self.message = "error", "확인하지 못했어요. 다시 시도해 주세요."
