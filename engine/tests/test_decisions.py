"""Decisions 화면: 주제 목록 · 비교표 · 주제 편집 · 사례 옮기기, MCP의 넓은 주제."""

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from context_holder import topics
from context_holder.index import Index
from context_holder.screens.app import COOKIE, create_app
from context_holder.screens.decisionsview import (
    TopicItem,
    build_decisions,
    decisions_more,
)
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
    assert (view.group_total, view.case_total, view.unassigned) == (2, 4, 1)
    assert view.areas == ["인프라", "개발 생산성"] and view.area == "인프라"  # 첫 분야를 고른다
    assert view.topics == [TopicItem("서비스 메시 운영", True)] and view.after is None
    assert view.selected.name == "서비스 메시 운영"
    first, second = view.columns
    assert first.company == "Toss" and second.company == "NAVER D2"  # 최근 글이 먼저
    assert first.chosen == "SidecarSet" and first.alternatives == ("직접 관리",)
    assert first.confidence == 3 and first.tradeoffs == ("재시작 없음 ↔ 운영 부담",)
    assert "| Toss (" in view.markdown and "사이드카를 어떻게 운영할까" in view.markdown
    assert view.url(sel="모노레포 운영").startswith("/decisions?area=")


def test_view_area_tab_search_and_select(tmp_path):
    wiki = make_wiki(tmp_path)
    seed(wiki)
    index = index_of(wiki, tmp_path)
    tab = build_decisions(wiki, index, NAMES, area="개발 생산성")
    assert [t.name for t in tab.topics] == [
        "모노레포 운영"
    ] and tab.selected.name == "모노레포 운영"
    picked = build_decisions(wiki, index, NAMES, sel="모노레포 운영")
    assert picked.area == "개발 생산성"  # 분야가 없으면 고른 주제의 분야
    assert picked.selected.name == "모노레포 운영" and len(picked.columns) == 1
    assert picked.markdown.startswith("## 모노레포 운영\n\n|")
    other = build_decisions(wiki, index, NAMES, sel="모노레포 운영", area="인프라")
    assert other.selected.name == "서비스 메시 운영"  # 고른 분야에 없는 주제는 놓는다
    found = build_decisions(wiki, index, NAMES, q="운영", area="인프라")
    assert found.area == "" and [t.name for t in found.topics] == [
        "서비스 메시 운영",
        "모노레포 운영",
    ]
    none = build_decisions(wiki, index, NAMES, q="없는 말")
    assert none.topics == [] and none.selected is None and none.markdown == ""
    empty = build_decisions(wiki, index, NAMES, sel="이상한 분야")  # 사례가 없는 주제
    assert empty.area == "인프라" and empty.selected.name == "서비스 메시 운영"
    unknown = build_decisions(wiki, index, NAMES, sel="모르는 주제", area="우주")
    assert unknown.area == "인프라" and unknown.selected.name == "서비스 메시 운영"


def test_view_pages_topics_by_cursor(tmp_path):
    wiki = make_wiki(tmp_path)
    groups = []
    for n in range(23):  # 사례 수가 다르면 많은 순, 같으면 이름 순
        for k in range(2 if n < 3 else 1):
            add(wiki, f"https://t/{n}-{k}", f"글 {n}", topic=f"원래 {n}")
        groups.append(Group(f"주제 {n:02d}", "백엔드", "", (f"원래 {n}",)))
    topics.save(wiki, TopicMap(tuple(groups)))
    index = index_of(wiki, tmp_path)

    view = build_decisions(wiki, index, NAMES)
    assert [t.name for t in view.topics] == [f"주제 {n:02d}" for n in range(10)]
    assert view.after == "주제 09"
    assert view.more_url("주제 09") == (
        "/decisions/more?area=%EB%B0%B1%EC%97%94%EB%93%9C"
        "&sel=%EC%A3%BC%EC%A0%9C+00&after=%EC%A3%BC%EC%A0%9C+09"
    )
    deep = build_decisions(wiki, index, NAMES, sel="주제 14")  # 고른 주제까지 보인다
    assert len(deep.topics) == 15 and deep.topics[-1].on and deep.after == "주제 14"

    page, more = decisions_more(wiki, index, "백엔드", "", "주제 12", "주제 09")
    assert [t.name for t in page.items] == [f"주제 {n:02d}" for n in range(10, 20)]
    assert [t.on for t in page.items].index(True) == 2 and page.after == "주제 19"
    assert more.more_url("주제 19").startswith("/decisions/more?area=")
    last, _ = decisions_more(wiki, index, "백엔드", "", "", "주제 19")
    assert len(last.items) == 3 and last.after is None
    searched, view_q = decisions_more(wiki, index, "백엔드", "주제", "", "주제 09")
    assert len(searched.items) == 10 and view_q.area == ""
    gone, _ = decisions_more(wiki, index, "백엔드", "", "", "없는 주제")
    assert gone.items == [] and gone.after is None


def test_view_skips_cases_gone_from_wiki_and_other_area(tmp_path):
    wiki = make_wiki(tmp_path)
    seed(wiki)
    index = index_of(wiki, tmp_path)
    topics.save(wiki, TopicMap((Group("모노레포 운영", "", "", ("모노레포 도구",)),)))
    for path in (wiki.root / "wiki").rglob("*.md"):
        path.unlink()
    view = build_decisions(wiki, index, NAMES)
    assert view.areas == ["기타"] and view.columns == []


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
    assert "주제에 묶지 못한 사례 1건 · 다음 수집 때 다시 묶어요" in page and "더 보기" not in page
    assert '<a class="chip on" href="/decisions?area=' in page  # 분야 탭
    assert 'hx-get="/article/' in page and "SidecarSet" in page
    assert page.count("data-article=") == 2 * 7  # 사례 2건, 머리줄 + 6줄: 열 어디를 눌러도 상세
    assert 'class="col-open" aria-label="Toss 글 상세 열기"' in page
    assert (
        'id="decisions-loading"' in page and 'id="decisions-failed"' in page
    )  # 주제를 바꾸는 동안
    body = c.get(
        "/decisions?area=개발 생산성", headers={**HTMX, "HX-Target": "decisions-body"}
    ).text
    assert "<html" not in body and '<span class="name">모노레포 운영</span>' in body
    assert '<span class="name">서비스 메시 운영</span>' not in body
    assert "맞는 주제가 없어요" in c.get("/decisions?q=zzz").text
    assert "찾은 주제: 운영" in c.get("/decisions?q=운영").text
    more = c.get("/decisions/more?area=인프라&after=서비스 메시 운영").text
    assert '<div hx-swap-oob="beforeend:#topic-list">' in more and "topic-list-more" not in more


def test_search_start_links_broad_topics_by_area(tmp_path):
    wiki = make_wiki(tmp_path)
    seed(wiki)
    page = client(tmp_path, wiki, FakeLlm([])).get("/search").text
    assert "주제별로 결정 비교하기" in page and "<dt>인프라</dt>" in page
    assert 'href="/decisions?sel=%EC%84%9C%EB%B9%84%EC%8A%A4' in page  # 서비스 메시 운영 비교표


def test_decisions_empty_says_the_next_sync_groups(tmp_path):
    wiki = make_wiki(tmp_path)
    add(wiki, "https://t/1", "글", topic="캐시 무효화")
    page = client(tmp_path, wiki, FakeLlm([])).get("/decisions").text
    assert "주제를 아직 묶지 않았어요" in page and "다음 수집이 끝나면 자동으로 묶여요" in page
    assert "원문은 보내지 않아요" in page and "/decisions/regroup" not in page


def test_decisions_head_edits_in_place_and_moves_last(tmp_path):
    wiki = make_wiki(tmp_path)
    seed(wiki)
    page = client(tmp_path, wiki, FakeLlm([])).get("/decisions").text
    head = page[page.index('class="topic-inline"') : page.index('class="head-actions"')]
    assert 'hx-post="/decisions/edit"' in head
    assert 'hx-trigger="change"' in head  # 칸을 벗어나면 저장
    assert "<option selected>인프라</option>" in head
    assert 'name="name" rows="1" maxlength="40"' in head and ">서비스 메시 운영</textarea>" in head
    assert ">사이드카를 어떻게 운영할까</textarea>" in head
    actions = page[page.index('class="head-actions"') : page.index('class="scroll-bar"')]
    assert "주제 편집" not in actions and "saved-note" not in actions  # 편집 모드 없이 바로 고친다
    assert actions.index('aria-label="비교표 복사"') < actions.index("사례 옮기기")


def test_decisions_edit_renames_says_what_changed_and_undoes(tmp_path):
    import json

    wiki = make_wiki(tmp_path)
    seed(wiki)
    c = client(tmp_path, wiki, FakeLlm([]))
    form = {"sel": "서비스 메시 운영", "name": "모노레포 운영", "area": "인프라", "question": ""}
    taken = c.post("/decisions/edit", data=form, headers=HTMX)
    assert taken.status_code == 422 and "이미 있는 주제" in taken.text
    renamed = c.post(
        "/decisions/edit",
        data={**form, "name": "서비스 메시", "question": "사이드카를 어떻게 운영할까"},
        headers=HTMX,
    )
    assert renamed.status_code == 200 and "<html" not in renamed.text
    assert renamed.headers["HX-Push-Url"].startswith("/decisions?area=")
    assert ">서비스 메시</textarea>" in renamed.text and "이름을 바꿨어요" in renamed.text
    group = topics.load(wiki).group("서비스 메시")
    assert group.area == "인프라" and group.topics == ("사이드카 버전 관리", "서비스 메시 도입")

    vals = renamed.text.split("hx-vals='")[1].split("'")[0]
    undo = c.post("/decisions/edit", data=json.loads(vals), headers=HTMX)
    assert "되돌렸어요" in undo.text and "hx-vals=" not in undo.text
    assert topics.load(wiki).group("서비스 메시 운영") is not None


def test_edit_note_names_the_change():
    from context_holder.screens.decisionsview import edit_note

    before = Group("캐시", "백엔드", "어디에 둘까", ("a",))
    assert edit_note(before, before, False) is None  # 바꾼 게 없으면 알리지 않는다
    area = edit_note(before, Group("캐시", "인프라", "어디에 둘까"), False)
    assert area.message == "분야를 바꿨어요"
    assert edit_note(before, Group("캐시", "백엔드", "뭘 쓸까"), False).message == "질문을 바꿨어요"
    both = edit_note(before, Group("캐시 전략", "인프라", "어디에 둘까"), False)
    assert both.message == "주제를 바꿨어요"
    assert both.undo == {
        "sel": "캐시 전략",
        "name": "캐시",
        "area": "백엔드",
        "question": "어디에 둘까",
        "undo": "true",
    }


def test_decisions_move_window_moves_a_case(tmp_path):
    wiki = make_wiki(tmp_path)
    seed(wiki)
    c = client(tmp_path, wiki, FakeLlm([]))
    window = c.get("/decisions/move?sel=서비스 메시 운영", headers=HTMX).text
    assert 'id="move-title">사례 옮기기' in window
    assert 'value="사이드카 버전 관리"' in window and 'value="서비스 메시 도입"' in window
    assert "data-topic-find" in window and '<select name="to"' not in window  # 찾아서 고른다
    assert 'data-find="모노레포 운영  개발 생산성"' in window and "개발 생산성 · 1건" in window
    assert 'name="to" value="서비스 메시 운영"' not in window  # 지금 주제로는 옮기지 않는다
    assert c.get("/decisions/move?sel=없는 주제").status_code == 404

    base = {"sel": "서비스 메시 운영", "topic": "사이드카 버전 관리"}
    none = c.post("/decisions/move", data={"sel": "서비스 메시 운영"}, headers=HTMX)
    assert none.status_code == 422 and "옮길 사례를 골라" in none.text
    moved = c.post("/decisions/move", data={**base, "to": "모노레포 운영"}, headers=HTMX)
    assert moved.headers["HX-Retarget"] == "#decisions-body"
    assert moved.headers["HX-Trigger"] == "topic-moved"
    assert ">서비스 메시 운영</textarea>" in moved.text  # 사례가 남아 그대로 본다
    assert topics.load(wiki).group_of("사이드카 버전 관리") == "모노레포 운영"

    new = {"sel": "서비스 메시 운영", "topic": "서비스 메시 도입", "mode": "new"}
    bad = c.post(
        "/decisions/move", data={**new, "name": "모노레포 운영", "area": "인프라"}, headers=HTMX
    )
    assert bad.status_code == 422 and "이미 있는 주제" in bad.text
    made = c.post(
        "/decisions/move",
        data={**new, "name": "메시 도입", "area": "인프라", "question": "메시를 들일까"},
        headers=HTMX,
    )
    # 마지막 사례가 빠진 주제는 사라지고 옮긴 주제를 연다
    assert ">메시 도입</textarea>" in made.text
    assert topics.load(wiki).group("서비스 메시 운영") is None


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
