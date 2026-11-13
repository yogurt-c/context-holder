"""Sources 화면의 소스 추가 · 수집 제외."""

import threading

import pytest
from fastapi.testclient import TestClient

from context_holder.discover import NotCollectable, Probe
from context_holder.feed import FeedEntry
from context_holder.screens import addsource
from context_holder.screens.app import COOKIE, create_app
from tests.test_screens import NOW, TOKEN, context, wiki  # noqa: F401

HTMX = {"HX-Request": "true"}
FOUND = Probe(
    feed_url="https://blog.snail.kr/feed",
    title="달팽이 기술 블로그",
    region="domestic",
    recent=(FeedEntry("probe", "https://blog.snail.kr/1", "캐시 전략", NOW, ""),),
    warnings=("마지막 글이 200일 전이에요. 새 글이 드물 수 있어요.",),
)


@pytest.fixture
def probes(monkeypatch):
    """discover.probe 대신 돌려줄 결과 (Probe나 예외)."""
    result: dict[str, object] = {"value": FOUND}

    def fake(client, url, now, on_step):
        on_step("피드를 찾는 중…")
        value = result["value"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(addsource.discover, "probe", fake)
    return result


@pytest.fixture
def job(wiki, tmp_path, probes):  # noqa: F811
    return addsource.AddSourceJob(
        context(wiki, tmp_path), threading.Lock(), background=lambda work: work()
    )


def test_check_then_add(job):
    job.check(" https://blog.snail.kr ")
    assert job.state == "ready" and job.probe == FOUND and job.restore is None

    added = job.add("  달팽이   블로그 ", "domestic", busy=False)

    assert added is not None and added.name == "달팽이 블로그"
    assert added.id == "blog.snail.kr" and added.feed_url == FOUND.feed_url
    assert job.state == "idle"
    assert "blog.snail.kr" in [s.id for s in job.ctx.wiki.active_sources()]


def test_check_rejects_bad_url_and_reports_not_collectable(job, probes):
    job.check("blog.snail.kr")
    assert job.state == "error" and "http(s)" in job.message

    probes["value"] = NotCollectable("RSS/Atom 피드를 찾지 못했어요.")
    job.check("https://nothing.example")
    assert (job.state, job.message) == ("error", "RSS/Atom 피드를 찾지 못했어요.")
    assert job.url == "https://nothing.example"


def test_check_reports_source_already_collected(job, probes):
    probes["value"] = Probe("http://toss.tech/rss/", "Toss", "domestic", (), ())
    job.check("https://toss.tech")
    assert job.state == "error" and "이미 수집하고 있는 소스예요: Toss" in job.message


def test_excluded_source_is_restored_not_added_again(job, probes):
    job.ctx.wiki.exclude_source("toss")
    probes["value"] = Probe("https://toss.tech/rss", "Toss Tech", "domestic", (), ())
    job.check("https://toss.tech")
    assert job.state == "ready" and job.restore is not None and job.restore.id == "toss"

    restored = job.add("", "", busy=False)

    assert restored is not None and restored.id == "toss"
    sources = job.ctx.wiki.sources()
    assert [s.id for s in sources].count("toss") == 1 and not any(s.excluded for s in sources)


def test_add_keeps_form_on_error(job):
    job.check("https://blog.snail.kr")
    assert job.add("달팽이", "domestic", busy=True) is None
    assert job.form_error == addsource.BUSY and job.state == "ready"
    assert job.add("   ", "domestic", busy=False) is None and "이름" in job.form_error
    assert job.add("달팽이", "mars", busy=False) is None and "국내" in job.form_error
    assert job.add("달팽이", "domestic", busy=False) is not None


def test_dismiss_keeps_running_check(job):
    tasks = []
    job.background = tasks.append
    job.check("https://blog.snail.kr")
    job.dismiss()
    assert job.state == "checking"
    tasks[0]()
    assert job.state == "ready"
    job.dismiss()
    assert (job.state, job.probe) == ("idle", None)


# ---------- 화면 ----------


@pytest.fixture
def client(wiki, tmp_path, probes, monkeypatch):  # noqa: F811
    class Inline:  # 확인을 뒤가 아니라 요청 안에서 끝낸다
        Lock = threading.Lock

        class Thread:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                self.target()

    monkeypatch.setattr(addsource, "threading", Inline)
    c = TestClient(create_app(context(wiki, tmp_path)), follow_redirects=False)
    c.cookies.set(COOKIE, TOKEN)
    return c


def test_sources_page_has_add_button_and_exclude_buttons(client):
    page = client.get("/sources").text
    assert "+ 소스 추가" in page and 'id="source-modal"' in page
    assert 'hx-post="/sources/toss/exclude"' in page
    assert "<b>Toss</b>의 새 글을 더는 모으지 않아요." in page


def test_add_flow_through_routes(client):
    form = client.get("/sources/add?new=1").text
    assert 'hx-post="/sources/add/check"' in form

    ready = client.post("/sources/add/check", data={"url": "https://blog.snail.kr"}, headers=HTMX)
    assert "이 블로그는 수집할 수 있어요." in ready.text
    assert 'value="달팽이 기술 블로그"' in ready.text and "캐시 전략" in ready.text
    assert 'value="domestic" checked' in ready.text and "200일 전" in ready.text

    added = client.post("/sources/add", data={"name": "달팽이", "region": "domestic"}, headers=HTMX)
    assert added.headers.get("HX-Refresh") == "true"
    page = client.get("/sources").text
    assert "달팽이" in page and "3곳" in page


def test_add_error_stays_in_modal(client):
    client.post("/sources/add/check", data={"url": "https://blog.snail.kr"}, headers=HTMX)
    response = client.post("/sources/add", data={"name": " ", "region": "domestic"}, headers=HTMX)
    assert "HX-Refresh" not in response.headers and "이름을 입력해 주세요." in response.text


def test_exclude_hides_source_but_keeps_name(client, wiki):  # noqa: F811
    response = client.post("/sources/toss/exclude", headers=HTMX)
    assert response.status_code == 204
    page = client.get("/sources").text
    assert "1곳" in page and "/sources/toss/exclude" not in page
    assert next(s for s in wiki.sources() if s.id == "toss").name == "Toss"

    missing = client.post("/sources/nope/exclude", headers=HTMX)
    assert missing.status_code == 422 and "없는 소스" in missing.text


def test_write_routes_need_htmx_header(client):
    assert client.post("/sources/toss/exclude").status_code == 403
