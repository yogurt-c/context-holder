"""Search · Inbox · Sources 화면과 상태 알림."""

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from context_holder.classify import Bucket, Decision
from context_holder.collect import Article
from context_holder.enrich import Enrichment
from context_holder.feedback import Kind, load_feedback, record_feedback
from context_holder.index import Index
from context_holder.mcptools import NO_MODEL_NOTE
from context_holder.screens import bookmarksview, inboxview, searchview, sourcesview
from context_holder.screens.app import COOKIE, create_app
from context_holder.screens.runs import alerts, run_records
from context_holder.wiki import Wiki, article_id
from tests.conftest import FakeEmbedder
from tests.test_screens import NOW, SOURCES, TODAY, TOKEN, add, context, run_file

HTMX = {"HX-Request": "true"}
CLOCK = NOW.astimezone().strftime("%H:%M")  # 기록 시각은 기기 현지 시각으로 보인다


@pytest.fixture
def wiki(tmp_path) -> Wiki:
    wiki = Wiki(tmp_path / "wiki")
    wiki.ensure_config()
    (wiki.root / "config/sources.yaml").write_text(SOURCES, encoding="utf-8")
    return wiki


def held(wiki: Wiki, url: str, title: str, score: float = 0.5, origin: str | None = None) -> str:
    """보류 글. 판정 이유(plus · minus)를 함께 쓴다."""
    article = Article("d2", url, title, NOW, "본문", "h")
    decision = Decision(url, score, Bucket.INBOX, "AI 엔지니어링", ("캐시 설계",), ("ML 비중",))
    enrichment = Enrichment((f"{title} 요약",), ("핵심",), ("llm",), None, None, 0.0)
    wiki.write_article(article, decision, enrichment, NOW, origin=origin)
    return article_id(url)


def make_client(wiki: Wiki, tmp_path: Path, embedder=None) -> tuple[TestClient, Index]:
    ctx = context(wiki, tmp_path)
    if embedder:
        ctx.index.sync(wiki, embedder)
        ctx = replace(ctx, embedder=lambda: embedder)
    c = TestClient(create_app(ctx), follow_redirects=False)
    c.cookies.set(COOKIE, TOKEN)
    return c, ctx.index


# ---------- 위키 · 인덱스: Inbox에서 고른 글 ----------


def test_save_from_inbox_moves_the_file_and_keeps_vectors(wiki, tmp_path):
    aid = held(wiki, "https://t/held", "보류 글")
    embedder = FakeEmbedder()
    index = Index(tmp_path / "index.db")
    index.sync(wiki, embedder)
    chunks = index.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    old = Path(index.get(aid).path)

    moved = wiki.save_from_inbox(old)
    index.move(aid, wiki.read(moved))

    assert not old.exists() and moved.parts[-4] == "wiki"
    stored = wiki.read(moved)
    assert stored.bucket is Bucket.SAVE and stored.summary == ("보류 글 요약",)
    assert index.get(aid).bucket is Bucket.SAVE and index.get(aid).path == str(moved)
    assert index.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == chunks
    assert index.sync(wiki, embedder) == (0, 0)  # 다시 색인하지 않는다


def test_save_from_inbox_avoids_name_clash(wiki):
    held(wiki, "https://t/x/post", "보류")
    add(wiki, "https://t/y/post", "저장된 같은 이름")
    path = next((wiki.root / "inbox").rglob("*.md"))
    moved = wiki.save_from_inbox(path)
    assert moved.name.startswith("post-") and len(list((wiki.root / "wiki").rglob("*.md"))) == 2


def test_remove_article_deletes_file_vectors_and_index_rows(wiki, tmp_path):
    aid = held(wiki, "https://t/held", "보류 글")
    (wiki.root / "vectors/m").mkdir(parents=True)
    (wiki.root / f"vectors/m/{aid}.f16").write_bytes(b"x")
    (wiki.root / f"vectors/m/{aid}.json").write_text("{}")
    index = Index(tmp_path / "index.db")
    index.sync(wiki, FakeEmbedder())
    path = Path(index.get(aid).path)

    wiki.remove_article(path, aid)
    index.remove(aid)

    assert not path.exists() and not list((wiki.root / "vectors").rglob(f"{aid}.*"))
    assert index.get(aid) is None
    assert index.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    assert index.keyword("보류") == []


# ---------- Inbox ----------


def test_inbox_lists_recent_then_init_group_and_explains_the_hold(wiki, tmp_path):
    first = held(wiki, "https://t/a", "어제 보류", 0.55)
    held(wiki, "https://t/b", "초기 보류", 0.4, origin="init")
    c, _ = make_client(wiki, tmp_path)

    page = c.get("/inbox").text

    assert page.index("어제 보류") < page.index("초기 수집 보류")
    assert f'href="/inbox?sel={first}"' in page and 'class="sel"' in page
    assert "캐시 설계" in page and "ML 비중" in page and "AI 엔지니어링" in page
    assert "left:55.0%" in page and "0.55" in page
    assert '<details class="init-group">' in page  # 고른 글이 매일 수집 글이면 접어 둔다
    assert "모두 관련없음…" in page


def test_inbox_selection_opens_init_group_and_shows_pending_note(wiki, tmp_path):
    aid = held(wiki, "https://t/b", "초기 보류", origin="init")
    path = next((wiki.root / "inbox").rglob("*.md"))
    path.write_text(path.read_text().replace("## 요약", "## 다른"))  # 요약이 없는 글
    c, _ = make_client(wiki, tmp_path)
    page = c.get(f"/inbox?sel={aid}").text
    assert '<details class="init-group" open>' in page
    assert "아직 요약하지 못한 글" in page
    assert "나중에" not in page  # 다음 글이 없다


def test_inbox_without_reasons_and_empty_inbox(wiki, tmp_path):
    add(wiki, "https://t/a", "보류", 0.5, bucket=Bucket.INBOX)
    c, _ = make_client(wiki, tmp_path)
    assert "판정 이유가 남아 있지 않아요" in c.get("/inbox").text
    assert (
        "검토할 글이 없어요"
        in make_client(Wiki(tmp_path / "empty"), tmp_path / "e")[0].get("/inbox").text
    )


def test_inbox_save_moves_article_and_selects_the_next(wiki, tmp_path):
    first = held(wiki, "https://t/a", "첫 보류", 0.55)
    second = held(wiki, "https://t/b", "둘째 보류", 0.45)
    c, index = make_client(wiki, tmp_path)

    done = c.post("/inbox/decide", data={"article_id": first, "choice": "save"}, headers=HTMX)

    assert done.status_code == 200
    assert index.get(first).bucket is Bucket.SAVE
    assert (wiki.root / "wiki").exists() and len(list((wiki.root / "inbox").rglob("*.md"))) == 1
    assert [(f.kind, f.url) for f in load_feedback(wiki)] == [("save", "https://t/a")]
    assert (
        f'href="/inbox?sel={second}"' in done.text and 'class="count" aria-label="1건"' in done.text
    )


def test_inbox_dismiss_deletes_article_but_keeps_it_seen(wiki, tmp_path):
    aid = held(wiki, "https://t/a", "보류")
    wiki.record_seen([Decision("https://t/a", 0.5, Bucket.INBOX, "x", (), ())], NOW)
    c, index = make_client(wiki, tmp_path)

    c.post("/inbox/decide", data={"article_id": aid, "choice": "dismiss"}, headers=HTMX)

    assert index.get(aid) is None and not list((wiki.root / "inbox").rglob("*.md"))
    assert [f.kind for f in load_feedback(wiki)] == ["not_relevant"]
    assert "https://t/a" in wiki.seen_urls()


def test_inbox_decide_rejects_bad_requests(wiki, tmp_path):
    aid = held(wiki, "https://t/a", "보류")
    saved = add(wiki, "https://t/s", "저장 글")
    c, _ = make_client(wiki, tmp_path)
    post = c.post
    assert (
        post("/inbox/decide", data={"article_id": aid, "choice": "x"}, headers=HTMX).status_code
        == 400
    )
    assert (
        post(
            "/inbox/decide", data={"article_id": saved, "choice": "save"}, headers=HTMX
        ).status_code
        == 404
    )
    assert (
        post(
            "/inbox/decide", data={"article_id": "none", "choice": "save"}, headers=HTMX
        ).status_code
        == 404
    )
    assert post("/inbox/decide", data={"article_id": aid, "choice": "save"}).status_code == 403
    next((wiki.root / "inbox").rglob("*.md")).unlink()  # 다른 기기의 pull로 사라졌다
    assert (
        post("/inbox/decide", data={"article_id": aid, "choice": "save"}, headers=HTMX).status_code
        == 404
    )


def test_init_group_bulk_dismiss_asks_first(wiki, tmp_path):
    keep = held(wiki, "https://t/a", "매일 보류")
    held(wiki, "https://t/b", "초기 1", origin="init")
    held(wiki, "https://t/c", "초기 2", origin="init")
    c, index = make_client(wiki, tmp_path)

    ask = c.get("/inbox?confirm=init").text
    assert (
        "2건을 모두 관련없음으로 남기고 지울까요?" in ask and 'hx-post="/inbox/dismiss-init"' in ask
    )

    done = c.post("/inbox/dismiss-init", headers=HTMX)
    assert done.status_code == 200 and "초기 수집 보류" not in done.text
    assert [a.id for a in index.select("WHERE bucket = ?", ("inbox",))] == [keep]
    assert [f.kind for f in load_feedback(wiki)] == ["not_relevant", "not_relevant"]


def test_neighbor_picks_next_then_previous():
    from context_holder.index import IndexedArticle

    order = [
        IndexedArticle(i, "", "", "", None, Bucket.INBOX, "done", None, None, "") for i in "abc"
    ]
    assert inboxview.neighbor(order, "a") == "b"
    assert inboxview.neighbor(order, "c") == "b"
    assert inboxview.neighbor(order, "zz") == "a"
    assert inboxview.neighbor(order[:1], "a") is None
    assert inboxview.neighbor([], "a") is None


# ---------- Search ----------


def test_search_without_query_offers_decision_topics(wiki, tmp_path):
    add(wiki, "https://t/a", "사이드카 운영", topic="사이드카 관리")
    c, _ = make_client(wiki, tmp_path)
    page = c.get("/search").text
    assert "글 1건" in page and "결정 사례 1건" not in page
    assert 'id="search-loading"' in page and 'id="search-failed"' in page  # 찾는 동안 · 실패 안내
    assert (
        'href="/search?q=%EC%82%AC%EC%9D%B4%EB%93%9C%EC%B9%B4+%EA%B4%80%EB%A6%AC&amp;kind=decision"'
        in page
    )


def test_search_start_shows_how_to_ask_claude(wiki, tmp_path):
    """검색어 없는 첫 화면: 내 위키에 있는 주제로 만든 Claude Code 질문 예시를 복사할 수 있다."""
    add(wiki, "https://t/a", "사이드카 운영", topic="사이드카 관리")
    c, _ = make_client(wiki, tmp_path)
    page = c.get("/search").text
    assert "Claude Code에서 묻기" in page
    assert "Context Holder 위키에서 사이드카 관리 사례를 찾아서" in page
    assert page.count('data-copy="ask-claude-') == 3
    assert "ask-status" not in page  # 연결 상태를 모르는 화면(개발 모드)은 상태를 비운다
    assert "Claude Code에서 묻기" not in c.get("/search?q=사이드카").text


def test_claude_prompts_without_topics_use_examples():
    prompts = searchview.claude_prompts([])
    assert len(prompts) == 3 and "메시지 큐" in prompts[0][1]


def test_search_highlights_filters_and_opens_detail(wiki, tmp_path):
    a = add(wiki, "https://t/a", "사이드카 버전 관리", topic="사이드카 관리", source="toss")
    b = add(wiki, "https://t/b", "사이드카 없이 운영하기", topic="사이드카 관리")
    add(wiki, "https://t/c", "사이드카 소개", published=NOW - timedelta(days=400))
    c, _ = make_client(wiki, tmp_path, FakeEmbedder())

    page = c.get("/search?q=사이드카").text
    assert "결과 3건" in page and "<mark>사이드카</mark>" in page
    assert 'value="toss">Toss</label>' in page and "<b>" not in page  # 개수는 보이지 않는다
    assert 'value="kubernetes">kubernetes</label>' in page
    assert "search-results-more" not in page  # 한 쪽에 다 들어간다
    assert f'hx-get="/article/{a}"' in page and 'hx-target="#modal-body"' in page
    assert "번째 단락" not in page and "번째 줄" not in page  # 구현 문구는 보여 주지 않는다
    assert "초</span>" not in page

    decisions = c.get("/search?q=사이드카&kind=decision&period=1y").text
    assert "결과 2건" in decisions and "사이드카 소개" not in decisions

    toss = c.get("/search?q=사이드카&company=toss").text
    assert "결과 1건" in toss and 'value="toss" checked' in toss

    partial = c.get("/search?q=사이드카", headers=HTMX).text
    assert "<html" not in partial and f"/article/{b}" in partial

    tagged = c.get("/search?q=사이드카&tag=없는태그").text
    assert "맞는 글이 없어요" in tagged and 'value="없는태그" checked' in tagged


def test_article_modal_shows_everything(wiki, tmp_path):
    a = add(wiki, "https://t/a", "사이드카 버전 관리", topic="사이드카 관리", source="toss")
    b = add(wiki, "https://t/b", "사이드카 없이 운영하기", topic="사이드카 관리")
    plain = add(wiki, "https://t/c", "그냥 글")
    c, _ = make_client(wiki, tmp_path, FakeEmbedder())

    modal = c.get(f"/article/{a}").text
    assert "<html" not in modal and 'id="article-title">사이드카 버전 관리</h2>' in modal
    assert "핵심 내용" in modal and "<q>근거 문장</q>" in modal  # 결정 기록 전체와 근거
    assert "결정 기록 복사" in modal and 'data-copy="modal-decision-md"' in modal
    assert "원문 열기" in modal and 'href="https://t/a"' in modal
    assert "비슷한 결정" in modal and f'hx-get="/article/{b}"' in modal
    assert "Claude에 문맥으로" not in modal

    other = c.get(f"/article/{plain}").text
    assert "비슷한 결정" not in other and "결정 기록 복사" not in other
    assert c.get("/article/none").status_code == 404


def test_article_modal_bookmark_icon(wiki, tmp_path):
    a = add(wiki, "https://t/a", "사이드카 버전 관리")
    c, _ = make_client(wiki, tmp_path)

    modal = c.get(f"/article/{a}").text
    assert 'class="icon-btn bookmark-toggle"' in modal and 'aria-pressed="false"' in modal
    assert 'aria-label="북마크"' in modal and ">북마크<" not in modal  # 글자 버튼이 아니라 아이콘
    assert "관련없음" not in modal and "위키에서 제거" in modal

    c.post("/feedback", data={"article_id": a, "kind": "bookmark"}, headers=HTMX)
    again = c.get(f"/article/{a}").text
    assert 'aria-pressed="true"' in again and 'aria-label="북마크 해제"' in again
    assert "popped" not in again  # 열 때는 튀지 않는다


def test_remove_from_wiki_confirms_in_popup_then_deletes_without_feedback(wiki, tmp_path):
    a = add(wiki, "https://t/a", "지울 글", topic="캐시")
    keep = add(wiki, "https://t/b", "남길 글")
    (wiki.root / "vectors/m").mkdir(parents=True)
    (wiki.root / f"vectors/m/{a}.f16").write_bytes(b"x")
    c, index = make_client(wiki, tmp_path)
    path = wiki.root / index.get(a).path
    c.post("/feedback", data={"article_id": a, "kind": "bookmark"}, headers=HTMX)

    modal = c.get(f"/article/{a}").text  # 확인 팝업은 상세창에 함께 온다
    assert 'data-confirm-open="remove-confirm"' in modal and 'id="remove-confirm"' in modal
    assert "되돌릴 수 없어요" in modal and f'hx-post="/article/{a}/remove"' in modal
    assert "data-confirm-close autofocus" in modal  # 열리면 취소에 초점
    assert "data-remove-article" in modal and "data-confirm-error hidden" in modal
    assert path.exists()

    done = c.post(f"/article/{a}/remove", headers=HTMX)
    assert done.status_code == 204  # 상세창 닫기와 새로 그리기는 app.js가 한다
    assert not path.exists() and index.get(a) is None
    assert not list((wiki.root / "vectors").glob(f"*/{a}.*"))
    assert index.get(keep) is not None
    assert [f.kind for f in load_feedback(wiki)] == ["bookmark"]  # 관련없음 등을 남기지 않는다
    assert "지울 글" not in c.get("/bookmarks").text
    assert c.get(f"/article/{a}").status_code == 404
    assert c.post(f"/article/{a}/remove", headers=HTMX).status_code == 404
    assert c.post(f"/article/{keep}/remove").status_code == 403  # HTMX 헤더 없는 쓰기


def test_remove_keeps_everything_when_index_is_locked(wiki, tmp_path, monkeypatch):
    import sqlite3

    a = add(wiki, "https://t/a", "지울 글")
    c, index = make_client(wiki, tmp_path)
    path = wiki.root / index.get(a).path

    def locked(_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(index, "remove", locked)
    busy = c.post(f"/article/{a}/remove", headers=HTMX)
    assert busy.status_code == 503 and "잠시 후 다시 시도해 주세요" in busy.text
    assert path.exists()  # 인덱스가 실패하면 파일도 지우지 않는다


def test_search_results_do_not_push_article_urls(wiki, tmp_path):
    """폼의 hx-push-url을 결과 링크가 물려받으면 글을 열 때 주소가 /article/…로 바뀐다."""
    add(wiki, "https://t/a", "사이드카 운영")
    c, _ = make_client(wiki, tmp_path)
    page = c.get("/search?q=사이드카").text
    assert '<div id="search-body" class="search-body" hx-push-url="false">' in page


def test_bookmarks_page_lists_latest_first_and_pages(wiki, tmp_path, monkeypatch):
    monkeypatch.setattr(bookmarksview, "PAGE_SIZE", 2)
    ids = [add(wiki, f"https://t/{i}", f"글 {i}", topic=None if i else "캐시") for i in range(4)]
    c, _ = make_client(wiki, tmp_path)

    empty = c.get("/bookmarks").text
    assert "책갈피 아이콘으로 북마크하면" in empty
    assert '<a href="/bookmarks" class="active" aria-current="page">' in empty

    for aid in ids:
        c.post("/feedback", data={"article_id": aid, "kind": "bookmark"}, headers=HTMX)
    c.post("/feedback", data={"article_id": ids[2], "kind": "unbookmark"}, headers=HTMX)

    page = c.get("/bookmarks").text
    assert "글 3" in page and "글 1" in page and "글 2" not in page and "글 0" not in page
    assert page.index("글 3") < page.index("글 1")  # 최근에 북마크한 글부터
    assert f'hx-get="/article/{ids[3]}"' in page and 'hx-target="#modal-body"' in page
    assert f'hx-get="/bookmarks/more?after={ids[1]}"' in page

    more = c.get(f"/bookmarks/more?after={ids[1]}").text
    assert 'hx-swap-oob="beforeend:#bookmark-list"' in more
    assert "글 0" in more and "결정 사례" in more and "/bookmarks/more" not in more


def test_search_without_model_says_keyword_only(wiki, tmp_path):
    add(wiki, "https://t/a", "사이드카 운영")
    c, _ = make_client(wiki, tmp_path)
    page = c.get("/search?q=사이드카").text
    assert searchview.NO_MODEL_SCREEN in page and NO_MODEL_NOTE not in page


def test_search_skips_articles_whose_file_is_gone(wiki, tmp_path):
    add(wiki, "https://t/a", "사이드카 운영")
    c, _ = make_client(wiki, tmp_path)
    next((wiki.root / "wiki").rglob("*.md")).unlink()
    assert "맞는 글이 없어요" in c.get("/search?q=사이드카").text


def test_query_parsing_and_urls():
    q = searchview.parse_query(
        {"q": "  outbox ", "kind": "bad", "period": "30d"},
        {"company": ["toss", "toss"], "tag": ["cdc"]},
    )
    assert q == searchview.Query("outbox", "all", "30d", ("toss",), ("cdc",))
    assert q.url() == "/search?q=outbox&period=30d&company=toss&tag=cdc"
    assert q.toggle("companies", "toss") == "/search?q=outbox&period=30d&tag=cdc"
    assert q.toggle("tags", "kafka").endswith("tag=cdc&tag=kafka")
    assert searchview.Query().url() == "/search"
    assert searchview.Query(kind="decision").url() == "/search?kind=decision"


def test_highlight_marks_terms_case_insensitively():
    assert searchview.highlight("Outbox와 outbox", ["outbox"]) == [
        ("Outbox", True),
        ("와 ", False),
        ("outbox", True),
    ]
    assert searchview.highlight("없음", []) == [("없음", False)]
    assert searchview.highlight("a outbox b", ["outbox"])[-1] == (" b", False)


@pytest.mark.parametrize(
    ("matched", "text", "where"),
    [
        ("요약 2번째 줄", "둘째 줄", "요약 2번째 줄"),
        ("요약 9번째 줄", "글 요약 첫 줄", "요약 9번째 줄"),
        ("핵심 내용 1번째 줄", "핵심", "핵심 내용 1번째 줄"),
        ("결정 기록", "버전이 제각각", "결정 기록"),
        ("원문 4번째 단락", "글 요약 첫 줄", "원문 4번째 단락에서 맞음"),
        ("키워드", "핵심", "키워드"),
        ("제목", "글 요약 첫 줄", "제목"),
    ],
)
def test_snippet_follows_the_matched_field(wiki, matched, text, where):
    add(wiki, "https://t/a", "글", topic="주제")
    stored = next(wiki.articles())
    assert searchview._snippet(stored, matched, ["핵심"]) == (text, where)


def test_period_filter_uses_collected_and_drops_undated(wiki, tmp_path):
    add(wiki, "https://t/a", "글")
    _, index = make_client(wiki, tmp_path)
    indexed = index.select()[0]
    month = searchview.Query(q="x", period="30d")
    assert searchview._keeps(replace(indexed, published=None), month, NOW)
    naive = replace(indexed, published="2026-11-01T00:00:00")
    assert searchview._keeps(naive, month, NOW)
    undated = replace(indexed, published=None, collected=None)
    assert not searchview._keeps(undated, month, NOW)


def test_search_pages_results_by_cursor(wiki, tmp_path):
    for n in range(25):
        add(wiki, f"https://t/{n}", f"사이드카 글 {n:02d}", source="toss" if n % 2 else "d2")
    c, _ = make_client(wiki, tmp_path, FakeEmbedder())

    page = c.get("/search?q=사이드카&period=1y").text
    assert "결과 25건" in page and page.count('hx-target="#modal-body"') == 20
    assert 'id="search-results-more"' in page and "/search/more?q=" in page
    after = page.split("&amp;after=")[1].split('"')[0]

    more = c.get(f"/search/more?q=사이드카&period=1y&after={after}").text
    assert '<ol hx-swap-oob="beforeend:#search-results">' in more
    assert more.count('hx-target="#modal-body"') == 5 and "search-results-more" not in more

    toss = c.get(f"/search/more?q=사이드카&company=toss&after={after}").text
    assert "search-results-more" not in toss  # 필터가 바뀌어 커서가 없으면 빈 쪽
    assert searchview.Query(q="a b", tags=("x",)).more_url("id1") == (
        "/search/more?q=a+b&tag=x&after=id1"
    )


# ---------- Sources ----------


def by_source(**entries) -> dict:
    return {"result": {"by_source": entries}, "finished_at": NOW.isoformat(), "status": "done"}


def test_sources_counts_new_saved_and_pass_rate_over_30_days(wiki, tmp_path):
    ok = {"found": 5, "new": 4, "failed": 0, "saved": 3, "error": None}
    run_file(wiki, TODAY, by_source(toss=ok, d2={**ok, "new": 0, "saved": 0, "error": "시간 초과"}))
    run_file(wiki, TODAY - timedelta(days=7), by_source(toss={**ok, "new": 2, "saved": 1}))
    run_file(wiki, TODAY - timedelta(days=40), by_source(toss={**ok, "new": 99}))
    run_file(wiki, TODAY - timedelta(days=50), {"status": "done", "result": {"saved": 1}})

    view = sourcesview.build_sources(wiki, tmp_path / "none.jsonl", NOW)

    toss, d2 = view.domestic
    assert (toss.new, toss.saved, toss.pass_rate) == (6, 4, 67)
    assert toss.spark == [2, 2, 2, 10, 18] and toss.last_check == f"오늘 {CLOCK}"
    assert d2.pass_rate is None and d2.error == "시간 초과"
    assert view.failures == [("NAVER D2", "시간 초과")] and view.has_records


def test_sources_page_without_records(wiki, tmp_path):
    c, _ = make_client(wiki, tmp_path)
    page = c.get("/sources").text
    assert "다음 수집부터 소스별" in page and "기록 없음" not in page and "spark" not in page
    assert "아직 후보가 없어요" in page and "2곳" in page


def test_sources_page_shows_failures_and_candidates(wiki, tmp_path):
    ok = {"found": 1, "new": 1, "failed": 0, "saved": 1, "error": None}
    run_file(wiki, TODAY - timedelta(days=1), by_source(toss=ok, d2={**ok, "error": "403"}))
    record_feedback(wiki, "air", Kind.SUGGEST_SOURCE, "https://spotify.eng", NOW, "음악 추천")
    log = tmp_path / "mcp-queries.jsonl"
    rows = [
        {
            "at": NOW.isoformat(),
            "tool": "find_decision_cases",
            "args": {"topic": "feature flag"},
            "ids": [],
        },
        {
            "at": NOW.isoformat(),
            "tool": "find_decision_cases",
            "args": {"topic": "feature flag"},
            "ids": [],
        },
        {"at": NOW.isoformat(), "tool": "search_wiki", "args": {"queries": ["hit"]}, "ids": ["x"]},
        {
            "at": (NOW - timedelta(days=40)).isoformat(),
            "tool": "search_wiki",
            "args": {},
            "ids": [],
        },
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\nbroken\n")
    c, _ = make_client(wiki, tmp_path)

    page = c.get("/sources").text

    assert "소스 1곳을 확인하지 못했어요" in page and "NAVER D2: 403" in page
    day = TODAY - timedelta(days=1)
    assert f"{day.month}월 {day.day}일 {CLOCK}" in page
    assert "결정 사례 · feature flag" in page and "(2회)" in page
    assert "https://spotify.eng" in page and "음악 추천" in page
    assert page.count("위키에 없었어요") == 1 and "feed 본문" not in page


def test_missed_queries_without_log(tmp_path):
    assert sourcesview.missed_queries(tmp_path / "none", NOW) == []


# ---------- 상태 알림 ----------


def test_no_alerts_for_fresh_wiki_or_recent_run(wiki):
    assert alerts(wiki, 0, NOW) == []
    run_file(wiki, TODAY - timedelta(days=1), {"status": "done"})
    assert alerts(wiki, 0, NOW) == []


def test_stale_alert_after_two_days(wiki):
    run_file(wiki, TODAY - timedelta(days=3), {"status": "done"})
    (found,) = alerts(wiki, 0, NOW)
    assert found.tone == "error" and found.title == "마지막 수집이 3일 전이에요."


def test_stopped_alert_names_the_reason_and_pending(wiki, tmp_path):
    run_file(wiki, TODAY, {"status": "stopped", "result": {"stopped": "auth"}})
    add(wiki, "https://t/p", "대기 글", pending=True)
    (found,) = alerts(wiki, 1, NOW)
    assert found.tone == "busy" and "로그인이 풀려" in found.title and "요약 대기 1건" in found.body
    c, _ = make_client(wiki, tmp_path)
    assert "로그인이 풀려" in c.get("/today").text and "로그인이 풀려" in c.get("/sources").text


def test_stopped_day_does_not_claim_everything_was_classified(wiki, tmp_path):
    run_file(wiki, TODAY, {"status": "stopped", "result": {"stopped": "auth", "saved": 0}})
    c, _ = make_client(wiki, tmp_path)
    today = c.get("/today").text
    assert "아직 분류하지 못한 글" in today and "모두 보류나 제외로" not in today


def test_stopped_alert_for_old_records_without_reason(wiki):
    run_file(wiki, TODAY, {"status": "stopped"})
    assert alerts(wiki, 0, NOW)[0].title == "수집이 중간에 멈췄어요."


def test_run_records_skip_init_and_broken_files(wiki):
    run_file(wiki, TODAY, {"status": "done"})
    (wiki.root / "state/runs/init.json").write_text("{}")
    (wiki.root / "state/runs/2026-11-01.json").write_text("{broken")
    assert [d for d, _ in run_records(wiki)] == [TODAY]


# ---------- 지금 수집 (앱이 띄웠을 때) ----------


class FakeScheduler:
    def __init__(self, running=False, paused=None, can_run=True):
        from context_holder.scheduler import Progress

        self.running = running
        self.progress = Progress("collect", 7, 13) if running else None
        self.started_at = NOW if running else None
        self.paused = paused
        self.can_run = can_run
        self.runs = 0

    def paused_until(self):
        return self.paused

    def can_run_now(self):
        return self.can_run and not self.running

    def run_now(self):
        from context_holder.scheduler import Progress

        self.runs += 1
        self.running, self.started_at = True, NOW
        self.progress = Progress("collect", 7, 13)
        return True


def app_client(wiki, tmp_path, scheduler) -> TestClient:
    ctx = replace(context(wiki, tmp_path), scheduler=scheduler)
    c = TestClient(create_app(ctx), follow_redirects=False)
    c.cookies.set(COOKIE, TOKEN)
    return c


def test_sync_now_button_starts_collection_and_card_polls(wiki, tmp_path):
    scheduler = FakeScheduler()
    c = app_client(wiki, tmp_path, scheduler)
    assert 'hx-post="/sync"' in c.get("/today").text

    card = c.post("/sync", headers=HTMX)
    assert scheduler.runs == 1
    assert 'hx-get="/sync-card"' in card.text and "이 기기가 수집 중" in card.text
    assert "소스 7/13" in card.text and "width:54%" in card.text and "<html" not in card.text

    poll = c.get("/sync-card")
    assert "HX-Refresh" not in poll.headers
    scheduler.running = False
    assert c.get("/sync-card").headers["HX-Refresh"] == "true"  # 끝났다: 화면을 새로 고친다


def test_sync_card_shows_pause_and_hides_button_when_done(wiki, tmp_path):
    from datetime import timedelta as td

    c = app_client(wiki, tmp_path, FakeScheduler(paused="forever", can_run=False))
    page = c.get("/today").text
    assert "수집 일시정지 중 (다시 켤 때까지)" in page and 'hx-post="/sync"' not in page
    later = NOW + td(hours=1)
    page = app_client(wiki, tmp_path / "2", FakeScheduler(paused=later)).get("/today").text
    assert f"수집 일시정지 중 ({later.astimezone():%H:%M}까지)" in page


def test_classify_progress_has_no_ratio(wiki, tmp_path):
    from context_holder.scheduler import Progress

    scheduler = FakeScheduler(running=True)
    scheduler.progress = Progress("classify", 0, 11)
    page = app_client(wiki, tmp_path, scheduler).get("/today").text
    assert "분류 중 (11건)" in page and 'role="progressbar"' not in page


def test_stale_banner_offers_sync_only_inside_the_app(wiki, tmp_path):
    run_file(wiki, TODAY - timedelta(days=3), {"status": "done"})
    assert "마지막 수집이 3일 전" in make_client(wiki, tmp_path)[0].get("/today").text
    plain = make_client(wiki, tmp_path / "p")[0].get("/today").text
    assert 'class="btn sm primary" hx-post="/sync"' not in plain
    inside = app_client(wiki, tmp_path / "a", FakeScheduler()).get("/today").text
    assert 'class="btn sm primary" hx-post="/sync"' in inside


def test_sync_without_app_is_refused(wiki, tmp_path):
    c, _ = make_client(wiki, tmp_path)
    assert c.post("/sync", headers=HTMX).status_code == 409
    assert c.get("/sync-card").headers["HX-Refresh"] == "true"


def test_stopped_alert_names_the_chosen_agent(wiki):
    run_file(wiki, TODAY, {"status": "stopped", "result": {"stopped": "limit"}})
    assert alerts(wiki, 0, NOW)[0].title.startswith("Claude 사용 한도")
    assert alerts(wiki, 0, NOW, agent="codex")[0].title.startswith("Codex 사용 한도")
    run_file(wiki, TODAY, {"status": "stopped", "result": {"stopped": "not_installed"}})
    assert "Codex CLI를 찾지 못해" in alerts(wiki, 0, NOW, agent="codex")[0].title


def test_sync_login_needs_the_app_and_a_pending_login(wiki, tmp_path):
    c = app_client(wiki, tmp_path, FakeScheduler())
    assert c.post("/sync/login", headers=HTMX).status_code == 409
    assert c.get("/sync-card/login").status_code == 200  # 기다리는 로그인이 없으면 카드만
