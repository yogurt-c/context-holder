"""글 상세 모달 (Search 결과 · Today 목록에서 연다).

요약, 핵심 내용, 결정 기록 전체(근거 문장 포함), 태그, 비슷한 결정을 보여 준다.
"""

from dataclasses import dataclass
from pathlib import Path

from context_holder.index import Index
from context_holder.mcptools import WikiTools
from context_holder.screens.views import decision_markdown
from context_holder.wiki import StoredArticle, Wiki

RELATED = 3


@dataclass(frozen=True)
class Related:
    company: str
    title: str
    id: str


@dataclass(frozen=True)
class ArticleView:
    stored: StoredArticle
    company: str
    decision_markdown: str  # "결정 기록 복사"로 복사할 내용
    related: list[Related]


def build_article(
    tools: WikiTools, wiki: Wiki, index: Index, article_id: str, names: dict[str, str]
) -> ArticleView | None:
    article = index.get(article_id)
    stored = wiki.read(Path(article.path)) if article else None
    if stored is None:  # 없는 id이거나 다른 기기의 pull로 사라졌다
        return None
    company = names.get(stored.source_id, stored.source_id)
    if not stored.decision:
        return ArticleView(stored, company, "", [])
    hits, _ = tools.decision_hits(topic=stored.decision.get("topic"), limit=RELATED + 1)
    related = [
        Related(names.get(h.article.source_id, h.article.source_id), h.article.title, h.article.id)
        for h in hits
        if h.article.id != stored.id
    ][:RELATED]
    return ArticleView(stored, company, decision_markdown(stored, company), related)
