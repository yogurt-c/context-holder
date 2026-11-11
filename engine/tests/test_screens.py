import io
import json
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from context_holder import cli
from context_holder.classify import Bucket, Decision
from context_holder.collect import Article
from context_holder.enrich import Confidence, DecisionRecord, Enrichment, Media
from context_holder.feedback import load_feedback
from context_holder.index import Index
from context_holder.screens import serve as serve_module
from context_holder.screens import views
from context_holder.screens.app import COOKIE, ScreenContext, create_app, new_token
from context_holder.wiki import Wiki, article_id

NOW = datetime(2026, 11, 9, 3, tzinfo=UTC)  # 서울 12시
TODAY = NOW.astimezone().date()
TOKEN = "t" * 43
SOURCES = """sources:
  - {id: toss, name: Toss, region: domestic, feed: 'https://toss.tech/rss', weight: 2}
  - {id: d2, name: NAVER D2, region: domestic, feed: 'https://d2.naver.com/d2.atom'}
"""


def add(
    wiki: Wiki,
    url: str,
    title: str,
    score: float = 0.8,
    collected: datetime = NOW,
    published: datetime | None = None,
    source: str = "d2",
    bucket: Bucket = Bucket.SAVE,
    topic: str | None = None,
    media: bool = False,
    pending: bool = False,
) -> str:
    article = Article(source, url, title, published or collected, "본문", "h")
    decision = (
        DecisionRecord(
            topic,
            "버전이 제각각",
            ("직접 관리", "SidecarSet"),
            "SidecarSet",
            ("재시작 없음 ↔ 운영 부담",),
            "통일",
            ("근거 문장",),
            (),
            Confidence.HIGH,
        )
        if topic
        else None
    )
    enrichment = Enrichment(
        (f"{title} 요약 첫 줄", "둘째 줄"),
        ("핵심",),
        ("kubernetes", "ops"),
        decision,
        Media("https://v", "youtube") if media else None,
        0.0,
    )
    wiki.write_article(
        article,
        Decision(url, score, bucket, "운영", (), ()),
        None if pending else enrichment,
        collected,
    )
    return article_id(url)


def run_file(wiki: Wiki, day: date, record: dict) -> None:
    path = wiki.root / "state/runs" / f"{day.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


@pytest.fixture
def wiki(tmp_path) -> Wiki:
    wiki = Wiki(tmp_path / "wiki")
    wiki.ensure_config()
    (wiki.root / "config/sources.yaml").write_text(SOURCES, encoding="utf-8")
    return wiki


def context(wiki: Wiki, tmp_path: Path) -> ScreenContext:
    index = Index(tmp_path / "index.db")
    index.sync(wiki)
    return ScreenContext(
        wiki=wiki,
        index=index,
        device="air",
        mcp_log=tmp_path / "mcp-queries.jsonl",
        clock=lambda: NOW,
        token=TOKEN,
        hosts=frozenset({"testserver"}),
    )


def client(wiki: Wiki, tmp_path: Path) -> TestClient:
    c = TestClient(create_app(context(wiki, tmp_path)), follow_redirects=False)
    c.cookies.set(COOKIE, TOKEN)
    return c


# ---------- 순위 ----------


def test_rank_multiplies_relevance_freshness_and_company_weight(wiki, tmp_path):
    add(wiki, "https://t/old", "오래된 글", 0.9, published=NOW - timedelta(days=7))
    add(wiki, "https://t/new", "새 글", 0.9, published=NOW)
    add(wiki, "https://t/toss", "토스 글", 0.5, source="toss")
    index = context(wiki, tmp_path).index

    ranked = views.rank(index.select(), {"toss": 2.0}, {"toss": "Toss"}, NOW)

    assert [r.article.title for r in ranked] == ["토스 글", "새 글", "오래된 글"]
    assert ranked[0].company == "Toss" and ranked[1].company == "d2"
    assert ranked[2].score == pytest.approx(0.45)  # 7일이면 절반


def test_freshness_falls_back_to_collected_and_handles_missing_dates():
    from context_holder.index import IndexedArticle

    base = IndexedArticle("a", "u", "t", "d2", None, Bucket.SAVE, "done", None, None, "p")
    assert views.freshness(base, NOW) == 1.0
    naive = IndexedArticle(
        "a", "u", "t", "d2", None, Bucket.SAVE, "done", None, None, "p", "2026-11-02T03:00:00"
    )
    assert views.freshness(naive, NOW) == pytest.approx(0.5)
    future = IndexedArticle(
        "a",
        "u",
        "t",
        "d2",
        (NOW + timedelta(days=1)).isoformat(),
        Bucket.SAVE,
        "done",
        None,
        None,
        "p",
    )
    assert views.freshness(future, NOW) == 1.0


def test_day_labels_and_local_day():
    assert views.long_day(date(2026, 11, 9)) == "11월 9일 월요일"
    assert views.local_day(None) is None
    assert (
        views.local_day(datetime(2026, 11, 9, 3))
        == datetime(2026, 11, 9, 3, tzinfo=UTC).astimezone().date()
    )


# ---------- 수집 현황 ----------


def test_run_status_for_each_state(wiki):
    status = views.run_status
    assert status(wiki, TODAY, "air", True).label == "오늘 아직 수집 전"
    assert status(wiki, TODAY, "air", False).label == "이날은 수집하지 않았어요"

    started = NOW.isoformat()
    run_file(wiki, TODAY, {"device": "studio", "status": "running", "started_at": started})
    running = status(wiki, TODAY, "air", True)
    assert (running.state, running.label, running.mine) == ("running", "studio가 수집 중", False)
    assert running.foot.endswith("에 시작") and running.counts == {}
    run_file(wiki, TODAY, {"device": "air", "status": "running"})
    assert status(wiki, TODAY, "air", True).label == "이 기기가 수집 중"
    assert status(wiki, TODAY, "air", True).foot == "--:--에 시작"

    result = {"saved": 2, "inboxed": 1, "dropped": 3, "pending": 0}
    run_file(wiki, TODAY, {"device": "air", "status": "stopped", "result": result})
    assert status(wiki, TODAY, "air", True).label == "수집이 중간에 멈췄어요"

    run_file(
        wiki,
        TODAY,
        {"device": "studio", "status": "done", "finished_at": started, "result": result},
    )
    done = status(wiki, TODAY, "air", True)
    assert done.label.startswith("오늘 ") and done.label.endswith("수집 완료")
    assert done.counts == {"신규": 6, "저장": 2, "보류": 1, "제외": 3}
    assert done.foot == "studio에서 수집"
    assert not status(wiki, TODAY, "air", False).label.startswith("오늘")


# ---------- 에이전트 검색 기록 ----------


def test_mcp_entries_keep_recent_searches_only(tmp_path):
    log = tmp_path / "q.jsonl"
    rows = [
        {"at": (NOW - timedelta(days=8)).isoformat(), "tool": "search_wiki", "args": {}, "ids": []},
        {"at": NOW.isoformat(), "tool": "get_article", "args": {"id": "x"}, "ids": ["x"]},
        {
            "at": NOW.isoformat(),
            "tool": "search_wiki",
            "args": {"queries": ["사이드카", "a very long english query", "셋째"]},
            "ids": ["1", "2"],
        },
        {
            "at": NOW.isoformat(),
            "tool": "find_decision_cases",
            "args": {"problem": "피처 플래그"},
            "ids": [],
        },
    ]
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n깨진 줄\n")

    entries = views.mcp_entries(log, NOW)

    assert entries == [
        views.McpEntry("결정 사례 · 피처 플래그", 0, "결정 사례 · 피처 플래그"),
        views.McpEntry("사이드카 외 2개", 2, "사이드카 · a very long english query · 셋째"),
    ]
    assert views.mcp_entries(tmp_path / "없음.jsonl", NOW) == []


def test_mcp_entries_merge_repeats_and_clip_at_words(tmp_path):
    log = tmp_path / "q.jsonl"
    long = "transactional outbox pattern CDC Debezium"
    rows = [
        {
            "at": NOW.isoformat(),
            "tool": "find_decision_cases",
            "args": {"topic": "서비스 간 데이터 동기화"},
            "ids": ["1"],
        },
        {"at": NOW.isoformat(), "tool": "search_wiki", "args": {"queries": [long]}, "ids": []},
        {
            "at": NOW.isoformat(),
            "tool": "find_decision_cases",
            "args": {"topic": "서비스 간 데이터 동기화"},
            "ids": ["1", "2"],
        },
        {"at": NOW.isoformat(), "tool": "search_wiki", "args": {"queries": ["가" * 30]}, "ids": []},
    ]
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))

    entries = views.mcp_entries(log, NOW)

    assert [e.call for e in entries] == [
        "가" * 19 + "…",
        "결정 사례 · 서비스 간 데이터 동기화",
        "transactional…",
    ]
    assert entries[1].times == 2 and entries[1].hits == 2  # 가장 최근 건수
    assert entries[2].full == long
    assert views.call_text("search_wiki", {}) == ("", "")


# ---------- Today ----------


def test_today_shows_lead_with_decision_and_ranked_briefs(wiki, tmp_path):
    lead = add(wiki, "https://t/lead", "사이드카 선언적 관리", 0.95, topic="사이드카 관리 방식")
    for n in range(6):
        add(wiki, f"https://t/{n}", f"글 {n}", 0.5 - n * 0.01, media=n == 0)
    add(wiki, "https://t/inbox", "보류 글", 0.45, bucket=Bucket.INBOX)
    add(wiki, "https://t/pending", "대기 글", 0.7, pending=True, collected=NOW - timedelta(days=1))
    result = {"saved": 7, "inboxed": 1, "dropped": 2, "pending": 1}
    run_file(
        wiki,
        TODAY,
        {"device": "air", "status": "done", "finished_at": NOW.isoformat(), "result": result},
    )

    page = client(wiki, tmp_path).get("/today").text

    assert "오늘의 브리프" in page
    assert "사이드카 선언적 관리" in page and "relevance" not in page  # 점수는 보여 주지 않는다
    assert "결정 기록: 사이드카 관리 방식" in page and "신뢰도 높음" in page
    assert '<span class="opt chosen">SidecarSet</span>' in page
    assert "원문 근거 문장 1개, 모두 원문에서 확인했어요" in page
    assert 'data-copy="decision-md"' in page and "## 사이드카 관리 방식 (NAVER D2)" in page
    assert f'"article_id": "{lead}"' in page
    assert "글 0" in page and "영상, 요약만" in page
    # 리드 + 4건만 그린다. 나머지 2건은 "더 보기"로 부른다 (5위 글이 커서)
    assert "글 3" in page and "글 4" not in page and "글 5" not in page
    fifth = article_id("https://t/3")
    assert f'hx-get="/today/more?date={TODAY.isoformat()}&amp;after={fifth}"' in page
    assert page.count('hx-target="#modal-body"') == 5  # 리드와 2~5위 제목은 상세 모달을 연다
    assert f'<article class="lead" data-article="{lead}">' in page  # 리드는 아무 곳이나 눌러도 연다
    assert f'<h2><a href="#" hx-get="/article/{lead}"' in page  # 키보드로도 연다
    assert "요약 대기 1" in page
    assert "저장 7건, 보류 1건, 제외 2건" in page
    assert "보류 글" in page  # Inbox 미리보기
    assert "같은 주제로 모인 사례가 생기면" in page
    assert "아직 기록이 없어요" in page
    assert "결정 기록 복사" in page and "Claude에 문맥으로" not in page


def test_today_without_saved_articles_shows_recent_week(wiki, tmp_path):
    add(wiki, "https://t/a", "어제 글", 0.9, collected=NOW - timedelta(days=1), topic="캐시")
    add(wiki, "https://t/b", "같은 주제", 0.8, collected=NOW - timedelta(days=2), topic="캐시")
    add(wiki, "https://t/old", "지난달 글", 0.9, collected=NOW - timedelta(days=30))
    c = client(wiki, tmp_path)

    page = c.get("/today").text
    assert "오늘은 아직 수집하지 않았어요." in page
    assert "최근 7일 상위 글" in page and "어제 글" in page and "지난달 글" not in page
    assert "캐시<b>2</b>" in page  # 2건 이상 묶인 주제는 막대로 보여 준다
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    assert f'href="/today?date={yesterday}"' in page

    run_file(wiki, TODAY, {"device": "air", "status": "running"})
    assert "이 기기가 수집 중이에요" in c.get("/today").text
    run_file(wiki, TODAY, {"device": "air", "status": "done", "result": {"inboxed": 2}})
    assert "신규 2건은 모두 보류나 제외로" in c.get("/today").text
    run_file(wiki, TODAY, {"device": "air", "status": "done"})
    assert "새로 저장할 글이 없어요." in c.get("/today").text


def test_today_navigates_between_collected_days(wiki, tmp_path):
    two_days_ago = NOW - timedelta(days=2)
    add(wiki, "https://t/a", "그제 글", collected=two_days_ago)
    c = client(wiki, tmp_path)
    day = two_days_ago.astimezone().date()

    page = c.get(f"/today?date={day.isoformat()}").text
    assert f"{day.month}월 {day.day}일 브리프" in page and "그제 글" in page
    # 이전 · 다음 날은 조회하지 않고 하루씩 옮긴다
    assert f'href="/today?date={(day + timedelta(days=1)).isoformat()}"' in page
    assert f'href="/today?date={(day - timedelta(days=1)).isoformat()}"' in page
    assert (
        "이날은 수집하지 않았어요."
        in c.get(f"/today?date={(day - timedelta(days=1)).isoformat()}").text
    )
    today_page = c.get("/today?date=nope").text
    assert "오늘의 브리프" in today_page and 'aria-label="다음 날 보기"' not in today_page
    assert "오늘로" in page  # 그저께: 오늘로 바로 간다
    assert "오늘로" not in today_page
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    assert "오늘로" not in c.get(f"/today?date={yesterday}").text  # 다음 날이 곧 오늘
    future = (TODAY + timedelta(days=3)).isoformat()
    assert "오늘의 브리프" in c.get(f"/today?date={future}").text  # 내일 이후는 오늘로


def test_today_more_pages_by_cursor(wiki, tmp_path):
    for n in range(17):  # 리드 + 4건, 더 보기 10건, 다시 2건
        add(wiki, f"https://t/{n}", f"글 {n:02d}", 0.9 - n * 0.01)
    c = client(wiki, tmp_path)
    ids = [article_id(f"https://t/{n}") for n in range(17)]

    first = c.get(f"/today/more?date={TODAY.isoformat()}&after={ids[4]}").text
    assert '<ol hx-swap-oob="beforeend:#today-briefs">' in first
    assert "글 05" in first and "글 14" in first and "글 04" not in first and "글 15" not in first
    assert f"after={ids[14]}" in first and 'id="today-briefs-more"' in first

    last = c.get(f"/today/more?date={TODAY.isoformat()}&after={ids[14]}").text
    assert "글 15" in last and "글 16" in last
    assert "today-briefs-more" not in last  # 끝: 버튼 자리를 비운다

    gone = c.get(f"/today/more?date={TODAY.isoformat()}&after=nope").text
    assert "<li>" not in gone and "today-briefs-more" not in gone
    assert "글 05" in c.get(f"/today/more?after={ids[4]}").text  # 날짜가 없으면 오늘


def test_saved_between_uses_local_days_whatever_the_offset(wiki, tmp_path):
    aid = add(wiki, "https://t/a", "오늘 글")
    add(wiki, "https://t/b", "그제 글", collected=NOW - timedelta(days=2))
    index = context(wiki, tmp_path).index
    local = NOW.astimezone(timezone(timedelta(hours=14))).isoformat()  # 다른 시간대로 적힌 줄
    with index.db:
        index.db.execute("UPDATE articles SET collected = ? WHERE id = ?", (local, aid))
    assert [a.title for a in views.saved_between(index, TODAY, TODAY)] == ["오늘 글"]
    two = TODAY - timedelta(days=2)
    assert sorted(a.title for a in views.saved_between(index, two, TODAY)) == ["그제 글", "오늘 글"]


def test_today_skips_lead_removed_by_another_device(wiki, tmp_path):
    add(wiki, "https://t/a", "사라질 글")
    c = client(wiki, tmp_path)
    for path in (wiki.root / "wiki").rglob("*.md"):
        path.unlink()
    assert "사라질 글" not in c.get("/today").text


def test_today_without_sources_config(tmp_path):
    wiki = Wiki(tmp_path / "wiki")
    add(wiki, "https://t/a", "설정 없는 위키")
    assert "설정 없는 위키" in client(wiki, tmp_path).get("/today").text


def test_decision_markdown_for_clipboard(wiki, tmp_path):
    add(wiki, "https://t/a", "글", topic="주제")
    stored = next(wiki.articles())
    text = views.decision_markdown(stored, "Toss")
    assert text.splitlines()[0] == "## 주제 (Toss)"
    assert "- 선택: SidecarSet" in text and "- 트레이드오프: 재시작 없음 ↔ 운영 부담" in text


# ---------- 보호 ----------


def test_token_becomes_cookie_and_is_removed_from_address(wiki, tmp_path):
    c = TestClient(create_app(context(wiki, tmp_path)), follow_redirects=False)

    assert c.get("/today").status_code == 403
    assert c.get("/today?token=wrong").status_code == 403
    first = c.get(f"/today?date=2026-11-08&token={TOKEN}")
    assert first.status_code == 303
    assert first.headers["location"] == "/today?date=2026-11-08"
    cookie = first.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert c.get("/today").status_code == 200
    assert c.get(f"/?token={TOKEN}").headers["location"] == "/"


def test_rejects_foreign_host_and_writes_without_htmx(wiki, tmp_path):
    c = client(wiki, tmp_path)
    assert c.get("/today", headers={"host": "evil.example"}).status_code == 403
    response = c.get("/today")
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert c.post("/feedback", data={"article_id": "x", "kind": "bookmark"}).status_code == 403


def test_home_placeholder_pages_and_unknown(wiki, tmp_path):
    c = client(wiki, tmp_path)
    assert c.get("/").headers["location"] == "/today"
    page = c.get("/settings").text  # 앱 밖에서는 설정을 열 수 없다
    assert "앱에서 열 수 있어요" in page and 'aria-current="page"' in page
    assert c.get("/nothing").status_code == 404
    assert c.get("/static/htmx.min.js").status_code == 200


def test_bookmark_and_not_relevant_go_to_feedback(wiki, tmp_path):
    aid = add(wiki, "https://t/a", "글")
    c = client(wiki, tmp_path)
    htmx = {"HX-Request": "true"}

    done = c.post("/feedback", data={"article_id": aid, "kind": "bookmark"}, headers=htmx)
    assert done.status_code == 200 and "북마크했어요" in done.text
    c.post("/feedback", data={"article_id": aid, "kind": "not_relevant"}, headers=htmx)
    assert [(f.kind, f.url, f.device) for f in load_feedback(wiki)] == [
        ("bookmark", "https://t/a", "air"),
        ("not_relevant", "https://t/a", "air"),
    ]
    for bad in (
        {"article_id": "nope", "kind": "bookmark"},
        {"article_id": aid, "kind": "x"},
        {"article_id": aid, "kind": "suggest_source"},
    ):
        assert c.post("/feedback", data=bad, headers=htmx).status_code == 400


def test_new_token_is_random():
    assert new_token() != new_token() and len(new_token()) >= 40


# ---------- serve ----------


def test_ready_line_and_bound_socket():
    sock = serve_module.bind()
    try:
        port = sock.getsockname()[1]
        assert sock.getsockname()[0] == "127.0.0.1" and port > 0
        line = json.loads(serve_module.ready_line(port, "abc"))
        assert line == {"port": port, "token": "abc", "url": f"http://127.0.0.1:{port}/?token=abc"}
    finally:
        sock.close()


def test_stops_when_stdin_closes():
    stopped, lines = [], []
    serve_module.watch_stdin(io.StringIO("ping\n\nsync_now\n"), lambda: stopped.append(True))
    serve_module.watch_stdin(io.StringIO("pause 1h\n\n"), lambda: None, lines.append)
    assert stopped == [True] and lines == ["pause 1h"]


def test_serve_app_mode_turns_on_scheduler_and_menu_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXT_HOLDER_HOME", str(tmp_path / "home"))
    wiki = Wiki(tmp_path / "wiki")
    seen = {}

    def fake_serve(make_context, out, stdin, on_ready, warm, on_line, background):
        seen.update(ctx=make_context(1), stdin=stdin, background=background)
        on_line("pause forever")

    monkeypatch.setattr(cli, "serve", fake_serve)
    monkeypatch.setattr(cli, "device_name", lambda home: "air")
    result = CliRunner().invoke(cli.app, ["serve", "--wiki", str(wiki.root), "--app"])

    assert result.exit_code == 0, result.output
    assert seen["ctx"].scheduler is not None and seen["stdin"] is not None
    assert seen["background"] is not None
    assert seen["ctx"].scheduler.paused_until() == "forever"
    assert (tmp_path / "home/logs/sync.log").exists()


def test_serve_command_wires_context_and_warm_up(tmp_path, monkeypatch):
    from tests.conftest import FakeEmbedder

    monkeypatch.setenv("CONTEXT_HOLDER_HOME", str(tmp_path / "home"))
    wiki = Wiki(tmp_path / "wiki")
    add(wiki, "https://t/a", "글")
    seen = {}

    def fake_serve(make_context, out, stdin, on_ready, warm, on_line, background):
        seen["ctx"] = make_context(4321)
        seen["stdin"] = stdin
        on_ready("http://x")
        warm()

    monkeypatch.setattr(cli, "serve", fake_serve)
    monkeypatch.setattr(cli, "device_name", lambda home: "air")
    monkeypatch.setattr(cli, "make_embedder", lambda model_id, local_only=False: FakeEmbedder())
    result = CliRunner().invoke(cli.app, ["serve", "--wiki", str(wiki.root), "--exit-on-stdin-eof"])

    assert result.exit_code == 0, result.output
    ctx = seen["ctx"]
    assert ctx.hosts == frozenset({"127.0.0.1:4321"}) and ctx.device == "air"
    assert seen["stdin"] is not None
    assert ctx.embedder() is ctx.embedder()  # 인덱스 맞추기와 화면 검색이 모델 하나를 나눠 쓴다
    assert Index(tmp_path / "home/index.db").select()[0].title == "글"  # 시작할 때 인덱스를 맞췄다


def test_warm_up_skips_index_without_local_model(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXT_HOLDER_HOME", str(tmp_path / "home"))
    wiki = Wiki(tmp_path / "wiki")
    add(wiki, "https://t/a", "글")

    def missing(model_id, local_only=False):
        raise RuntimeError("모델 없음")

    monkeypatch.setattr(cli, "make_embedder", missing)
    cli._warm_index(wiki)
    assert not (tmp_path / "home/index.db").exists()
