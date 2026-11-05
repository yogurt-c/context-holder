"""수집(sync)과 첫 실행(init)이 함께 쓰는 Claude 호출 규칙."""

import logging

from context_holder.classify import ClassifyResult, StructuredLlm
from context_holder.collect import Article
from context_holder.enrich import Enrichment, enrich
from context_holder.llm import ErrorKind, LlmError

log = logging.getLogger(__name__)

# 이 오류가 나면 이번 실행에서는 Claude를 더 부르지 않는다
STOP_KINDS = frozenset({ErrorKind.AUTH, ErrorKind.LIMIT, ErrorKind.NOT_INSTALLED})


class Budget:
    """이번 실행의 사용량과 멈춤 여부."""

    def __init__(self) -> None:
        self.cost = 0.0
        self.stopped: str | None = None

    def note_classify(self, result: ClassifyResult) -> None:
        self.cost += result.cost_usd
        stop = next(
            (p.reason for p in result.pending if p.reason.split(":")[0] in STOP_KINDS), None
        )
        self.stopped = self.stopped or stop

    def try_enrich(self, llm: StructuredLlm, article: Article) -> tuple[Enrichment | None, str]:
        """요약을 만든다. 실패하면 (None, 이유)."""
        if self.stopped:
            return None, self.stopped
        try:
            enrichment = enrich(llm, article)
        except LlmError as e:
            log.warning("요약 실패 %s: %s", article.url, e)
            if e.kind in STOP_KINDS:
                self.stopped = str(e)
            return None, str(e)
        self.cost += enrichment.cost_usd
        return enrichment, ""
