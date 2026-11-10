"""검색창 추천: 결정 주제 문장(넓은 주제 · 질문 · 사례 주제)을 글자와 뜻으로 추천한다."""

from pathlib import Path

from fastapi.testclient import TestClient

from context_holder.index import Index
from context_holder.screens.app import COOKIE, create_app
from context_holder.screens.suggestions import Suggester
from context_holder.wiki import Wiki
from tests.conftest import FakeEmbedder
from tests.test_decisions import make_wiki, seed
from tests.test_screens import TOKEN, context


def suggester(tmp_path: Path, embedder=None, **kw) -> tuple[Suggester, Wiki]:
    wiki = make_wiki(tmp_path)
    seed(wiki)
    index = Index(tmp_path / "index.db")
    index.sync(wiki)
    s = Suggester(wiki, index, lambda: embedder, background=lambda work: work(), **kw)
    return s, wiki


def texts(items) -> list[tuple[str, str, str]]:
    return [(s.text, s.topic, s.kind) for s in items]


def test_empty_query_shows_questions_of_topics_with_cases(tmp_path):
    s, _ = suggester(tmp_path)
    assert texts(s.suggest("")) == [
        ("사이드카를 어떻게 운영할까", "서비스 메시 운영", "question"),  # 사례가 많은 주제 먼저
        ("모노레포 운영", "모노레포 운영", "topic"),  # 질문이 없으면 이름
    ]


def test_letters_match_names_questions_and_case_topics(tmp_path):
    s, _ = suggester(tmp_path)
    assert texts(s.suggest("모노")) == [
        ("모노레포 운영", "모노레포 운영", "topic"),
        ("모노레포 도구", "모노레포 운영", "case"),
    ]
    assert texts(s.suggest("사이드카")) == [
        ("사이드카를 어떻게 운영할까", "서비스 메시 운영", "question"),
        ("사이드카 버전 관리", "서비스 메시 운영", "case"),
    ]
    assert s.suggest("없는 주제") == []  # 사례가 없는 넓은 주제는 추천하지 않는다
    assert s.suggest("냉장고") == []


def test_meaning_matches_when_similar_enough(tmp_path):
    embedder = FakeEmbedder(synonyms={"istio": "메시"})
    s, _ = suggester(tmp_path, embedder, min_similarity=0.3)
    found = texts(s.suggest("istio"))
    assert ("서비스 메시 도입", "서비스 메시 운영", "case") in found
    assert all(topic == "서비스 메시 운영" for _, topic, _ in found)
    assert s.suggest("블록체인") == []  # 가까운 문장이 없으면 아무것도 보이지 않는다
    assert s.suggest("i") == []  # 한 글자로는 뜻을 찾지 않는다


def test_letter_matches_come_before_meaning_and_limit_holds(tmp_path):
    embedder = FakeEmbedder(synonyms={"모노": "사이드카"})
    s, _ = suggester(tmp_path, embedder, min_similarity=0.0, max_gap=1.0, limit=3)
    found = texts(s.suggest("모노"))
    assert found[:2] == [
        ("모노레포 운영", "모노레포 운영", "topic"),
        ("모노레포 도구", "모노레포 운영", "case"),
    ]
    assert len(found) == 3 and len({t for t, _, _ in found}) == 3  # 겹치지 않는다


def test_sentences_are_embedded_once_in_background(tmp_path):
    embedder = FakeEmbedder()
    waiting = []
    wiki = make_wiki(tmp_path)
    seed(wiki)
    index = Index(tmp_path / "index.db")
    index.sync(wiki)
    s = Suggester(wiki, index, lambda: embedder, background=waiting.append, min_similarity=0.0)
    assert texts(s.suggest("istio")) == []  # 아직 임베딩 전: 글자로만 찾는다
    s.suggest("istio")
    assert len(waiting) == 1  # 이미 만드는 중이면 또 시키지 않는다
    waiting.pop()()
    assert s.suggest("istio") and not waiting
    calls = embedder.calls
    s.suggest("메시")
    assert embedder.calls == calls and not waiting  # 같은 문장은 다시 임베딩하지 않는다


def test_model_problems_fall_back_to_letters(tmp_path):
    class Broken(FakeEmbedder):
        def passages(self, texts):
            raise RuntimeError("모델 파일이 없다")

    s, _ = suggester(tmp_path, Broken(), min_similarity=0.0)
    assert texts(s.suggest("모노"))[0][0] == "모노레포 운영"
    assert [t for t, _, _ in texts(s.suggest("istio"))] == []


# ---------- 화면 ----------


def client(tmp_path: Path) -> TestClient:
    wiki = make_wiki(tmp_path)
    seed(wiki)
    c = TestClient(create_app(context(wiki, tmp_path)), follow_redirects=False)
    c.cookies.set(COOKIE, TOKEN)
    return c


def test_suggest_partial_links_to_each_screen(tmp_path):
    c = client(tmp_path)
    decisions = c.get("/suggest", params={"q": "모노", "for": "decisions"}).text
    assert "<html" not in decisions and 'aria-label="추천 검색어"' in decisions
    assert (
        'href="/decisions?sel=%EB%AA%A8%EB%85%B8%EB%A0%88%ED%8F%AC+%EC%9A%B4%EC%98%81"' in decisions
    )
    search = c.get("/suggest", params={"q": "모노", "for": "search"}).text
    assert 'href="/search?q=%EB%AA%A8%EB%85%B8%EB%A0%88%ED%8F%AC+%EB%8F%84%EA%B5%AC"' in search
    assert "모노레포 운영</span>" in search  # 문장이 속한 넓은 주제
    start = c.get("/suggest", params={"q": "", "for": "search"}).text
    assert "이런 걸 찾을 수 있어요" in start and "사이드카를 어떻게 운영할까" in start
    assert c.get("/suggest", params={"q": "냉장고", "for": "search"}).text == ""  # 빈 상자도 없다


def test_search_boxes_ask_for_suggestions(tmp_path):
    c = client(tmp_path)
    for path, target in (("/search", "search"), ("/decisions", "decisions")):
        page = c.get(path).text
        assert f'hx-get="/suggest?for={target}"' in page
        assert f'id="{target}-suggest"' in page and 'aria-controls="' in page
        assert "input changed delay:3" not in page  # 치는 동안에는 검색하지 않는다 (추천을 볼 틈)
        assert '<button type="submit"' in page  # Enter나 버튼으로 찾는다
        assert 'id="suggest-loading"' in page  # 불러오는 동안 보일 자리표시
    search = c.get("/search").text
    assert 'hx-trigger="submit, change from:#search-body"' in search  # 필터는 바로 반영한다


def test_missing_model_file_falls_back_to_letters(tmp_path):
    wiki = make_wiki(tmp_path)
    seed(wiki)
    index = Index(tmp_path / "index.db")
    index.sync(wiki)

    def no_model():
        raise FileNotFoundError("모델을 내려받지 않았다")

    s = Suggester(wiki, index, no_model, background=lambda work: work(), min_similarity=0.0)
    assert [x.text for x in s.suggest("모노")] == ["모노레포 운영", "모노레포 도구"]
    assert s.suggest("istio") == []
    model = [FakeEmbedder()]
    s = Suggester(wiki, index, lambda: model[0], background=lambda work: work(), min_similarity=0.0)
    assert s.suggest("istio")  # 문장을 임베딩해 둔 뒤
    model[0] = None  # 모델을 쓸 수 없게 되면 글자로만 찾는다
    assert s.suggest("istio") == []


def test_warm_embeds_sentences_before_first_typing(tmp_path):
    embedder = FakeEmbedder()
    s, _ = suggester(tmp_path, embedder, min_similarity=0.0)
    s.warm()
    calls = embedder.calls
    assert calls > 0
    s.suggest("istio")
    assert embedder.calls == calls  # 이미 만들어 두었다


def test_meaning_drops_sentences_far_below_the_best(tmp_path):
    embedder = FakeEmbedder(synonyms={"istio": "메시"})
    near = suggester(tmp_path / "a", embedder, min_similarity=0.0, max_gap=0.01)[0]
    wide = suggester(tmp_path / "b", embedder, min_similarity=0.0, max_gap=1.0)[0]
    assert 0 < len(near.suggest("istio")) < len(wide.suggest("istio"))
