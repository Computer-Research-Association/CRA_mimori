"""
chunker.py
정제된 텍스트(clean_content)를 임베딩 단위(청크)로 분할.

전략:
  - 나무위키(source == "namuwiki")이면서 섹션 마커가 있는 문서
    -> 문서 구조 기반: 마커로 섹션을 먼저 나누고, 섹션 하나가 CHUNK_SIZE보다
       길면 그 안에서만 재귀적 분할(RecursiveCharacterTextSplitter)을 적용
  - 그 외(커뮤니티/댓글형 소스, 마커 없는 짧은 나무위키 문서)
    -> 재귀적 분할만 적용. 구분자 우선순위에 "[댓글]" 경계를 포함해
       본문과 댓글이 같은 청크에서 최대한 섞이지 않도록 함

parent_id 필드: 청크가 속한 원본 문서의 MongoDB _id.
  부모 문서는 별도로 청킹하지 않는다 — memes 컬렉션에 이미 존재하는
  원본 문서 자체가 부모. parent_lookup.py로 재조회.
"""

from langchain_text_splitters import RecursiveCharacterTextSplitter

from config.config_cilent import CHUNK_SIZE, CHUNK_OVERLAP, NAMUWIKI_SECTION_MARKER

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n[댓글]\n", "\n\n", "\n", ". ", " ", ""],
)


def _split_namuwiki_sections(text: str) -> list[tuple[str | None, str]]:
    """
    NAMUWIKI_SECTION_MARKER로 시작하는 줄을 기준으로 (섹션 제목, 섹션 본문)
    리스트로 분할. 첫 마커 이전에 등장하는 텍스트(문서 상단 안내문 등)는
    제목 없음(None) 섹션으로 취급.
    """
    sections: list[tuple[str | None, list[str]]] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for line in text.split("\n"):
        if line.startswith(NAMUWIKI_SECTION_MARKER):
            sections.append((current_title, current_lines))
            current_title = line[len(NAMUWIKI_SECTION_MARKER):].strip()
            current_lines = []
        else:
            current_lines.append(line)
    sections.append((current_title, current_lines))

    return [
        (title, "\n".join(lines).strip())
        for title, lines in sections
        if "\n".join(lines).strip()
    ]


def chunk_document(doc: dict) -> list[dict]:
    """
    전처리된 문서 하나를 청크 리스트로 변환.

    doc은 다음 필드를 포함해야 함:
      _id, source, keyword, url, title, published_date, crawled_at,
      clean_content(없으면 content로 대체)

    반환 딕셔너리의 parent_id는 원본 문서의 _id.
    청크 검색 히트 후 원본 문서가 필요하면 parent_lookup.py로 재조회.
    """
    text = doc.get("clean_content") or doc.get("content") or ""
    if not text.strip():
        return []

    if doc.get("source") == "namuwiki" and NAMUWIKI_SECTION_MARKER in text:
        pieces: list[tuple[str | None, str]] = []
        for title, section_text in _split_namuwiki_sections(text):
            for piece in _splitter.split_text(section_text):
                pieces.append((title, piece))
    else:
        pieces = [(None, piece) for piece in _splitter.split_text(text)]

    chunks = []
    for i, (section_title, chunk_text) in enumerate(pieces):
        chunks.append({
            "parent_id":     doc.get("_id"),
            "chunk_index":   i,
            "text":          chunk_text,
            "source":        doc.get("source"),
            "keyword":       doc.get("keyword"),
            "url":           doc.get("url"),
            "title":         doc.get("title"),
            "section_title": section_title,
            "published_date": doc.get("published_date"),
            "crawled_at":    doc.get("crawled_at"),
        })
    return chunks