import json
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest
from typer.testing import CliRunner

from context_holder import cli
from context_holder.classify import Bucket, Decision
from context_holder.collect import Article
from context_holder.feedback import load_feedback
from context_holder.index import Index
from context_holder.mcpserver import build_server
from context_holder.mcptools import JUDGE_NOTE, NO_MODEL_NOTE, WikiTools, _fit
from context_holder.wiki import Wiki, article_id
from tests.conftest import FakeEmbedder
from tests.test_index import write

NOW = datetime(2026, 11, 9, tzinfo=UTC)
GPU = {"그래": "mig", "래픽": "mig", "픽카": "mig", "카드": "mig"}  # 다른 말로 물어도 같은 뜻


@pytest.fixture
def wiki(tmp_path) -> Wiki:
    wiki = Wiki(tmp_path / "wiki")
    wiki.ensure_config()
    return wiki


def make_tools(wiki: Wiki, tmp_path: Path, embedder=None, **kw) -> WikiTools:
    embedder = embedder if embedder is not None else FakeEmbedder(synonyms=GPU)
    index = Index(tmp_path / "index.db")
    index.sync(wiki, embedder)
    return WikiTools(
        index=index,
        wiki=wiki,
        embedder=kw.get("factory", lambda: embedder),
        device="air",
        log_path=tmp_path / "mcp-queries.jsonl",
        clock=lambda: NOW,
    )


def log(tmp_path: Path) -> list[dict]:
    lines = (tmp_path / "mcp-queries.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


# ---------- list_topics ----------


def test_list_topics_groups_case_and_spacing(wiki, tmp_path):
    write(wiki, "https://t/1", "글 1", ("a",), topic="캐시 무효화")
    write(wiki, "https://t/2", "글 2", ("b",), topic="캐시  무효화 ")
    write(wiki, "https://t/3", "글 3", ("c",), topic="Outbox Pattern")
    write(wiki, "https://t/4", "글 4", ("d",))  # 결정 기록 없음
    tools = make_tools(wiki, tmp_path)

    text = tools.list_topics()
    assert "결정 주제 2개" in text
    assert text.index("캐시 무효화 (2)") < text.index("Outbox Pattern (1)")
    assert tools.list_topics(prefix="outbox") == "결정 주제 1개 (사례 수)\n- Outbox Pattern (1)"
    assert tools.list_topics(prefix="없는") == "결정 주제가 없다."


# ---------- search_wiki ----------


def test_search_merges_queries_and_shows_short_entries(wiki, tmp_path):
    outbox = write(wiki, "https://t/o", "Outbox 도입기", ("CDC 릴레이",), topic="이벤트 발행")
    cache = write(wiki, "https://t/c", "캐시 전략", ("이벤트 기반 만료",), bucket=Bucket.INBOX)
    tools = make_tools(wiki, tmp_path)

    text = tools.search_wiki(["outbox", "이벤트 기반 만료"])

    assert f"[{outbox}] Outbox 도입기" in text
    assert f"[{cache}] 캐시 전략 (보류)" in text
    assert "Toss · 2026-11-06" in text
    assert "결정 주제: 이벤트 발행" in text
    assert "요약 한 줄" in text and "https://t/o" in text
    assert "get_article(id)" in text
    assert log(tmp_path)[-1]["ids"][:2] in ([outbox, cache], [cache, outbox])


def test_search_filters_company_and_date(wiki, tmp_path):
    write(wiki, "https://t/o", "Outbox 도입기", ("CDC 릴레이",))
    tools = make_tools(wiki, tmp_path)

    assert "Outbox" in tools.search_wiki(["outbox"], company="Toss")
    assert "Outbox" in tools.search_wiki(["outbox"], company="toss")
    assert "찾지 못했다" in tools.search_wiki(["outbox"], company="Netflix")
    assert "Outbox" in tools.search_wiki(["outbox"], since="2026-11-01")
    assert "찾지 못했다" in tools.search_wiki(["outbox"], since="2026-12-01")


def test_search_leaves_relevance_judgement_to_the_caller(wiki, tmp_path):
    write(wiki, "https://t/g", "GPU-native 클러스터", ("MIG로 인스턴스 분할",), topic="GPU 공유")
    tools = make_tools(wiki, tmp_path)

    text = tools.search_wiki(["그래픽카드 쪼개기"])

    assert "GPU-native 클러스터" in text
    assert text.endswith(JUDGE_NOTE)
    assert tools.find_decision_cases(topic="GPU 공유").endswith(JUDGE_NOTE)


def test_search_without_model_uses_keywords(wiki, tmp_path):
    write(wiki, "https://t/o", "Outbox 도입기", ("CDC 릴레이",), topic="이벤트 발행")

    def missing_model():
        raise RuntimeError("모델 파일 없음")

    tools = make_tools(wiki, tmp_path, factory=missing_model)
    text = tools.search_wiki(["outbox"])
    assert text.startswith(NO_MODEL_NOTE)
    assert "Outbox 도입기" in text

    none_tools = make_tools(wiki, tmp_path, factory=lambda: None)
    assert NO_MODEL_NOTE in none_tools.search_wiki(["전혀 없는 말"])


def test_empty_search_lists_topics_instead_of_guessing(wiki, tmp_path):
    write(wiki, "https://t/o", "Outbox 도입기", ("CDC 릴레이",), topic="이벤트 발행")
    tools = make_tools(wiki, tmp_path, factory=lambda: None)

    text = tools.search_wiki(["  ", "쿠버네티스 오토스케일"])
    assert "위키에서 맞는 글을 찾지 못했다." in text
    assert "있는 결정 주제 예: 이벤트 발행" in text

    empty = make_tools(Wiki(tmp_path / "empty"), tmp_path / "e", factory=lambda: None)
    assert "있는 결정 주제 예: 없음" in empty.search_wiki(["x"])


def test_search_respects_limit(wiki, tmp_path):
    for n in range(4):
        write(wiki, f"https://t/{n}", f"Outbox 사례 {n}", ("CDC",))
    text = make_tools(wiki, tmp_path).search_wiki(["outbox"], limit=2)
    assert text.count("Outbox 사례") == 2


# ---------- find_decision_cases ----------


def test_decision_cases_put_exact_topic_first(wiki, tmp_path):
    write(wiki, "https://t/a", "캐시 이야기", ("캐시 무효화 이벤트",), topic="데이터 동기화")
    exact = write(wiki, "https://t/b", "만료 정책", ("TTL",), topic="캐시 무효화")
    write(wiki, "https://t/c", "캐시 무효화 회고", ("캐시 무효화",))  # 결정 기록 없음
    tools = make_tools(wiki, tmp_path)

    text = tools.find_decision_cases(topic="캐시 무효화")

    first = text.splitlines()[0]
    assert first == f"- [{exact}] 캐시 무효화 — 만료 정책"
    assert "선택지: A / B" in text and "선택: B" in text and "빠름 ↔ 복잡" in text
    assert "신뢰도 high" in text
    assert "캐시 무효화 회고" not in text
    assert "get_decision(id)" in text


def test_decision_cases_by_problem_and_missing_input(wiki, tmp_path):
    write(wiki, "https://t/a", "Outbox 도입기", ("CDC 릴레이",), topic="이벤트 발행")
    tools = make_tools(wiki, tmp_path)
    assert "Outbox 도입기" in tools.find_decision_cases(problem="outbox 이벤트 유실")
    assert tools.find_decision_cases() == "topic이나 problem을 준다."
    assert "찾지 못했다" in make_tools(wiki, tmp_path, factory=lambda: None).find_decision_cases(
        topic="양자 컴퓨팅"
    )


# ---------- get_decision · get_article ----------


def test_get_decision_shows_evidence(wiki, tmp_path):
    from context_holder.enrich import Confidence, DecisionRecord, Enrichment

    url = "https://t/e"
    record = DecisionRecord(
        "이벤트 발행",
        "커밋 후 발행이 빠진다",
        ("Outbox", "CDC"),
        "Outbox",
        ("단순함 ↔ 지연",),
        "유실 0건",
        ("커밋과 함께 기록했다.",),
        ("하루 1억 건",),
        Confidence.MEDIUM,
    )
    wiki.write_article(
        Article("toss", url, "Outbox 도입기", NOW, "본문", "h"),
        Decision(url, 0.9, Bucket.SAVE, "백엔드", (), ()),
        Enrichment(("요약",), ("핵심",), ("outbox",), record, None, 0.0),
        NOW,
    )
    tools = make_tools(wiki, tmp_path)

    text = tools.get_decision(article_id(url))
    for part in ("주제: 이벤트 발행", "- CDC", "선택: Outbox", "결과: 유실 0건", "신뢰도: medium"):
        assert part in text
    assert "- 커밋과 함께 기록했다." in text
    assert "원문에서 확인하지 못한 근거:\n- 하루 1억 건" in text


def test_get_decision_missing(wiki, tmp_path):
    plain = write(wiki, "https://t/p", "그냥 글", ("a",))
    tools = make_tools(wiki, tmp_path)
    assert tools.get_decision("nope") == "nope에는 결정 기록이 없다."
    assert "결정 기록이 없다" in tools.get_decision(plain)


def test_get_article_sections(wiki, tmp_path):
    aid = write(
        wiki, "https://t/a", "Outbox 도입기", ("CDC 릴레이",), topic="이벤트", tags=("cdc",)
    )
    plain = write(wiki, "https://t/p", "그냥 글", ("a",))
    tools = make_tools(wiki, tmp_path)

    full = tools.get_article(aid)
    assert "## 요약\n- 요약 한 줄" in full and "## 핵심 내용\n- CDC 릴레이" in full
    assert "태그: cdc" in full and "get_decision(id)" in full
    summary = tools.get_article(aid, section="요약")
    assert "핵심 내용" not in summary
    assert "선택: B" in tools.get_article(aid, section="결정 기록")
    assert "결정 기록이 없다." in tools.get_article(plain, section="결정 기록")
    assert "section은" in tools.get_article(aid, section="원문")
    assert tools.get_article("nope") == "nope 글이 없다."


def test_get_article_of_pending(wiki, tmp_path):
    url = "https://t/pending"
    wiki.write_article(
        Article("toss", url, "요약 대기 글", None, "본문", "h"),
        Decision(url, 0.9, Bucket.SAVE, "백엔드", (), ()),
        None,
        NOW,
        reason="limit",
    )
    text = make_tools(wiki, tmp_path).get_article(article_id(url))
    assert "날짜 모름" in text
    assert "아직 요약하지 못한 글이다" in text


def test_article_removed_by_another_device(wiki, tmp_path):
    aid = write(wiki, "https://t/a", "Outbox 도입기", ("CDC",), topic="이벤트")
    tools = make_tools(wiki, tmp_path)
    for path in (wiki.root / "wiki").rglob("*.md"):
        path.unlink()  # pull로 지워졌지만 인덱스는 아직 모른다
    assert tools.get_article(aid) == f"{aid} 글이 없다."
    assert "Outbox 도입기" in tools.search_wiki(["outbox"])  # 목록은 인덱스만으로 보여 준다
    assert "[" not in tools.find_decision_cases(topic="이벤트").splitlines()[0]


# ---------- suggest_source · 로그 · 예산 ----------


def test_suggest_source_goes_to_this_device_feedback(wiki, tmp_path):
    tools = make_tools(wiki, tmp_path)

    assert "남겼다" in tools.suggest_source("https://eng.example.com/post", "CDC 비교가 자세하다")
    assert tools.suggest_source("ftp://x", "?") == "http(s) 주소를 준다."

    [entry] = load_feedback(wiki)
    assert (entry.kind, entry.device, entry.note) == (
        "suggest_source",
        "air",
        "CDC 비교가 자세하다",
    )


def test_every_call_is_logged_with_time(wiki, tmp_path):
    write(wiki, "https://t/o", "Outbox 도입기", ("CDC",))
    tools = make_tools(wiki, tmp_path)

    tools.call("search_wiki", queries=["outbox"])
    tools.list_topics()

    first, second = log(tmp_path)
    assert first["tool"] == "search_wiki" and first["args"]["queries"] == ["outbox"]
    assert isinstance(first["ms"], int) and first["chars"] > 0
    assert second["tool"] == "list_topics"


def test_fit_drops_tail_beyond_budget():
    lines = ["가" * 900, "나" * 900, "다" * 900]
    text = _fit(lines, "list_topics")
    assert "나" * 900 not in text
    assert "길이 제한으로 2개 항목을 뺐다" in text


def test_missing_source_config(tmp_path):
    tools = make_tools(Wiki(tmp_path / "bare"), tmp_path, factory=lambda: None)
    assert tools._company("toss") == "toss"


# ---------- 인덱스 갱신 · 서버 ----------


def test_long_running_server_sees_reindex_by_engine(wiki, tmp_path):
    write(wiki, "https://t/o", "Outbox 도입기", ("CDC",))
    embedder = FakeEmbedder(synonyms=GPU)
    tools = make_tools(wiki, tmp_path, embedder)
    assert "GPU" not in tools.search_wiki(["그래픽카드"])

    write(wiki, "https://t/g", "GPU-native 클러스터", ("MIG로 인스턴스 분할",))
    Index(tmp_path / "index.db").sync(wiki, embedder)  # 엔진(다른 연결)이 갱신했다

    assert "GPU-native 클러스터" in tools.search_wiki(["그래픽카드"])


def test_server_exposes_six_text_tools(wiki, tmp_path):
    write(wiki, "https://t/o", "Outbox 도입기", ("CDC",), topic="이벤트 발행")
    server = build_server(make_tools(wiki, tmp_path))

    async def run():
        tools = await server.list_tools()
        calls = {
            "find_decision_cases": {"topic": "이벤트 발행"},
            "search_wiki": {"queries": ["outbox"]},
            "list_topics": {},
            "get_decision": {"id": article_id("https://t/o")},
            "get_article": {"id": article_id("https://t/o")},
            "suggest_source": {"url": "https://x.example/a", "reason": "좋다"},
        }
        results = {name: await server.call_tool(name, args) for name, args in calls.items()}
        return tools, results

    tools, results = anyio.run(run)
    assert sorted(t.name for t in tools) == sorted(results)
    assert "기술 선택" in next(t for t in tools if t.name == "find_decision_cases").description
    for result in results.values():
        assert not result.is_error
        assert result.structured_content is None  # 같은 내용을 두 번 보내지 않는다
    assert "Outbox 도입기" in results["search_wiki"].content[0].text


def test_ctx_mcp_starts_stdio_server(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXT_HOLDER_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "device_name", lambda home: "air")
    started = {}

    class FakeServer:
        def run(self, transport):
            started["transport"] = transport

    def fake_build(tools):
        started["model"] = tools.embedder().model_id
        return FakeServer()

    monkeypatch.setattr(cli, "build_server", fake_build)
    result = CliRunner().invoke(cli.app, ["mcp", "--wiki", str(tmp_path / "wiki")])

    assert result.exit_code == 0, result.output
    assert started == {"transport": "stdio", "model": "bge-m3"}
    assert result.output == ""  # stdout은 MCP 프로토콜 전용


def test_wiki_read_of_missing_file(tmp_path):
    assert Wiki(tmp_path).read(tmp_path / "nope.md") is None
