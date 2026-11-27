"""`ctx mcp`: Claude Code가 세션마다 stdio로 띄우는 MCP 서버 (plan 8장).

stdout은 MCP 프로토콜이 쓴다. 로그는 stderr로만 보낸다.
"""

from collections.abc import Callable

import httpx
from mcp.server.mcpserver import MCPServer

from context_holder.extract import html_to_markdown
from context_holder.feed import FetchError, get
from context_holder.mcptools import WikiTools

ORIGINAL_CHARS = 20_000  # 원문이 길면 앞부분만 넘긴다

INSTRUCTIONS = """빅테크 기술 블로그에서 모은 기술 결정 사례 위키다.
기술 선택, 아키텍처 결정, 트레이드오프 비교를 하기 전에 먼저 찾는다.
순서: find_decision_cases → 부족하면 search_wiki를 다른 말(한국어 · 영어)로 여러 개 →
그래도 없으면 웹 검색 → 좋은 글은 suggest_source로 남긴다.
답할 때는 사례의 원문 URL을 함께 밝힌다."""


def read_original(url: str, client: Callable[[], httpx.Client] = httpx.Client) -> str:
    """원문 페이지를 받아 본문만 Markdown으로. 수집과 같이 robots.txt를 지킨다."""
    try:
        with client() as http:
            response = get(http, url)
    except FetchError as e:
        return f"원문을 읽지 못했다: {e}"
    if response.status_code >= 400:
        return f"원문을 읽지 못했다: HTTP {response.status_code}"
    text = html_to_markdown(response.text, url)
    if not text.strip():
        return "원문에서 본문을 찾지 못했다."
    if len(text) > ORIGINAL_CHARS:
        text = text[:ORIGINAL_CHARS] + f"\n\n(원문이 길어 앞 {ORIGINAL_CHARS:,}자만 보냈다)"
    return f"원문: {url}\n\n{text}"


def build_server(tools: WikiTools, original: Callable[[str], str] | None = None) -> MCPServer:
    """original을 주면 글 원문 읽기 도구(read_original)도 연다 (글 상세 대화용)."""
    server = MCPServer("context-holder", instructions=INSTRUCTIONS)

    if original is not None:

        @server.tool(
            description=(
                "위키 글 id로 원문 본문을 읽는다. 요약에 없는 세부(코드, 수치, 구현, 인용)가"
                " 필요할 때만 쓴다."
            ),
            structured_output=False,
        )
        def read_original(id: str) -> str:
            article = tools.index.get(id)
            if article is None:
                return f"위키에 없는 글이다: {id}"
            return original(article.url)

    @server.tool(
        description=(
            "기술 선택 · 아키텍처 결정 · 트레이드오프 비교 전에 호출한다. "
            "다른 회사가 같은 문제에서 어떤 선택지를 두고 무엇을 골랐는지(Decision Record)를 "
            "찾는다. topic(예: '캐시 무효화') 또는 problem(문제 서술) 중 하나 이상을 준다."
        ),
        structured_output=False,
    )
    def find_decision_cases(
        topic: str | None = None, problem: str | None = None, limit: int = 5
    ) -> str:
        return tools.call("find_decision_cases", topic=topic, problem=problem, limit=limit)

    @server.tool(
        description=(
            "위키 글을 찾는다. 같은 뜻을 한국어 · 영어 · 관련 개념으로 바꾼 질의를 1~5개 주면 "
            "합쳐서 순위를 낸다. company(회사 이름이나 소스 id), since(YYYY-MM-DD)로 좁힐 수 있다. "
            "짧은 목록만 돌려주므로 상세는 get_article로 본다."
        ),
        structured_output=False,
    )
    def search_wiki(
        queries: list[str], company: str | None = None, since: str | None = None, limit: int = 8
    ) -> str:
        return tools.call("search_wiki", queries=queries, company=company, since=since, limit=limit)

    @server.tool(
        description="위키에 있는 결정 주제와 사례 수. prefix로 시작하는 주제만 볼 수 있다.",
        structured_output=False,
    )
    def list_topics(prefix: str | None = None) -> str:
        return tools.call("list_topics", prefix=prefix)

    @server.tool(
        description="결정 기록 전체(선택지, 트레이드오프, 결과, 원문 근거 문장, 신뢰도)를 본다.",
        structured_output=False,
    )
    def get_decision(id: str) -> str:
        return tools.call("get_decision", id=id)

    @server.tool(
        description="글의 한국어 요약과 핵심 내용을 본다. section: 요약 | 핵심 내용 | 결정 기록.",
        structured_output=False,
    )
    def get_article(id: str, section: str | None = None) -> str:
        return tools.call("get_article", id=id, section=section)

    @server.tool(
        description=(
            "위키에 없어 웹에서 찾은 좋은 기술 글을 수집 후보로 남긴다. reason에 왜 좋은지 쓴다."
        ),
        structured_output=False,
    )
    def suggest_source(url: str, reason: str) -> str:
        return tools.call("suggest_source", url=url, reason=reason)

    return server
