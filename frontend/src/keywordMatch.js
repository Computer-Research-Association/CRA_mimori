// 검색창에 입력한 키워드가 이미 있는 키워드와 "사실상 같은 표기"인지 가늠한다.
// quality_test/matching.py의 normalize()와 같은 규칙(NFC 정규화 → 대소문자
// 무시 → 공백 전부 제거 → 물결류 장식 문자 제거)을 프론트에서도 그대로 쓴다 —
// 서버 쪽 relevance 판정과 "같은 단어로 본다"는 기준이 어긋나지 않게 하려는 것.
const DECORATIVE_RE = /[~〜﹏∼]/g
const WS_RE = /\s+/g

export function normalizeKeyword(s) {
  if (!s) return ''
  return s.normalize('NFC').toLowerCase().replace(WS_RE, '').replace(DECORATIVE_RE, '')
}

// 이 길이 미만인 정규화 결과는 비교에서 뺀다 — 한두 글자짜리는 부분 포함
// 관계만으로 "같은 밈"이라 보기엔 너무 흔해서 무관한 키워드끼리도 우연히
// 걸리기 쉽다(예: 어떤 키워드에도 흔히 들어가는 조사/음절 등).
const MIN_COMPARE_LENGTH = 2

// input과 "표기만 다르거나(공백·물결표 차이) 한쪽이 다른 쪽을 포함하는" 기존
// 키워드를 전부 찾는다. 완전일치(raw exact match)는 호출부에서 이미 처리했다고
// 가정하고 여기선 다루지 않는다 — 예: "거제야호"↔"거제 야호~"(표기 차이),
// "야호~"↔"거제 야호~"(포함 관계) 둘 다 여기서 잡힌다.
export function findSimilarKeywords(input, keywords) {
  const normInput = normalizeKeyword(input)
  if (normInput.length < MIN_COMPARE_LENGTH) return []
  return keywords.filter((kw) => {
    if (kw === input) return false // 완전 동일 원문은 호출부의 exact-match 처리 몫
    const normKw = normalizeKeyword(kw)
    if (normKw.length < MIN_COMPARE_LENGTH) return false
    return normKw === normInput || normKw.includes(normInput) || normInput.includes(normKw)
  })
}
