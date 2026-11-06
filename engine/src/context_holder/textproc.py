"""검색용 토큰 분리. 한글은 두 글자씩(bigram), 그 밖의 글자는 단어 단위로 자른다.

한국어는 조사가 붙어 단어 단위로는 잘 안 맞는다("캐시를", "캐시가").
형태소 분석기 없이도 두 글자 단위로 자르면 "캐시"처럼 두 글자 단어까지 찾을 수 있다.
(FTS5 trigram은 세 글자 미만을 못 찾는다.)
"""

import re
import unicodedata

_TOKEN = re.compile(r"[가-힣]+|[a-z0-9]+")


def search_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN.finditer(unicodedata.normalize("NFKC", text).lower()):
        word = match.group(0)
        if "가" <= word[0] <= "힣" and len(word) > 1:
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
        else:
            tokens.append(word)
    return tokens
