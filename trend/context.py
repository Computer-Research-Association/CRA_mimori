"""LLM이 트렌드 해석에 사용할 관측값을 계산한다. 유행 원인은 추정하지 않는다."""
from datetime import date, timedelta
import math
from statistics import mean


def format_trend_observations(result: dict) -> str:
    """반영된 소스의 일별 지표와 연속된 두 7일 구간의 비교를 제공한다."""
    lines = []
    definitions = (
        ('naver', '네이버 검색 관심도', 'ratios', 'naver_z', '상대지수이며 절대 검색 건수가 아님'),
        ('kakao', '카카오 블로그·카페 언급', 'kakao_counts', 'kakao_z', '수집된 게시물 언급 건수이며 검색 수요와 다름'),
        ('google', '구글 검색 관심도', 'google_ratios', 'google_z', '상대지수이며 절대 검색 건수가 아님'),
    )
    for source, label, field, z_field, unit in definitions:
        if source not in result.get('sources', []):
            continue
        score = result.get(z_field)
        score_note = f' (소스 z={score:+.2f})' if _finite(score) else ''
        lines.append(f'{label}{score_note}: {unit}.')
        points = {}
        for row in result.get(field) or []:
            try:
                day = date.fromisoformat(row['date'])
                value = row['ratio']
            except (KeyError, TypeError, ValueError):
                continue
            # 판정일 당일의 부분 집계와 미래 자료는 해석에서도 제외한다.
            if result.get('date') and day.isoformat() >= result['date']:
                continue
            if _finite(value) and value >= 0:
                points[day] = float(value)
        ordered = sorted(points.items())
        if not ordered:
            lines.append('사용 가능한 일별 관측값은 없음. 소스 점수만으로 추이를 만들어 설명하지 말 것.')
            continue
        first, last = ordered[0], ordered[-1]
        peak = max(ordered, key=lambda point: point[1])
        lines.append(
            f'관측 기간 {first[0]}~{last[0]}, 관측일 {len(ordered)}개. '
            f'마지막 관측값 {last[1]:.2f}; 구간 최고값 {peak[1]:.2f} ({peak[0]}).'
        )
        # 누락일이 있을 때 7개 관측을 7일이라고 잘못 부르지 않는다.
        window = [last[0] - timedelta(days=n) for n in range(13, -1, -1)]
        if all(day in points for day in window):
            previous = mean(points[day] for day in window[:7])
            recent = mean(points[day] for day in window[7:])
            change = (
                f'직전 7일 대비 {(recent / previous - 1) * 100:+.1f}%'
                if previous > 0 else '직전 평균이 0이므로 증감률 계산 불가'
            )
            lines.append(
                f'{window[7]}~{window[-1]} 7일 평균 {recent:.2f}; '
                f'{window[0]}~{window[6]} 직전 7일 평균 {previous:.2f}; {change}.'
            )
    lines.append(
        '종합 상태는 마지막 관측값의 과거 중앙값·사분위 범위 대비 robust z를 소스별로 가중 결합한 판정이다. '
        '두 7일 평균의 증감률은 별도 추이 설명이며 상태를 직접 결정하는 값이 아니다. '
        '집계일과 마지막 관측일은 다를 수 있다. 지표만으로 유행의 원인·실제 사용자 수·전체 인기도를 단정할 수 없다.'
    )
    return '\n'.join(lines)


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
