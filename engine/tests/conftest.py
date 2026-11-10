from collections.abc import Callable

import httpx
import pytest

LONG_BODY = " ".join(["캐시 무효화 전략을 이벤트 기반으로 바꾼 과정을 정리한다."] * 20)


def rss(items: list[dict[str, str]]) -> str:
    rows = "".join(
        f"""<item>
  <title>{i["title"]}</title>
  <link>{i["link"]}</link>
  <pubDate>{i.get("date", "Wed, 05 Nov 2026 01:00:00 GMT")}</pubDate>
  <description><![CDATA[{i.get("summary", "요약")}]]></description>
  {f"<content:encoded><![CDATA[{i['content']}]]></content:encoded>" if "content" in i else ""}
</item>"""
        for i in items
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>blog</title><link>https://blog.example.com</link>{rows}</channel></rss>"""


def article_page(title: str, body: str) -> str:
    return f"""<html><head><title>{title}</title></head><body>
<nav>홈 블로그 채용</nav>
<article><h1>{title}</h1><h2>배경</h2><p>{body}</p><p>{body}</p></article>
<footer>© example</footer></body></html>"""


Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def make_client() -> Callable[[Handler], httpx.Client]:
    def _make(handler: Handler) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return _make


@pytest.fixture(autouse=True)
def no_network_politeness(monkeypatch: pytest.MonkeyPatch) -> None:
    """테스트에서는 재시도 · 요청 간격 대기와 robots.txt 확인을 끈다.

    요청 수를 세는 테스트가 있어서다.
    """
    monkeypatch.setattr("context_holder.feed.time.sleep", lambda _: None)
    monkeypatch.setattr("context_holder.feed.RESPECT_ROBOTS", False)


class FakeEmbedder:
    """단어(토큰)가 겹칠수록 가까워지는 결정적 임베더. 실제 모델 없이 검색 흐름을 시험한다."""

    def __init__(
        self, model_id: str = "fake", dim: int = 64, synonyms: dict[str, str] | None = None
    ):
        self.model_id = model_id
        self.dim = dim
        self.synonyms = synonyms or {}  # 다른 말을 같은 뜻으로 묶는다 (실제 모델의 의미 이해 흉내)
        self.calls = 0

    def passages(self, texts):
        import numpy as np

        self.calls += 1
        return np.array([self._vec(t) for t in texts], dtype=np.float32).reshape(
            len(texts), self.dim
        )

    def query(self, text):
        return self._vec(text)

    def _vec(self, text):
        import hashlib

        import numpy as np

        from context_holder.textproc import search_tokens

        v = np.zeros(self.dim, dtype=np.float32)
        for token in search_tokens(text):
            token = self.synonyms.get(token, token)
            v[int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = np.linalg.norm(v)
        return v / norm if norm else v


@pytest.fixture(autouse=True)
def fake_search_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI가 실제 검색 모델을 내려받지 않게 한다."""
    monkeypatch.setattr(
        "context_holder.cli.make_embedder",
        lambda model_id, local_only=False: FakeEmbedder(model_id),
    )


@pytest.fixture(autouse=True)
def isolated_git_config(tmp_path_factory: pytest.TempPathFactory):
    """이 기기의 ~/.gitconfig를 읽지 않게 한다. CI와 개발 기기에서 같은 결과가 나오게."""
    import pygit2
    from pygit2.enums import ConfigLevel

    paths = pygit2.settings.search_path
    levels = (ConfigLevel.GLOBAL, ConfigLevel.XDG, ConfigLevel.SYSTEM)
    before = {level: paths[level] for level in levels}
    empty = str(tmp_path_factory.mktemp("gitconfig"))
    for level in levels:
        paths[level] = empty
    yield
    for level, path in before.items():
        paths[level] = path


@pytest.fixture
def remote(tmp_path):
    """GitHub 대신 쓰는 로컬 bare 레포. 경로를 원격 주소로 쓴다."""
    import pygit2

    path = tmp_path / "remote.git"
    pygit2.init_repository(str(path), bare=True, initial_head="main")
    return str(path)


@pytest.fixture
def make_device(tmp_path, remote):
    """원격에 연결된 기기 하나를 만든다 (위키 폴더 = 레포)."""
    from context_holder.gitsync import WikiRepo

    def _make(name: str) -> WikiRepo:
        repo = WikiRepo.create(tmp_path / name, remote)
        repo.set_identity(name, f"{name}@example.com")
        return repo

    return _make


class MemoryKeychain:
    """테스트용 키체인 저장소. keyring 함수를 이것으로 바꿔 진짜 macOS 키체인을 건드리지 않는다."""

    def __init__(self):
        self.secret: str | None = None

    def get(self, service, account):
        return self.secret

    def set(self, service, account, value):
        self.secret = value

    def delete(self, service, account):
        import keyring.errors

        if self.secret is None:
            raise keyring.errors.PasswordDeleteError("없음")
        self.secret = None


@pytest.fixture(autouse=True)
def no_real_keychain(monkeypatch: pytest.MonkeyPatch) -> MemoryKeychain:
    """모든 테스트에서 키체인과 gh 로그인을 가짜로 바꾼다."""
    memory = MemoryKeychain()
    monkeypatch.setattr("context_holder.auth.keyring.get_password", memory.get)
    monkeypatch.setattr("context_holder.auth.keyring.set_password", memory.set)
    monkeypatch.setattr("context_holder.auth.keyring.delete_password", memory.delete)
    monkeypatch.setattr("context_holder.auth.gh_lookup", lambda: None)
    return memory


@pytest.fixture(autouse=True)
def plain_tool_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """외부 명령은 이름 그대로 쓴다. 테스트가 이 기기에 무엇이 설치됐는지에 기대지 않게."""
    plain = _PlainNames()
    for module in ("llm", "claudesetup", "codexsetup", "setupflow", "auth"):
        monkeypatch.setattr(f"context_holder.{module}.tool", plain)


class _PlainNames:
    def __call__(self, name: str) -> str:
        return name

    def cache_clear(self) -> None:
        pass


@pytest.fixture(autouse=True)
def isolated_app_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """앱 폴더(~/.context-holder)를 건드리지 않게 한다. 계정 폴더도 여기에 생긴다."""
    if "CONTEXT_HOLDER_HOME" not in __import__("os").environ:
        monkeypatch.setenv("CONTEXT_HOLDER_HOME", str(tmp_path_factory.mktemp("app-home")))
