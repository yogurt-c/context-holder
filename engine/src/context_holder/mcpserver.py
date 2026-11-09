"""`ctx mcp`: Claude Code가 세션마다 stdio로 띄우는 MCP 서버 (plan 8장).

stdout은 MCP 프로토콜이 쓴다. 로그는 stderr로만 보낸다.
"""

from mcp.server.mcpserver import MCPServer

from context_holder.mcptools import WikiTools

INSTRUCTIONS = """빅테크 기술 블로그에서 모은 기술 결정 사례 위키다.
기술 선택, 아키텍처 결정, 트레이드오프 비교를 하기 전에 먼저 찾는다.
순서: find_decision_cases → 부족하면 search_wiki를 다른 말(한국어 · 영어)로 여러 개 →
그래도 없으면 웹 검색 → 좋은 글은 suggest_source로 남긴다.
답할 때는 사례의 원문 URL을 함께 밝힌다."""


def build_server(tools: WikiTools) -> MCPServer:
    server = MCPServer("context-holder", instructions=INSTRUCTIONS)

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
