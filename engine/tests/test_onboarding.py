"""시작하기 탭: 필수 단계 진행률, 사이드바 표시, 다 끝나면 탭을 뺀다."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from context_holder import appsettings, cli, onboarding
from context_holder.scheduler import Progress
from context_holder.topics import TOPICS_FILE
from context_holder.wiki import Wiki
from tests.test_screens import add
from tests.test_setup import flow, screen  # noqa: F401 - pytest fixture

STARTED = appsettings.Settings(setup_done=True)


def fresh_wiki(tmp_path) -> Wiki:
    wiki = Wiki(tmp_path / "w")
    wiki.ensure_config()
    return wiki


def screen_wiki(tmp_path) -> Wiki:
    return Wiki(tmp_path / "screen-wiki")  # screen()이 쓰는 위키


def group_topics(wiki: Wiki) -> None:
    path = wiki.root / TOPICS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("topics: []\n", encoding="utf-8")


def test_counts_required_steps_only(flow, tmp_path):  # noqa: F811
    wiki = fresh_wiki(tmp_path)
    progress = onboarding.build(flow, wiki, appsettings.Settings())
    assert [s.key for s in progress.steps] == ["github", "claude", "start", "first_sync", "topics"]
    assert (progress.done, progress.total, progress.complete) == (0, 5, False)


def test_steps_follow_the_wiki(flow, tmp_path):  # noqa: F811
    wiki = fresh_wiki(tmp_path)
    assert onboarding.build(flow, wiki, STARTED).done == 3  # 연결 · 시작은 설정을 마칠 때 갖췄다
    add(wiki, "https://d2.naver.com/a", "첫 글")
    after_sync = onboarding.build(flow, wiki, STARTED)
    assert after_sync.is_done("first_sync") and not after_sync.is_done("topics")
    assert after_sync.percent == 80
    group_topics(wiki)
    assert onboarding.build(flow, wiki, STARTED).complete


def test_sidebar_check_does_not_call_claude(flow, tmp_path):  # noqa: F811
    flow.run.calls.clear()
    progress = onboarding.build(flow, fresh_wiki(tmp_path), appsettings.Settings(), check=False)
    assert not progress.is_done("claude") and flow.run.calls == []


def test_pending_remembers_completion(flow, tmp_path):  # noqa: F811
    wiki = fresh_wiki(tmp_path)
    appsettings.update(flow.settings_path, setup_done=True)
    assert onboarding.pending(flow, wiki, flow.settings_path).done == 3
    add(wiki, "https://d2.naver.com/a", "첫 글")
    group_topics(wiki)
    assert onboarding.pending(flow, wiki, flow.settings_path) is None
    assert appsettings.load(flow.settings_path).onboarding_done
    (wiki.root / TOPICS_FILE).unlink()  # 나중에 상태가 바뀌어도 탭을 되살리지 않는다
    assert onboarding.pending(flow, wiki, flow.settings_path) is None


def test_sidebar_shows_progress_then_drops_the_tab(flow, tmp_path):  # noqa: F811
    c = screen(flow, tmp_path)
    page = c.get("/today").text
    assert 'class="onboard"' in page and ">0/5<" in page
    appsettings.update(flow.settings_path, setup_done=True)
    wiki = screen_wiki(tmp_path)
    add(wiki, "https://d2.naver.com/a", "첫 글")
    assert ">4/5<" in c.get("/search").text
    group_topics(wiki)
    assert 'class="onboard' not in c.get("/today").text
    assert appsettings.load(flow.settings_path).onboarding_done


def test_setup_page_walks_the_required_steps(flow, tmp_path):  # noqa: F811
    c = screen(flow, tmp_path)
    page = c.get("/setup").text
    assert "매일 모아 내 위키에 정리해요" in page and "0/5 단계 마침" in page
    for title in ("수집 시작", "첫 수집", "주제 정리", "Claude Code에서 위키 검색"):
        assert title in page
    assert "약 570MB" in page and "첫 수집이 끝나면 할 수 있어요" in page

    appsettings.update(flow.settings_path, setup_done=True, daily_at="07:40")
    add(screen_wiki(tmp_path), "https://d2.naver.com/a", "첫 글")
    page = c.get("/setup").text
    assert "매일 07:40에 수집해요" in page and 'name="hour"' not in page
    assert 'href="/decisions">Decisions에서 주제 정리' in page
    assert "다 마쳤어요" not in page


def test_setup_page_says_when_everything_is_done(flow, tmp_path):  # noqa: F811
    c = screen(flow, tmp_path)
    appsettings.update(flow.settings_path, setup_done=True)
    wiki = screen_wiki(tmp_path)
    add(wiki, "https://d2.naver.com/a", "첫 글")
    group_topics(wiki)
    page = c.get("/setup").text
    assert "다 마쳤어요" in page and 'class="onboard' not in page


def running_scheduler(stage: str, done: int, total: int) -> SimpleNamespace:
    return SimpleNamespace(
        running=True,
        progress=Progress(stage, done, total),
        started_at=datetime(2026, 11, 9, 3, tzinfo=UTC),
        paused_until=lambda: None,
        can_run_now=lambda: False,
    )


def test_first_sync_step_shows_model_download_and_polls(flow, tmp_path):  # noqa: F811
    appsettings.update(flow.settings_path, setup_done=True)
    c = screen(flow, tmp_path, scheduler=running_scheduler("model", 0, 0))
    panel = c.get("/setup/panel").text
    assert "검색 모델 내려받는 중 (약 570MB)" in panel and 'hx-trigger="every 2s"' in panel
    assert 'role="progressbar"' not in panel  # 셀 수 없는 단계는 막대를 그리지 않는다

    c = screen(flow, tmp_path, scheduler=running_scheduler("init", 3, 12))
    panel = c.get("/setup/panel").text
    assert "첫 수집 3/12" in panel and 'aria-valuenow="25"' in panel


def test_model_progress_label():
    assert Progress("model", 0, 0).label == "검색 모델 내려받는 중 (약 570MB)"


def test_announces_model_download_only_when_missing(tmp_path, monkeypatch, capsys):
    wiki = fresh_wiki(tmp_path)
    monkeypatch.setattr(cli, "home", lambda: tmp_path / "home")
    cli._announce_model(wiki, events=False)
    assert capsys.readouterr().out == ""
    cli._announce_model(wiki, events=True)
    line = json.loads(capsys.readouterr().out)
    assert line == {"event": "progress", "stage": "model", "done": 0, "total": 0}
    model = wiki.search_model().lower().split("/")[-1]
    (tmp_path / "home" / "models" / f"models--x--{model}").mkdir(parents=True)
    cli._announce_model(wiki, events=True)
    assert capsys.readouterr().out == ""
