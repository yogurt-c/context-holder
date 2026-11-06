from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from context_holder.classify import Bucket
from context_holder.embed import model_spec
from context_holder.index import Index
from context_holder.paragraphs import split_paragraphs
from context_holder.search import search
from context_holder.wiki import Wiki
from tests.conftest import FakeEmbedder
from tests.test_index import write

NOW = datetime(2026, 11, 6, tzinfo=UTC)


def test_split_paragraphs_merges_short_and_splits_long():
    short = "짧은 단락.\n\n또 짧은 단락."
    long_text = "가나다라마바사. " * 300

    assert split_paragraphs(short) == ["짧은 단락. 또 짧은 단락."]
    chunks = split_paragraphs(long_text)
    assert len(chunks) > 1 and all(len(c) <= 1000 for c in chunks)
    assert split_paragraphs("x" * 2500)[0] == "x" * 1000


def test_model_registry():
    assert model_spec("e5-small").dim == 384
    with pytest.raises(KeyError, match="모르는 검색 모델"):
        model_spec("nope")


def test_vectors_round_trip_without_text(tmp_path: Path):
    wiki = Wiki(tmp_path)
    vectors = np.eye(3, 4, dtype=np.float32)

    wiki.write_vectors("fake", "abc", vectors, content_hash="h")

    assert np.allclose(wiki.read_vectors("fake", "abc"), vectors)
    assert wiki.read_vectors("fake", "missing") is None
    assert "원문" not in wiki.vector_path("fake", "abc").with_suffix(".json").read_text()
    wiki.remove_vectors("fake")
    assert not (tmp_path / "vectors/fake").exists()
    wiki.remove_vectors("never-written")


def test_search_model_config(tmp_path: Path):
    wiki = Wiki(tmp_path)
    assert wiki.search_model() == "bge-m3"

    wiki.set_search_model("e5-small")

    assert wiki.search_model() == "e5-small"


def test_vector_search_finds_paraphrase_that_keyword_misses(tmp_path: Path):
    wiki = Wiki(tmp_path)
    gpu = write(wiki, "https://t/gpu", "GPU-native 클러스터", ("MIG로 인스턴스 분할",))
    write(wiki, "https://t/coupon", "선착순 쿠폰", ("수량 검증",))
    embedder = FakeEmbedder(synonyms={"그래": "mig", "래픽": "mig", "픽카": "mig", "카드": "mig"})
    index = Index(tmp_path / "index.db")
    index.sync(wiki, embedder)

    query = "그래픽카드 쪼개기"
    assert index.keyword(query) == []
    hits = search(index, query, embedder)

    assert hits[0].article.id == gpu
    assert hits[0].matched != "키워드" and hits[0].keyword_rank is None


def test_hybrid_search_fuses_keyword_and_vector_ranks(tmp_path: Path):
    wiki = Wiki(tmp_path)
    outbox = write(wiki, "https://t/o", "Outbox 도입기", ("CDC 릴레이",), topic="이벤트 발행")
    write(wiki, "https://t/c", "캐시 전략", ("이벤트 기반 만료",))
    index = Index(tmp_path / "index.db")
    embedder = FakeEmbedder()
    index.sync(wiki, embedder)

    hits = search(index, "outbox 이벤트 발행", embedder, limit=5)

    assert hits[0].article.id == outbox
    assert hits[0].keyword_rank == 1 and hits[0].vector_rank is not None


def test_keyword_only_search_without_embedder(tmp_path: Path):
    wiki = Wiki(tmp_path)
    aid = write(wiki, "https://t/k", "Kafka 암호화", ("종단 간 암호화",))
    index = Index(tmp_path / "index.db")
    index.sync(wiki)

    [hit] = search(index, "kafka", None)

    assert hit.article.id == aid and hit.matched == "키워드" and hit.vector_rank is None


def test_paragraph_vectors_from_wiki_are_searched(tmp_path: Path):
    wiki = Wiki(tmp_path)
    aid = write(wiki, "https://t/p", "제목만 있는 글", ("관련 없는 요약",))
    embedder = FakeEmbedder()
    paragraphs = ["첫 단락은 소개", "두 번째 단락에 NIC 인터럽트 분산 이야기"]
    wiki.write_vectors("fake", aid, embedder.passages(paragraphs), content_hash="h")
    index = Index(tmp_path / "index.db")
    index.sync(wiki, embedder)

    [hit] = [h for h in search(index, "NIC 인터럽트", embedder) if h.article.id == aid]

    assert hit.matched == "원문 2번째 단락"


def test_changing_model_rebuilds_vectors_and_buckets_filter(tmp_path: Path):
    wiki = Wiki(tmp_path)
    write(wiki, "https://t/s", "저장 글", ("벡터 검색",))
    inbox = write(wiki, "https://t/i", "보류 글", ("벡터 검색",), bucket=Bucket.INBOX)
    index = Index(tmp_path / "index.db")
    index.sync(wiki, FakeEmbedder("one"))

    other = FakeEmbedder("two", dim=32)
    assert index.sync(wiki, other) == (2, 0)  # 모델이 바뀌면 전부 다시 계산

    saved_only = search(index, "벡터 검색", other, buckets=(Bucket.SAVE,))
    assert inbox not in {h.article.id for h in saved_only}


def test_vector_search_on_empty_index(tmp_path: Path):
    index = Index(tmp_path / "index.db")
    embedder = FakeEmbedder()
    index.sync(Wiki(tmp_path), embedder)

    assert search(index, "아무거나", embedder) == []
