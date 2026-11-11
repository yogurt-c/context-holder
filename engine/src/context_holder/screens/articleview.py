"""글 상세 모달 (Search 결과 · Today 목록에서 연다).

요약, 핵심 내용, 결정 기록 전체(근거 문장 포함), 태그, 비슷한 결정을 보여 준다.
"""

from dataclasses import dataclass
from pathlib import Path

from context_holder.feedback import bookmarked, load_feedback
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
    bookmarked: bool = False  # 상단 북마크 아이콘을 채워 보인다


def build_article(
    tools: WikiTools, wiki: Wiki, index: Index, article_id: str, names: dict[str, str]
) -> ArticleView | None:
    article = index.get(article_id)
    stored = wiki.read(Path(article.path)) if article else None
    if stored is None:  # 없는 id이거나 다른 기기의 pull로 사라졌다
        return None
    company = names.get(stored.source_id, stored.source_id)
    marks = {"bookmarked": stored.id in bookmarked(load_feedback(wiki))}
    if not stored.decision:
        return ArticleView(stored, company, "", [], **marks)
    hits, _ = tools.decision_hits(topic=stored.decision.get("topic"), limit=RELATED + 1)
    related = [
        Related(names.get(h.article.source_id, h.article.source_id), h.article.title, h.article.id)
        for h in hits
        if h.article.id != stored.id
    ][:RELATED]
    return ArticleView(stored, company, decision_markdown(stored, company), related, **marks)


def remove_article(wiki: Wiki, index: Index, article_id: str) -> bool:
    """글 상세 "위키에서 제거": 글 파일 · 벡터 · 인덱스에서 지운다. 피드백은 남기지 않는다.

    본 글 기록은 남으니 다시 수집하지 않는다. 지운 파일은 다음 동기화 때 다른 기기로 간다.
    """
    article = index.get(article_id)
    if article is None:
        return False
    index.remove(article.id)  # 인덱스가 잠겨 실패하면 파일은 그대로 둔다
    wiki.remove_article(Path(article.path), article.id)
    return True
