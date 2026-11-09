"""Decisions 화면: 주제 목록 · 비교표 · 주제 정리 버튼, MCP의 넓은 주제."""

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from context_holder import topics
from context_holder.index import Index
from context_holder.llm import ErrorKind, LlmError
from context_holder.screens.app import COOKIE, create_app
from context_holder.screens.decisionsview import RegroupJob, build_decisions
from context_holder.topics import Group, TopicMap
from context_holder.wiki import Wiki
from tests.test_classify import FakeLlm
from tests.test_screens import NOW, SOURCES, TOKEN, add, context
from tests.test_setup import HTMX

NAMES = {"toss": "Toss", "d2": "NAVER D2"}


def make_wiki(tmp_path: Path) -> Wiki:
    wiki = Wiki(tmp_path / "wiki")
    wiki.ensure_config()
    (wiki.root / ".git").mkdir()
    (wiki.root / "config/sources.yaml").write_text(SOURCES, encoding="utf-8")
    return wiki


def seed(wiki: Wiki) -> None:
    add(wiki, "https://t/1", "사이드카 버전 통일", source="toss", topic="사이드카 버전 관리")
    add(
        wiki,
        "https://t/2",
        "메시 도입기",
        source="d2",
        topic="서비스 메시 도입",
        published=NOW - timedelta(days=40),
    )
    add(wiki, "https://t/3", "혼자인 사례", source="d2", topic="모노레포 도구")
    add(wiki, "https://t/4", "아직 분류 전", source="d2", topic="LLM 평가")
    topics.save(
        wiki,
        TopicMap(
            (
                Group(
                    "서비스 메시 운영",
                    "인프라",
                    "사이드카를 어떻게 운영할까",
                    ("사이드카 버전 관리", "서비스 메시 도입"),
                ),
                Group("모노레포 운영", "개발 생산성", "", ("모노레포 도구",)),
                Group("이상한 분야", "우주", "", ("없는 주제",)),
            )
        ),
    )


def index_of(wiki: Wiki, tmp_path: Path) -> Index:
    index = Index(tmp_path / "index.db")
    index.sync(wiki)
    return index


def test_view_groups_by_area_and_compares_companies(tmp_path):
    wiki = make_wiki(tmp_path)
    seed(wiki)
    view = build_decisions(wiki, index_of(wiki, tmp_path), NAMES)
    assert (view.group_total, view.case_total, view.unassigned, view.hidden) == (2, 4, 1, 1)
    assert [(area, [t.name for t in items]) for area, items in view.areas] == [
        ("인프라", ["서비스 메시 운영"])
    ]
    item = view.areas[0][1][0]
    assert (item.count, item.companies, item.on) == (2, 2, True)
    assert view.selected.name == "서비스 메시 운영"
    first, second = view.columns
    assert first.company == "Toss" and second.company == "NAVER D2"  # 최근 글이 먼저
    assert first.chosen == "SidecarSet" and first.alternatives == ("직접 관리",)
    assert first.confidence == 3 and first.tradeoffs == ("재시작 없음 ↔ 운영 부담",)
    assert "| Toss (" in view.markdown and "사이드카를 어떻게 운영할까" in view.markdown
    assert view.url(sel="모노레포 운영").startswith("/decisions?sel=")


def test_view_show_all_search_and_select(tmp_path):
    wiki = make_wiki(tmp_path)
    seed(wiki)
    index = index_of(wiki, tmp_path)
    everything = build_decisions(wiki, index, NAMES, show_all=True)
    assert [a for a, _ in everything.areas] == ["인프라", "개발 생산성"]
    picked = build_decisions(wiki, index, NAMES, sel="모노레포 운영")
    assert picked.selected.name == "모노레포 운영" and len(picked.columns) == 1
    assert picked.markdown.startswith("## 모노레포 운영\n\n|")
    assert [a for a, _ in picked.areas] == ["인프라", "개발 생산성"]  # 고른 주제는 보인다
    none = build_decisions(wiki, index, NAMES, q="없는 말")
    assert none.areas == [] and none.selected is None and none.markdown == ""
    assert (
        build_decisions(wiki, index, NAMES, sel="모르는 주제").selected.name == "서비스 메시 운영"
    )
    assert "all=1" in everything.url()


def test_view_skips_cases_gone_from_wiki_and_other_area(tmp_path):
    wiki = make_wiki(tmp_path)
    seed(wiki)
    index = index_of(wiki, tmp_path)
    topics.save(wiki, TopicMap((Group("모노레포 운영", "", "", ("모노레포 도구",)),)))
    for path in (wiki.root / "wiki").rglob("*.md"):
        path.unlink()
    view = build_decisions(wiki, index, NAMES, show_all=True)
    assert view.areas[0][0] == "기타" and view.columns == []


def test_regroup_job_runs_once_and_reports(tmp_path):
    wiki = make_wiki(tmp_path)
    seed(wiki)
    ran = []
    job = RegroupJob(
        wiki,
        lambda: FakeLlm(
            [
                {
                    "groups": [
                        {"name": "AI 평가", "area": "AI 엔지니어링", "question": "", "cases": [1]}
                    ]
                }
            ]
        ),
        background=ran.append,
    )
    assert not job.start(busy=True) and job.state == "error" and "수집이 끝난 뒤" in job.message
    assert job.start(busy=False) and job.state == "running"
    assert not job.start(busy=False)  # 이미 돌고 있다
    ran[0]()
    assert job.state == "done" and "사례 1건을 주제 4개로" in job.message


def test_regroup_job_explains_claude_problems(tmp_path):
    wiki = make_wiki(tmp_path)
    add(wiki, "https://t/1", "글", topic="캐시")
    for kind, text in ((ErrorKind.AUTH, "로그인이 필요해요"), (ErrorKind.FAILED, "다시 시도해")):
        job = RegroupJob(wiki, lambda k=kind: FakeLlm([LlmError(k, "x")]), background=lambda w: w())
        job.start(busy=False)
        assert job.state == "error" and text in job.message
    job = RegroupJob(wiki, lambda: FakeLlm([{"groups": []}]), background=lambda w: w())
    job.start(busy=False)
    assert "1건은 다음에 다시" in job.message


# ---------- 화면 ----------


def client(tmp_path: Path, wiki: Wiki, llm) -> TestClient:
    from dataclasses import replace

    ctx = replace(context(wiki, tmp_path), regroup_llm=lambda: llm)
    c = TestClient(create_app(ctx), follow_redirects=False)
    c.cookies.set(COOKIE, TOKEN)
    return c


def test_decisions_page_and_partials(tmp_path):
    wiki = make_wiki(tmp_path)
    seed(wiki)
    c = client(tmp_path, wiki, FakeLlm([]))
    page = c.get("/decisions").text
    assert "서비스 메시 운영" in page and "비교표 복사" in page and 'aria-current="page"' in page
    assert "사례가 1건인 주제 1개 더 보기" in page and "아직 분류하지 않은 사례 1건" in page
    assert 'hx-get="/article/' in page and "SidecarSet" in page
    assert page.count("data-article=") == 2 * 7  # 사례 2건, 머리줄 + 6줄: 열 어디를 눌러도 상세
    assert 'class="col-open" aria-label="Toss 글 상세 열기"' in page
    assert (
        'id="decisions-loading"' in page and 'id="decisions-failed"' in page
    )  # 주제를 바꾸는 동안
    body = c.get("/decisions?all=1", headers={**HTMX, "HX-Target": "decisions-body"}).text
    assert "<html" not in body and "모노레포 운영" in body and "비교할 수 있는 주제만 보기" in body
    assert "맞는 주제가 없어요" in c.get("/decisions?q=zzz").text


def test_search_start_links_broad_topics_by_area(tmp_path):
    wiki = make_wiki(tmp_path)
    seed(wiki)
    page = client(tmp_path, wiki, FakeLlm([])).get("/search").text
    assert "주제별로 결정 비교하기" in page and "<dt>인프라</dt>" in page
    assert 'href="/decisions?sel=%EC%84%9C%EB%B9%84%EC%8A%A4' in page  # 서비스 메시 운영 비교표


def test_decisions_empty_before_first_regroup_then_runs(tmp_path):
    wiki = make_wiki(tmp_path)
    add(wiki, "https://t/1", "글", topic="캐시 무효화")
    llm = FakeLlm(
        [{"groups": [{"name": "캐시 전략", "area": "백엔드", "question": "", "cases": [1]}]}]
    )
    c = client(tmp_path, wiki, llm)
    page = c.get("/decisions").text
    assert "주제를 아직 정리하지 않았어요" in page and "원문은 보내지 않아요" in page
    started = c.post("/decisions/regroup", headers=HTMX).text
    assert "주제를" in started
    for _ in range(100):  # 백그라운드 스레드가 끝나기를 기다린다
        poll = c.get("/decisions/regroup")
        if poll.headers.get("HX-Refresh"):
            break
    assert poll.headers["HX-Refresh"] == "true" and "정리했어요" in poll.text
    assert "캐시 전략" in c.get("/decisions").text
    assert "HX-Refresh" not in c.get("/decisions/regroup").headers


# ---------- MCP ----------


def test_mcp_lists_and_finds_by_broad_topic(tmp_path):
    from tests.test_mcp import make_tools

    wiki = make_wiki(tmp_path)
    seed(wiki)
    tools = make_tools(wiki, tmp_path)
    text = tools.list_topics()
    assert "결정 주제 2개 (사례 수)\n- 서비스 메시 운영 (2)\n" in text
    assert "아직 묶지 않은 주제 1개" in text and "- LLM 평가 (1)" in text
    assert "주제 이름을 주면" in text
    filtered = tools.list_topics(prefix="사이드카")
    assert "서비스 메시 운영" in filtered and "LLM 평가" not in filtered
    assert "LLM 평가" in tools.list_topics(prefix="llm")
    hits, _ = tools.decision_hits(topic="서비스 메시 운영", limit=5)
    assert {h.article.decision_topic for h in hits[:2]} == {
        "사이드카 버전 관리",
        "서비스 메시 도입",
    }
