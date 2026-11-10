"""수동 추가는 URL만 비교하고, 수집 이력이나 본문 해시로 거르지 않는다."""

import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from context_holder.classify import Bucket, Decision
from context_holder.collect import Article
from context_holder.enrich import Enrichment
from context_holder.feed import FetchError
from context_holder.screens import addarticle
from context_holder.screens.app import COOKIE, create_app
from context_holder.wiki import article_id
from tests.test_screens import NOW, TOKEN, add, context, wiki  # noqa: F401


@pytest.fixture
def job(wiki, tmp_path, monkeypatch):  # noqa: F811
    ctx = context(wiki, tmp_path)
    job = addarticle.AddArticleJob(
        ctx, threading.Lock(), threading.Lock(), lambda: "claude", background=lambda work: work()
    )
    monkeypatch.setattr(
        addarticle,
        "fetch_article",
        lambda client, source, url: Article(
            source.id, url, "새 글", NOW - timedelta(days=400), "본문", "same"
        ),
    )
    monkeypatch.setattr(
        addarticle,
        "enrich",
        lambda *args: Enrichment(("요약",), ("핵심",), ("test",), None, None, 0),
    )
    return job


def test_add_unregistered_article_and_today(job):
    job.start("https://new.example/post?utm_source=test#part", busy=False)
    assert job.state == "done"
    [stored] = list(job.ctx.wiki.articles())
    assert stored.url == "https://new.example/post"
    assert stored.origin == "manual" and stored.bucket is Bucket.SAVE
    assert stored.collected == NOW
    assert "new.example" not in [s.id for s in job.ctx.wiki.sources()]
    assert job.ctx.index.get(stored.id)
    c = TestClient(create_app(job.ctx))
    c.cookies.set(COOKIE, TOKEN)
    page = c.get("/today").text
    assert "새 글" in page and "직접 추가" in page and "+ 글 추가" in page


def test_existing_url_does_not_fetch(job, monkeypatch):
    aid = add(job.ctx.wiki, "https://t/post", "기존 글")
    monkeypatch.setattr(addarticle, "fetch_article", lambda *args: pytest.fail("중복 수집"))
    job.start("https://t/post/?utm_source=test", busy=False)
    assert job.state == "done" and job.article_id == aid
    assert "이미" in job.message
    assert len(list(job.ctx.wiki.articles())) == 1


def test_dismiss_resets_finished_job_but_not_running(job):
    job.start("https://t/post", busy=False)
    job.dismiss()
    assert (job.state, job.url, job.message) == ("idle", "", "")
    assert job.take_changed() and not job.take_changed()  # 바로 닫아도 Today는 한 번 새로 고친다
    job.start("", busy=False)
    job.dismiss()
    assert job.state == "idle"
    tasks = []
    job.background = tasks.append
    job.start("https://t/other", busy=False)
    job.dismiss()
    assert job.state == "running"
    tasks[0]()
    assert job.state == "done"


def test_inbox_moves_without_fetch(job, monkeypatch):
    aid = add(
        job.ctx.wiki,
        "https://t/held",
        "보류",
        bucket=Bucket.INBOX,
        collected=NOW - timedelta(days=10),
    )
    monkeypatch.setattr(addarticle, "fetch_article", lambda *args: pytest.fail("보류 재수집"))
    job.start("https://t/held", busy=False)
    [stored] = list(job.ctx.wiki.articles())
    assert job.state == "done" and job.article_id == aid
    assert stored.bucket is Bucket.SAVE and stored.origin == "manual"
    assert stored.collected == NOW


def test_removed_url_can_be_added_and_hash_is_not_compared(job):
    wiki = job.ctx.wiki  # noqa: F811
    url = "https://t/removed"
    aid = add(wiki, url, "삭제할 글", bucket=Bucket.INBOX)
    wiki.remove_article(next(wiki.articles()).path, aid)
    wiki.record_seen([Decision(url, 0.4, Bucket.INBOX, "", (), ())], NOW, hashes={url: "same"})
    job.start(url, busy=False)
    assert job.state == "done" and job.article_id == article_id(url)
    job.start("https://t/another-url", busy=False)
    assert job.state == "done" and len(list(wiki.articles())) == 2


@pytest.mark.parametrize(
    "url", ["", "file:///tmp/a", "https://", "https://u:p@t/a", "https://t:bad/a"]
)
def test_invalid_url(job, url):
    job.start(url, busy=False)
    assert job.state == "error" and not list(job.ctx.wiki.articles())


def test_failure_retains_url_and_retry(job, monkeypatch):
    fetch = addarticle.fetch_article

    def fail(*args):
        raise FetchError("blocked")

    monkeypatch.setattr(addarticle, "fetch_article", fail)
    job.start("https://t/post", busy=False)
    assert job.state == "error" and job.url == "https://t/post"
    assert not list(job.ctx.wiki.articles())
    monkeypatch.setattr(addarticle, "fetch_article", fetch)
    job.start(job.url, busy=False)
    assert job.state == "done"


def test_busy_and_double_submission(job):
    job.start("https://t/post", busy=True)
    assert job.state == "error"
    tasks = []
    job.background = tasks.append
    job.start("https://t/post", busy=False)
    job.start("https://t/other", busy=False)
    assert job.url == "https://t/post" and len(tasks) == 1
    tasks[0]()
    assert job.state == "done"


def test_routes(job, monkeypatch):
    def start(self, url, busy):
        job.start(url, busy)
        self.state, self.message, self.url = job.state, job.message, job.url
        self.article_id, self.in_modal, self.changed = job.article_id, job.in_modal, job.changed

    original = addarticle.AddArticleJob.start
    monkeypatch.setattr(addarticle.AddArticleJob, "start", start)
    job.start = lambda url, busy: original(job, url, busy)
    c = TestClient(create_app(job.ctx))
    c.cookies.set(COOKIE, TOKEN)
    assert c.post("/articles/add", data={"url": "https://t/a"}).status_code == 403
    response = c.post("/articles/add", data={"url": "https://t/a"}, headers={"HX-Request": "true"})
    assert response.status_code == 200 and response.headers["HX-Trigger"] == "add-started"
    status = c.get("/articles/add/status").text
    assert "위키에 추가했어요" in status and "data-auto-dismiss" in status
    assert "위키에 추가했어요" not in c.get("/articles/add").text
    assert c.get(f"/article/{job.article_id}").status_code == 200
    assert "add-status-note" in c.get("/articles/add/status").text
    dismissed = c.post("/articles/add/dismiss", headers={"HX-Request": "true"})
    assert dismissed.status_code == 200 and "add-status-note" not in dismissed.text
    assert "위키에 추가했어요" not in c.get("/articles/add").text


def test_new_form_starts_fresh(job):
    c = TestClient(create_app(job.ctx))
    c.cookies.set(COOKIE, TOKEN)
    c.post("/articles/add", data={"url": "bad"}, headers={"HX-Request": "true"})
    assert 'role="alert"' in c.get("/articles/add").text
    assert 'role="alert"' not in c.get("/articles/add?new=1").text


def test_summary_failure_keeps_url_without_partial_save(job, monkeypatch):
    from context_holder.llm import ErrorKind, LlmError

    def fail(*args):
        raise LlmError(ErrorKind.AUTH, "login")

    monkeypatch.setattr(addarticle, "enrich", fail)
    job.start("https://t/post", busy=False)
    assert job.state == "error" and job.url == "https://t/post"
    assert not list(job.ctx.wiki.articles())
    assert "로그인" in job.message
    assert not job.ctx.wiki.seen_urls()


def test_today_refreshes_once_after_wiki_changes(job, monkeypatch):
    app = create_app(job.ctx)
    c = TestClient(app)
    c.cookies.set(COOKIE, TOKEN)
    today = {"HX-Request": "true", "HX-Current-URL": "http://testserver/today"}
    c.post("/articles/add", data={"url": "https://t/new"}, headers={"HX-Request": "true"})
    refreshes = 0
    for _ in range(100):  # 앱 안의 작업은 백그라운드 스레드에서 끝난다
        response = c.get("/articles/add/status", headers=today)
        refreshes += response.headers.get("HX-Refresh") == "true"
        if "글 보기" in response.text:
            break
        threading.Event().wait(0.05)
    response = c.get("/articles/add/status", headers=today)
    assert refreshes + (response.headers.get("HX-Refresh") == "true") == 1


def test_refresh_is_consumed_on_other_pages(job):
    c = TestClient(create_app(job.ctx))
    c.cookies.set(COOKIE, TOKEN)
    c.post("/articles/add", data={"url": "https://t/new"}, headers={"HX-Request": "true"})
    for _ in range(100):
        response = c.get("/articles/add/status")
        assert "HX-Refresh" not in response.headers
        if "글 보기" in response.text:
            break
        threading.Event().wait(0.05)
    today = {"HX-Request": "true", "HX-Current-URL": "http://testserver/today?date=x"}
    assert "HX-Refresh" not in c.get("/articles/add/status", headers=today).headers


def test_other_pages_and_unchanged_wiki_do_not_refresh(job, monkeypatch):
    add(job.ctx.wiki, "https://t/post", "기존 글")
    job.ctx.index.sync(job.ctx.wiki)
    c = TestClient(create_app(job.ctx))
    c.cookies.set(COOKIE, TOKEN)
    today = {"HX-Request": "true", "HX-Current-URL": "http://testserver/today"}
    c.post("/articles/add", data={"url": "https://t/post"}, headers={"HX-Request": "true"})
    for _ in range(100):
        if "글 보기" in c.get("/articles/add/status").text:
            break
        threading.Event().wait(0.05)
    assert "HX-Refresh" not in c.get("/articles/add/status", headers=today).headers


def test_existing_article_is_shown_in_modal_without_toast(job):
    add(job.ctx.wiki, "https://t/post", "기존 글")
    c = TestClient(create_app(job.ctx))
    c.cookies.set(COOKIE, TOKEN)
    hx = {"HX-Request": "true"}
    response = c.post("/articles/add", data={"url": "https://t/post"}, headers=hx)
    assert "HX-Trigger" not in response.headers
    assert "이미 위키에 있는 글이에요" in response.text and "글 보기" in response.text
    assert "add-status-note" not in c.get("/articles/add/status").text


def test_running_and_failed_show_toast(job, monkeypatch):
    tasks = []
    job.background = tasks.append
    job.start("https://t/new", busy=False)
    assert job.state == "running" and not job.in_modal

    def fail(*args):
        raise FetchError("blocked")

    monkeypatch.setattr(addarticle, "fetch_article", fail)
    tasks[0]()
    assert job.state == "error" and not job.in_modal and job.url == "https://t/new"


def test_invalid_url_stays_in_modal(job):
    job.start("bad", busy=False)
    assert job.state == "error" and job.in_modal
