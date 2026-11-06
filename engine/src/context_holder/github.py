"""GitHub 계정에서 위키 레포를 찾고, 없으면 private으로 만든다 (plan 3.2)."""

from dataclasses import dataclass

import httpx

API = "https://api.github.com"
REPO_NAME = "context-wiki"


class GitHubError(Exception):
    pass


@dataclass(frozen=True)
class Account:
    login: str
    name: str
    noreply_email: str


@dataclass(frozen=True)
class WikiRemote:
    url: str
    created: bool


def account(client: httpx.Client, token: str) -> Account:
    data = _get(client, token, "/user").json()
    return Account(
        login=data["login"],
        name=data.get("name") or data["login"],
        noreply_email=f"{data['id']}+{data['login']}@users.noreply.github.com",
    )


def find_or_create_wiki(client: httpx.Client, token: str, login: str) -> WikiRemote:
    response = _get(client, token, f"/repos/{login}/{REPO_NAME}", allow_missing=True)
    if response.status_code == 404:
        created = client.post(
            f"{API}/user/repos",
            headers=_headers(token),
            json={
                "name": REPO_NAME,
                "private": True,
                "description": "context-holder 위키 (기술 결정 사례)",
            },
        )
        if created.status_code != 201:
            raise GitHubError(f"위키 레포를 만들지 못했다: {created.status_code} {created.text}")
        return WikiRemote(created.json()["clone_url"], created=True)
    data = response.json()
    if not data.get("private"):
        raise GitHubError(f"{login}/{REPO_NAME}가 공개 레포다. 위키는 private이어야 한다")
    return WikiRemote(data["clone_url"], created=False)


def _get(client: httpx.Client, token: str, path: str, allow_missing: bool = False):
    response = client.get(f"{API}{path}", headers=_headers(token))
    if response.status_code == 401:
        raise GitHubError("GitHub 토큰이 만료됐거나 잘못됐다. 다시 로그인한다")
    if response.status_code == 404 and allow_missing:
        return response
    if response.status_code != 200:
        raise GitHubError(f"GitHub 요청 실패 {path}: {response.status_code}")
    return response


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
