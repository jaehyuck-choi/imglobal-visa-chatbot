# -*- coding: utf-8 -*-
"""
관할청 매칭 모듈 — immigration_offices.json의 '관할구역' 자유서식 텍스트를 정확히 파싱한다.

문제였던 것: 관할구역 문자열에 "(김해시, 밀양시, 양산시 제외)"처럼 제외 조건이 괄호로
섞여 있어서, 단순 문자열 포함검사(city in text)로 매칭하면 제외 대상 도시가
오히려 그 사무소의 관할로 잘못 걸리는 오탐이 발생한다. (검증: office_match.py)

두 번째로 발견된 문제(2026-09-16, 사용자 리포트로 재현): 레코드마다 같은 시/도를 신/구
명칭으로 다르게 쓴다 — 예를 들어 춘천출입국·외국인사무소는 "강원특별자치도"(신 명칭)를 쓰는데
양주출입국·외국인사무소는 같은 지역(철원군)을 "강원도"(구 명칭)로 적어놨다. 그래서
find_offices_two_step(province="강원특별자치도", city="철원군", ...)을 호출하면 1단계
후보 좁히기에서 양주가 통째로 빠지고, 남은 후보(춘천 계열)는 철원군을 명시적으로 "제외"하고
있어서 안전장치가 발동해 "매칭 실패"로 잘못 끝났다. 원인은 2단계(제외조건 처리)가 아니라
1단계(후보 좁히기)가 province 문자열을 완전 일치로만 찾았다는 데 있었다. province_root()로
"도/특별자치도/광역시/특별시" 같은 접미사를 떼고 핵심 지명만으로 후보를 찾도록 고쳤다.

이 모듈은 이 두 문제를 해결한 버전이며, 실제 백엔드에서 이 함수를 그대로 가져다 쓰면 된다.
"""
import json
import re


def province_root(province: str) -> str:
    """'강원특별자치도' -> '강원', '경상남도' -> '경상남'처럼 접미사를 떼어 핵심 지명만 남긴다.
    같은 지역이 레코드마다 신/구 행정구역명으로 다르게 적혀 있어도(예: 강원도 vs 강원특별자치도)
    이 핵심 지명 기준으로 찾으면 후보에서 빠지지 않는다."""
    return re.sub(r'(특별자치도|특별자치시|광역시|특별시|도)$', '', province)


def parse_jurisdiction(text: str):
    """'관할구역' 문자열 -> (포함 지역 리스트, 제외 지역 리스트)"""
    excluded = []
    for m in re.finditer(r'\(([^()]*)제외\)', text):
        inside = m.group(1)
        excluded += [t.strip() for t in re.split(r'[,\s]+', inside) if t.strip()]
    cleaned = re.sub(r'\([^()]*\)', '', text)  # 괄호(제외 조건 포함) 제거 후 남은 포함지역만 추출
    included = [t.strip() for t in cleaned.split(',') if t.strip()]
    return included, excluded


def find_offices(city: str, offices: list) -> list:
    """
    사용자가 입력한 시/군/구 이름(city)이 실제로 관할하는 사무소 목록을 반환.
    같은 도시를 본청과 출장소가 동시에 관할하는 경우(예: 김해시 -> 부산청 별관 + 김해출장소)는
    둘 다 반환하며, 화면에는 이름에 '출장소'가 들어간 항목을 먼저 보여주는 걸 권장
    (사용자와 더 가깝고 구체적인 창구인 경우가 많음).
    """
    hits = []
    for o in offices:
        included, excluded = parse_jurisdiction(o['관할구역'])
        if any(city in ex or ex in city for ex in excluded):
            continue
        if any(city in inc or inc in city for inc in included):
            hits.append(o)
    hits.sort(key=lambda o: 0 if '출장소' in o['기관명'] or '센터' in o['기관명'] else 1)
    return hits


def find_offices_two_step(province: str, city: str, offices: list) -> list:
    """
    1단계: 사용자가 고른 '시/도'(예: 경상남도)가 관할구역 또는 주소에 언급된 사무소만 후보로 좁힌다.
    2단계: 그 후보들 안에서만 시/군/구까지 정밀 매칭(find_offices)한다.
    2단계에서 아무 데도 명시적으로 안 걸리면(예: '창원시'는 어느 관할구역 텍스트에도
    이름이 직접 등장하지 않음), 그 도(道) 안에서 '(...제외)' 패턴을 가진 사무소
    = 나머지 지역을 다 담당하는 '본청/사무소'로 자동 귀속시킨다.
    -> 전국 시/군/구 전체 목록(약 228개) 없이도, 이 54개 관할구역 데이터만으로
       올바른 결론을 낼 수 있다는 게 핵심 (사용자가 시/도를 먼저 골라줘서 후보를 줄여주기 때문).

    안전장치: 그 도 안의 어느 후보든 이 city를 '명시적으로 제외'한다고 써놨다면,
    fallback 사무소로 귀속시키지 않고 빈 리스트를 반환한다. 제외 조건은 "여기 담당 아님"이라는
    적극적인 신호이므로, 단순히 "어디에도 안 나옴"보다 훨씬 강한 근거다 — 그 city를 실제로
    담당하는 사무소가 (예: 목포출장소처럼) 후보 목록에서 우연히 빠져 있을 뿐일 가능성이 높으므로,
    엉뚱한 본청으로 잘못 귀속시키느니 "매칭 실패"로 남겨 사용자에게 직접 확인을 권하는 편이 안전하다.

    세 번째로 발견된 문제(2026-09-16, 전국 228개 시/군/구 전수 감사로 발견): 대구/대전/울산은
    관할구역 텍스트가 그냥 "대구광역시"처럼 아무 괄호도 없이 통째로 한 줄로만 적혀 있다 —
    즉 그 안의 구/군을 누구도 따로 떼어가지 않았다는 뜻인데, 예전 fallback 로직은 오직
    "도(...제외)"처럼 괄호가 붙은 경우만 '포괄 담당 사무소'로 인정했기 때문에, 괄호 자체가
    없는 이 케이스는 fallback도 못 타고 그냥 매칭 실패로 빠졌다(대구 9개 구/군, 대전 5개,
    울산 5개, 총 19곳 확인). 고친 방법: '(...제외)' 괄호가 있든 없든, parse_jurisdiction으로
    괄호를 지우고 남은 포함 목록 안에 그 도(道) 이름이 통째로 하나의 항목으로 들어있으면
    ("경상남도(김해시 제외)"도 괄호를 지우면 포함 목록에 "경상남도"가 그대로 남는다) 똑같이
    '포괄 담당 사무소'로 취급하도록 일반화했다. 이러면 예전 정규식 fallback이 하던 일도
    그대로 포함되면서, 괄호 없는 케이스까지 한 번에 커버된다.
    """
    root = province_root(province)
    candidates = [o for o in offices if root in o['관할구역'] or root in o['주소']]
    exact = find_offices(city, candidates)
    if exact:
        return exact

    explicitly_excluded_elsewhere = any(
        city in ex or ex in city
        for o in candidates
        for ex in parse_jurisdiction(o['관할구역'])[1]
    )
    if explicitly_excluded_elsewhere:
        return []

    # 명시적으로 안 걸리면: 괄호를 지운 포함 목록 안에 '그 도(道) 이름' 자체가 통째로 한 항목으로
    # 들어있는 사무소 = 하위 시/군/구를 아무도 따로 안 떼어간 '포괄 담당' 사무소로 자동 귀속.
    # province(사용자가 고른 전체 명칭)와 root(접미사 뗀 핵심 지명) 둘 다 시도.
    blanket = [
        o for o in candidates
        if province in parse_jurisdiction(o['관할구역'])[0]
        or root in parse_jurisdiction(o['관할구역'])[0]
    ]
    return blanket


if __name__ == '__main__':
    data = json.load(open('immigration_offices.json', encoding='utf-8'))['offices']

    print("=== 1단계 매칭만 사용 (시/도 후보 좁히기 없이 바로 전국 대상) ===")
    for city in ['김해시', '양산시', '완도군', '창원시']:
        found = [o['기관명'] for o in find_offices(city, data)]
        print(f"{city:6s} -> {found}")

    print("\n=== 2단계 매칭 (사용자가 먼저 시/도를 골랐다고 가정) ===")
    cases = [
        ('경상남도', '김해시'),
        ('경상남도', '창원시'),
        ('전라남도', '완도군'),       # 알려진 한계: 실제 데이터는 '전라남도'가 아니라 '전남광주통합특별시'를 씀
        ('전남광주통합특별시', '완도군'),  # 위와 같은 사례를 데이터가 실제로 쓰는 명칭으로 재시도 -> 목포출장소로 정확히 매칭
        ('충청남도', '천안시'),
        ('강원특별자치도', '철원군'),  # 2026-09-16 사용자 리포트로 발견: 양주는 '강원도'(구 명칭)로
                                    # 적혀 있어 신 명칭으로 검색하면 후보에서 빠졌었음 -> province_root로 수정
    ]
    for province, city in cases:
        found = [o['기관명'] for o in find_offices_two_step(province, city, data)]
        print(f"{province} {city:6s} -> {found}")

    print("\n=== 안전장치 확인: 후보 목록에서 담당 출장소가 우연히 빠진 경우 ===")
    # 목포출장소를 일부러 뺀 축소판 후보군 — 완도군을 실제로 담당하는 사무소가 없는 상태를 재현
    trimmed = [o for o in data if o['기관명'] != '광주출입국·외국인사무소 목포출장소']
    found = [o['기관명'] for o in find_offices_two_step('전남광주통합특별시', '완도군', trimmed)]
    print(f"(목포출장소 누락 상태) 전남광주통합특별시 완도군 -> {found}  (기대값: [] — 잘못된 본청 귀속 대신 매칭 실패로 처리)")
