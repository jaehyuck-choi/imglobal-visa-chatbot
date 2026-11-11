"""
2단계: 1단계 원자료(D-2 매뉴얼 JSON, easylaw 절차 개요 JSON, 출입국관서 현황 API)를
서비스가 실제로 쓸 수 있는 "조건 -> 서류 -> 발급경로 -> 관할청" 규칙 객체로 정규화한다.

입력:
  - D2_stay_extension_stage1.json   (법무부 매뉴얼 원문 미러, 1단계 산출물)
  - process_overview_easylaw.json   (체류기간연장허가 공통 절차, easylaw.go.kr)
  - immigration_offices.json        (관할청 현황, data.go.kr 15040715)

출력:
  - D2_stay_extension_stage2.json

여기서부터는 원문에 없는 내용(발급 경로 안내문, 등급확인 안내문)이 섞이므로,
1단계 파일들은 그대로 두고 이 파일에서만 조합한다 (1단계의 "원문 그대로" 성격을 보존하기 위함).
"""
import json
import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from office_lookup import find_offices_two_step  # noqa: E402
from full_region_list import REGIONS  # noqa: E402

BASE = '../data'
STAGE1 = f'{BASE}/D2_stay_extension_stage1.json'
PROCESS = '../data/process_overview_easylaw.json'  # NOTE: 원래 Claude 작업세션의 임시 스크래치 파일로, 현재 저장소에는 포함되어 있지 않음 (재현 시 별도 준비 필요)
OFFICES = '../regions/immigration_offices.json'
OUT = f'{BASE}/D2_stay_extension_stage2.json'


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def build_full_region_index(office_records):
    """
    전국 시/도 + 시/군/구 228개 전체(full_region_list.REGIONS)를 office_lookup.py의
    find_offices_two_step()에 실제로 하나씩 돌려서, "이 지역을 화면에 버튼/드롭다운으로
    내놨을 때 관할청이 뭐라고 뜨는지"를 미리 다 계산해둔 표를 만든다.

    2026-09-16, 사용자가 "16개 시/도 버튼은 예시일 뿐이고 전체 커버리지 확인은 안 됐다"는
    한계를 지적하면서 "아예 전수 데이터를 만들자"고 요청해서 추가함. 이 전수 실행 자체가
    곧 회귀 테스트였고, 실제로 대구/대전/울산 소속 19개 구/군에서 매칭 실패가 나는 진짜
    버그(관할구역 텍스트에 괄호 없이 "대구광역시"처럼 통째로만 적혀 있는 케이스를 기존
    fallback이 못 잡던 문제)를 찾아서 office_lookup.py의 find_offices_two_step 자체를
    고쳤다 — 그 수정판으로 이 인덱스를 만들었으므로, 이 표는 "매칭 실패 0건"이 재확인된
    상태다(아래 build_stage2.py 실행 시 assert로 재확인).
    """
    rows = []
    for province, cities in REGIONS.items():
        for city in cities:
            matches = find_offices_two_step(province, city, office_records)
            names = [m['기관명'] for m in matches]
            rows.append({
                "시도": province,
                "시군구": city,
                "대표_관할청": names[0] if names else None,
                "관할청_전체": names,
            })
    failed = [r for r in rows if not r["관할청_전체"]]
    if failed:
        # 이 스크립트를 다시 돌렸을 때 새로운 매칭 실패가 생기면(예: office 데이터를 고치다
        # 실수로 어떤 지역의 커버리지를 깨먹으면) 조용히 넘어가지 않고 바로 여기서 멈춰서
        # 알려준다 — "우리가 신경 쓴 부분"이 조용히 깨지는 걸 막기 위한 안전장치.
        raise RuntimeError(
            f"전국 시/군/구 전수 매칭에서 {len(failed)}건 실패: "
            + ', '.join(f"{r['시도']} {r['시군구']}" for r in failed)
        )
    return rows


def main():
    stage1 = load(STAGE1)
    process = load(PROCESS)
    offices = load(OFFICES)
    full_region_index = build_full_region_index(offices["offices"])

    # ── 조건별 규칙 (나.세부심사기준 + 가.기본원칙을 조건-결과 형태로 재구성) ──
    # "cycle"/"max_stay_per_grant" 원문 문구만으로는 비전공자가 바로 이해하기 어려워서,
    # explanation(쉬운 말 풀이)과 example(구체적 날짜로 계산해 본 예시)을 각 규칙에 덧붙였다.
    rules = [
        {
            "id": "cycle_degree",
            "condition": {"visa_subtype": ["D-2-1", "D-2-2", "D-2-3", "D-2-4", "D-2-7"], "note": "학위과정"},
            "cycle": "외국인등록은 다음년도 3월말 또는 9월말로 조정, 변경·연장은 2년 이내에서 3월말 또는 9월말까지 허가",
            "explanation": (
                "쉽게 말하면, 학기가 3월과 9월에 시작하니까 체류기간 만료일도 그 학기가 끝나는 시점인 3월 31일 또는 "
                "9월 30일 중 가까운 쪽으로 맞춰진다. 실제 입국일이 언제였는지는 상관없다. 연장을 받을 때도 마찬가지 "
                "방식이 적용되는데, 이번엔 한 번에 최대 2년치까지 몰아서 허가해줄 수 있고, 그 2년의 끝나는 날짜 역시 "
                "항상 3월말 또는 9월말 중 하나가 되도록 맞춘다."
            ),
            "example": (
                "예시: 2026년 5월 20일에 입국해서 외국인등록을 하면, 등록증 만료일은 그 다음 맞는 학기말인 "
                "2027년 3월 31일로 잡힌다(2026년 9월 30일이 아니라, 최소 학기 하나는 채우도록 다음 해 3월 기준). "
                "그 뒤 만료가 다가와서 연장 신청을 하면, 이번엔 최대 2년을 한 번에 줄 수 있으므로 2029년 3월 31일까지 "
                "허가받을 수 있다(사안에 따라 1년 단위인 2028년 3월 31일까지만 나올 수도 있음 — 실제 부여 기간은 심사 결과에 따름)."
            ),
        },
        {
            "id": "cycle_research",
            "condition": {"visa_subtype": ["D-2-5"], "note": "연구유학"},
            "max_total_stay": "2년 초과 불가",
            "max_stay_per_grant": "1년",
            "explanation": (
                "연구유학(D-2-5)은 학위과정과 다르게 학기말에 맞추는 조정이 없다. 대신 한 번에 최대 1년까지만 "
                "체류기간을 주고, 아무리 연장을 반복해도 처음 체류자격을 받은 날로부터 통산 2년을 넘길 수 없다."
            ),
            "example": (
                "예시: 2026년 3월 1일에 연구유학 자격으로 체류를 시작하면, 최초 허가는 최대 2027년 2월 28일까지(1년). "
                "이후 연장을 신청해도 전체 체류기간이 시작일로부터 2년, 즉 2028년 2월 29일을 넘을 수 없으므로 "
                "그 이상은 다른 체류자격으로 바꾸거나 출국해야 한다."
            ),
        },
        {
            "id": "cycle_exchange",
            "condition": {"visa_subtype": ["D-2-6", "D-2-8"], "note": "교환학생/방문학생"},
            "cycle": "해당 과정 종료일로부터 1개월 범위 내에서 1월말 또는 7월말로 조정하여 허가",
            "explanation": (
                "교환학생·방문학생은 원래 다니는 학교의 학적이 본국에 있고, 한국 대학에서의 학습 '과정'이 끝나는 "
                "날짜가 기준이 된다. 그 과정 종료일 기준으로 가장 가까운 1월말 또는 7월말(1개월 이내 범위)로 "
                "체류기간을 맞춰서 허가해준다."
            ),
            "example": (
                "예시: 교환학기가 2026년 6월 20일에 끝난다면, 종료일로부터 1개월 이내인 2026년 7월 31일로 "
                "체류기간이 맞춰진다. 만약 과정이 12월 10일에 끝난다면 다음해 1월 31일로 맞춰진다."
            ),
        },
        {
            "id": "tier_top",
            "condition": {"institution_tier": ["우수인증대학", "인증대학"], "gpa_min": 2.0},
            "max_stay_per_grant": "1회 2년",
            "documents_waived": ["재정입증 서류(단, 인증대학 장관 고시 국가 및 중점 관리 국가 국민은 제외 — 이 경우 정상 제출)"],
            "asked_in_chatbot": False,
            "chatbot_usage": "직접 묻지 않고, 일반대학 기준 안내에 '해당하면 서류가 면제될 수 있다'는 참고 문구로만 노출(B안)",
        },
        {
            "id": "tier_general",
            "condition": {"institution_tier": ["일반대학"], "note": "인증대학·비자심사강화대학 명단에 속하지 않는 대학"},
            "max_stay_per_grant": "1회 최대 2년",
            "documents_required_extra": ["재정능력 입증서류", "체류지 입증서류"],
            "special_case": "베트남 국적 어학연수생은 재정능력 심사기준 특례 적용(일반대학 이하)",
            "asked_in_chatbot": False,
            "chatbot_usage": "질문 없이 모든 사용자에게 보수적 기본값으로 항상 적용(B안) — 화면에는 질문 흐름 중간이 아니라 최종 요약에서 준비서류 다음·관할청 연락처 바로 앞에 배치해, '전화로 확인하라'는 안내와 실제 전화번호가 붙어서 보이도록 한다(사용자 피드백, 2026-09-16)",
        },
        {
            "id": "tier_weak",
            "condition": {"institution_tier": ["비자심사강화대학(舊 하위대학)"], "gpa_max": 1.0},
            "max_stay_per_grant": "최초 1회 6개월 이내(사유서 필요)",
            "note": "다음 연장 시에도 평균 D학점 이하면 체류기간 연장 제한",
            "asked_in_chatbot": False,
            "chatbot_usage": "직접 묻지 않음(B안) — 다만 tier_top과 함께 최종 요약에 '해당하면 이렇게 될 수 있다'는 주의 문구로 노출하고, 정확한 해당 여부는 관할청 확인 안내로 대체(2026-09-16: 이전엔 tier_top만 보여주고 이 내용은 화면에 빠져 있었음 — 사용자 피드백으로 추가)",
        },
        {
            "id": "night_class",
            "condition": {"class_schedule": "야간 또는 주말 수업 수강"},
            "restriction": "원칙적으로 체류허가 제한. 주간·주중 수업으로 변경 확인되면 허가 가능",
            "exception": "법무부에서 허용한 일부 야간 학위과정 재학생 제외",
            "asked_in_chatbot": True,
        },
        {
            "id": "leave",
            "condition": {"academic_status": "휴학(개인사정·학점미달)"},
            "restriction": "체류기간 연장 제한",
            "exception": "질병·사고 등 부득이한 사유가 인정되는 경우 예외적으로 체류자격 변경 등 조치",
            "asked_in_chatbot": True,
        },
    ]
    for r in rules[:3]:
        r["asked_in_chatbot"] = False
        r["chatbot_usage"] = "직접 묻지 않고 known_at_login의 visa_subtype(로그인 시 이미 아는 값)으로 자동 적용"

    # hard_caps_by_degree도 "몇 년까지"라는 숫자만 있으면 와닿지 않으므로, 학사일정을 고려한
    # explanation과 example(입학연도 기준 계산)을 덧붙인다. 이 상한은 eligibility_rules의 개별
    # 연장 허가와는 별개로 "아무리 연장을 반복해도 이 날짜는 넘을 수 없다"는 절대 한도다.
    hard_caps_by_degree = {
        "설명": (
            "이건 '한 번에 몇 년 주는지'가 아니라 '연장을 아무리 반복해도 입학일 기준으로 최대 언제까지만 "
            "체류를 허용하는지'를 정한 절대 상한이다. 예: 학사과정으로 2022년 3월에 입학했다면, 매번 연장을 "
            "잘 받아왔어도 원칙적으로 2028년 2월(입학 후 6년)을 넘겨서까지 유학(D-2) 자격으로 체류할 수는 없다."
        ),
        "전문학사": {"기본상한": "입학 후 3년", "예외": "3년제 전문학사는 4년", "example": "2026년 3월 입학 → 기본상한 2029년 2월까지(3년제는 2030년 2월까지)"},
        "학사": {"기본상한": "입학 후 6년", "예외": "5년제 학사는 7년", "example": "2026년 3월 입학 → 기본상한 2032년 2월까지(5년제는 2033년 2월까지)"},
        "석사": {"기본상한": "입학 후 5년", "예외": "3년제 석사는 6년", "example": "2026년 3월 입학 → 기본상한 2031년 2월까지(3년제는 2032년 2월까지)"},
        "박사": {"기본상한": "입학 후 8년", "예외": "2년제 박사는 7년", "example": "2026년 3월 입학 → 기본상한 2034년 2월까지(2년제는 2033년 2월까지)"},
    }

    # 매뉴얼 맨 앞 "유의사항" 1번에 있는 문서 전체 공통 규칙(2026-09-15 재조사로 발견):
    # "국내에서 발급·제출하는 서류의 유효기간은 별도의 유효기간이 설정되지 않은 경우 발급일로부터
    # 3개월 이내"— D-2 전용 규정이 아니라 매뉴얼 전체에 적용되는 공통 규칙이라 stage1(D-2 챕터
    # 원문 미러)에는 없고, 여기 stage2에서 관련 서류에 표시해준다.
    DOC_VALIDITY_DEFAULT = "발급일로부터 3개월 이내(매뉴얼 공통 유의사항 1번 — 별도 유효기간이 없는 모든 국내발급서류에 적용)"

    # ── 서류별 발급 경로 (URL이 아닌 '메뉴 경로' 텍스트로 안내) ──
    documents = [
        {
            "name": "체류기간 연장허가 신청서(통합신청서)",
            "obtain_type": "government_form",
            "how_to_obtain": "하이코리아(hikorea.go.kr) → 정보광장 → 민원서식/업무매뉴얼 에서 서식 다운로드. 온라인 신청 시에는 하이코리아에서 바로 작성 가능",
            "fallback": "관할 출입국·외국인청 방문 시 현장 비치 서식 사용 가능",
        },
        {
            "name": "여권, 외국인등록증",
            "obtain_type": "self_held",
            "how_to_obtain": "본인 보관 서류. 분실·갱신이 필요하면 외국인등록증은 하이코리아 온라인 재발급 신청 또는 관할 출입국·외국인청 방문",
        },
        {
            "name": "수수료(체류기간연장허가 6만원)",
            "obtain_type": "online_payment_or_visit",
            "how_to_obtain": "하이코리아 온라인 신청 시 온라인 결제, 방문 신청 시 관할청 창구에서 납부",
        },
        {
            "name": "수료증명서, 지도교수 및 유학담당자 확인서",
            "obtain_type": "university_office",
            "how_to_obtain": "소속 대학 국제교류처(또는 유학생지원센터) 방문·이메일 요청. 법무부 지정 서식(지도교수 확인서)에 지도교수·유학담당자 서명 필요",
            "validity": DOC_VALIDITY_DEFAULT,
        },
        {
            # 원문(stage1) 표현 그대로: "학업을 정상적으로 수행하고 있음을 입증하는 서류
            # (예: 재학증명서, 성적증명서, 출석확인서 등)" — 예시 서류를 별도 배열로 명시.
            "name": "학업을 정상적으로 수행하고 있음을 입증하는 서류",
            "examples": ["재학증명서", "성적증명서", "출석확인서"],
            "obtain_type": "university_portal",
            "how_to_obtain": "재학 중인 대학교 포털 → 증명서 발급(제증명) 메뉴에서 온라인 발급 (대학마다 메뉴 명칭은 다를 수 있음)",
            "note": "학교가 유학생정보시스템에 이미 등록·확인되는 경우 제출 생략 가능 — 담당 출입국청에 사전 확인 권장",
            "validity": DOC_VALIDITY_DEFAULT,
        },
        {
            "name": "재정입증 서류(잔고증명서 등)",
            "obtain_type": "bank",
            "how_to_obtain": "본인 거래은행 앱(모바일뱅킹) 또는 영업점 창구에서 잔고증명서 발급",
            "note": "우수인증대학·인증대학 재학생(성적 C학점 이상, 고시국가/중점관리국가 국민 제외)은 제출 생략 가능",
            "validity": DOC_VALIDITY_DEFAULT + " — 신청 임박해서 미리 떼두면 만료돼서 다시 발급받아야 할 수 있으니 주의",
        },
        {
            # 원문(stage1): "체류지 입증서류(임대차계약서, 숙소제공 확인서, 체류기간 만료예고
            # 통지우편물, 공공요금 납부영수증, 기숙사비 영수증 등)" — "숙소제공 확인서"가 이전
            # 버전 how_to_obtain에서 누락돼 있었어서 이번에 바로잡음.
            "name": "체류지 입증서류",
            "examples": ["임대차계약서", "숙소제공 확인서", "체류기간 만료예고 통지우편물", "공공요금 납부영수증", "기숙사비 영수증"],
            "obtain_type": "mixed",
            "how_to_obtain": "예시 서류마다 발급 경로가 다름 — obtain_by_example 참고",
            "obtain_by_example": {
                "임대차계약서": "본인 보관 서류(계약 시 받은 원본) 확인. 분실 시 임대인 또는 공인중개사에게 사본 요청",
                "숙소제공 확인서": "기숙사·홈스테이 등 실제 거처를 제공하는 쪽(학교 기숙사 행정실 또는 임대인)에게 요청해 발급",
                "체류기간 만료예고 통지우편물": "출입국청에서 우편으로 보낸 것을 수령해 보관(별도 발급 절차 없음)",
                "공공요금 납부영수증": "본인 명의 전기·수도·가스 등 고지서 보관 또는 공급사 홈페이지/앱에서 재발급",
                "기숙사비 영수증": "학교 기숙사 행정실에서 발급",
            },
            "validity": DOC_VALIDITY_DEFAULT + "(단, 임대차계약서 등 원래 유효기간이 정해진 서류는 그 기간을 따름)",
        },
    ]

    university_tier_guidance = {
        "issue": "우수인증대학/인증대학/일반대학/비자심사강화대학 등급에 따라 필요 서류와 체류기간 상한이 달라짐",
        "why_not_automated": "법무부·교육부가 매년 발표하는 명단이라 실시간 크롤링 시 최신성 오류 위험이 있어, 자동 판정 대신 사용자가 직접 확인하도록 안내",
        "guidance_text": "재학 중인 대학의 인증 등급은 관할 출입국·외국인청에 문의하면 정확히 확인할 수 있습니다. 아래 관할청 연락처로 문의해 보세요.",
        "office_lookup_ref": "immigration_offices.json",
        "chatbot_question_policy": (
            "챗봇 대화에서는 대학 등급/성적을 직접 물어보지 않는다(B안, 보수적 기본값). "
            "eligibility_rules 중 institution_tier='일반대학' 규칙을 기본으로 항상 보여주고, "
            "'우수인증대학·인증대학 재학생은 재정입증 서류가 면제될 수 있으니 관할청에 등급을 확인해보라'는 안내 문구를 덧붙인다. "
            "이유: 사용자가 자기 학교의 인증 등급을 스스로 정확히 판단하기 어렵고, 잘못 응답 시 서류를 빠뜨릴 위험이 "
            "'일반대학 기준으로 서류를 더 준비하게 안내'하는 것보다 크기 때문(보수적 기본값)."
        ),
    }

    # 챗봇 질문 흐름에서 실제로 사용하는 조건 축(axis) 설계 — 사용자 확인(B안 등) 반영 결과.
    # 이 축들은 서로 독립적으로 조합(AND)되며, 각 축의 답변에 따라 eligibility_rules 중 해당하는
    # 항목들을 모아 최종 안내를 구성한다 (단일 규칙 매칭이 아니라 축별로 뽑은 뒤 합치는 구조).
    # 2026-09-15 업데이트: 실제 서비스는 로그인 시점에 이미 비자 종류·체류지 주소를 알고 있고,
    # 만료일이 다가오면 시스템이 먼저 알림을 보내 대화가 시작된다는 게 확인됨. 그래서 챗봇은
    # "이미 아는 값"은 다시 묻지 않고, "학기마다 바뀔 수 있어 로그인 시점 값을 못 믿는 값"만 묻는다.
    chatbot_flow_design = {
        "known_at_login": [
            {
                "field": "visa_subtype",
                "source": "회원가입/로그인 시 저장된 계정 정보",
                "why_not_asked_again": "가입 시 확정된 값이라 학기 중 바뀔 일이 거의 없음",
                "used_for": "eligibility_rules 중 visa_subtype 조건(연장 주기·상한) 자동 적용",
            },
            {
                "field": "address",
                "source": "회원가입 시 등록한 체류지 주소",
                "why_not_asked_again": "자주 안 바뀌므로 새로 고르게 하지 않고 confirmAddress에서 '이 주소 맞나요?'만 확인",
                "used_for": "office_lookup 관할청 매칭 — 맞으면 즉시 매칭, 아니면 광역→시/군/구 흐름으로 정정",
            },
        ],
        "axes": [
            {
                "axis": "class_schedule",
                "question": "야간 또는 주말 수업을 듣고 있나요?",
                "maps_to_rule_condition": "class_schedule",
                "why_asked_every_time": "학기마다 수강 시간표가 바뀔 수 있어 로그인 시점 값을 신뢰할 수 없음",
                "choices": ["예", "아니오", "모르겠어요"],
                "unknown_handling": (
                    "'모르겠어요'를 선택지로 제공한다(사용자 확인, 2026-09-15). 대학등급 때와 같은 보수적 원칙: "
                    "'해당 없음'으로 조용히 넘어가지 않고, night_class 제한 규칙 내용을 그대로 보여주면서 "
                    "'해당될 수 있으니 본인 시간표를 다시 확인해보라'는 경고 문구를 함께 띄운다. 즉 모르면 "
                    "'제한 대상일 수도 있다'고 가정하고 안내하는 쪽이 '문제 없다고 안내했는데 실제로는 걸리는' "
                    "경우보다 안전하다는 게 이유."
                ),
            },
            {
                "axis": "academic_status",
                "question": "현재 휴학 중인가요? (휴학 중이라면: 질병·사고 / 개인사정·학점미달)",
                "maps_to_rule_condition": "academic_status",
                "why_asked_every_time": "휴학 여부·사유도 학기마다 바뀌는 값",
                "choices": ["예", "아니오, 재학 중"],
                "follow_up": "휴학 사유(질병·사고 vs 개인사정)에 따라 restriction/exception 중 적용되는 문구가 달라짐",
                "unknown_handling": (
                    "'모르겠어요' 선택지를 두지 않는다 — 휴학 여부는 본인이 모를 수 없는 사실이라 class_schedule과 "
                    "달리 '모름' 케이스 자체가 현실적으로 없다고 보고 예/아니오만 제공한다."
                ),
            },
        ],
        "removed_axes": [
            {
                "axis": "institution_tier / gpa",
                "reason": "사용자 확인 결과 B안(보수적 기본값) 채택 — 질문에서 제외하고 일반대학 기준으로 통일 안내",
            }
        ],
        "removed_axes_still_used": (
            "'질문에서 뺐다'는 게 '규칙 데이터를 지웠다'는 뜻이 아니다. eligibility_rules 안에 tier_top/"
            "tier_general/tier_weak 세 규칙이 그대로 남아 있고, 각 규칙의 asked_in_chatbot=false, "
            "chatbot_usage 필드가 '이 규칙을 실제로 어떻게 쓰는지'를 설명한다. 예: tier_general은 질문 없이 "
            "모든 사용자에게 기본값으로 자동 적용되고, tier_top/tier_weak은 '~하면 서류가 줄 수도 있다'는 참고 "
            "문구로만 노출된다. 그러니 이전에 정리한 조건(축) 작업은 버려진 게 아니라, '질문할 조건'과 "
            "'질문 없이 내부적으로만 쓰는 조건'으로 역할이 나뉜 것뿐이다."
        ),
        "location_precision": (
            "시/군/구까지 정확히 매칭하되(office_lookup 참고), 로그인 시 이미 아는 주소를 confirmAddress에서 "
            "'맞나요?' 확인만 받는 1버튼 흐름을 기본으로 하고, 주소가 바뀐 경우에만 광역→시/군/구 2단계 선택으로 대체한다."
        ),
        "trigger": "사용자가 먼저 대화를 시작하는 게 아니라, 체류기간 만료 임박 시 시스템이 보낸 알림을 클릭해 대화가 시작됨을 가정",
        "confirmed_by_user_on": "2026-09-15",
        "content_vs_presentation_note": (
            "이 JSON(eligibility_rules의 explanation/example, restriction/exception, required_documents의 "
            "how_to_obtain/note 등)이 담고 있는 건 '어떤 조건일 때 무슨 내용을 보여줘야 하는지'와 그 내용 자체(사실 "
            "관계)다 — 이건 도메인 지식이 필요한 부분이라 데이터/기획 담당(형)이 정하고 UX/UI팀에 넘겨주는 게 맞다. "
            "반대로 화면 레이아웃, 버튼이냐 드롭다운이냐 카드형이냐 챗봇 대화형이냐, 색상·타이포·애니메이션 같은 "
            "'어떻게 보여줄지'는 UX/UI팀의 몫이다. 이 JSON의 explanation/example 문장들은 최종 화면 카피가 아니라 "
            "'이런 내용이 빠짐없이 들어가야 한다'는 참고 초안이므로, UX/UI팀이 실제 화면에 맞게 문장을 다듬거나 "
            "길이를 줄이는 건 자연스러운 일이고 막을 이유가 없다 — 다만 조건(condition)과 사실관계(문서명, 상한 "
            "연수, 관할청 정보 등)는 바뀌면 안 되므로, UX팀이 화면을 새로 짜더라도 이 JSON의 필드를 그대로 참조해서 "
            "채우도록(하드코딩으로 다시 베껴 쓰지 않도록) 안내하는 게 중요하다. 즉 형이 할 일은 '정확한 데이터 구조 "
            "+ 읽었을 때 오해 없을 정도의 초안 문구'까지이고, 화면에 맞는 최종 다듬기는 UX/UI팀 몫이다."
        ),
    }

    # 대학 인증등급 명단, 관할청 현황(offices) 등은 매년/수시로 바뀌는 데이터인데,
    # 이걸 언제·어떻게 갱신할지에 대한 운영 계획은 사용자 확인 결과 지금 단계에서는 보류하기로 함.
    operational_data_refresh = {
        "issue": "university_tier_guidance가 참조하는 대학 인증등급 명단, office_lookup의 관할청 현황(immigration_offices.json) 모두 법무부/교육부가 매년 또는 수시로 갱신하는 데이터",
        "decision": "지금 단계(공모전 MVP)에서는 갱신 주기·운영 계획을 정하지 않고 보류",
        "reason": "실제 서비스 운영 단계에 들어갔을 때 다시 논의하는 게 맞다고 판단 — 지금 정해봐야 운영 전이라 검증할 방법이 없음",
        "decided_by_user_on": "2026-09-15",
        "future_consideration": "재논의 시 고려할 것: immigration_offices.json은 공공데이터포털 파일데이터라 정기적 재수집(예: 학기당 1회) 필요, 대학 인증등급 명단은 법무부 고시를 모니터링해야 함",
    }

    expiration_date_sourcing = {
        "issue": "서비스의 핵심 트리거(체류기간 만료 임박 알림)를 위해 개인별 정확한 만료일을 자동으로 확보할 수 있는지 조사한 결과",
        "official_api": (
            "법무부/data.go.kr의 공개 오픈API(예: 본 서비스가 쓰는 출입국관리사무소 현황)는 통계·기관현황 등 "
            "비개인 데이터만 제공하며, 개인별 체류기간 만료일을 조회하는 공식 오픈API는 확인되지 않음."
        ),
        "private_broker_option": (
            "CODEF(코드에프, 헥토데이터)라는 민간 구독형 데이터 중개 플랫폼이 '하이코리아 체류만료일 조회' 상품을 "
            "제공하는 것으로 확인됨. 이 방식은 사용자의 하이코리아 로그인 자격증명(아이디/비밀번호)을 서비스가 "
            "위탁받아 CODEF를 통해 대신 로그인해서 마이페이지 정보를 스크레이핑하는 구조(Connected ID 방식)로, "
            "공식 정부 API가 아니라 유료 민간 서비스이며 개인정보 위수탁 계약·강화된 보안 인증이 필요함."
        ),
        "recommended_for_mvp": (
            "공모전 MVP 단계에서는 회원가입/온보딩 시 사용자가 외국인등록증에 적힌 만료일을 직접 입력하도록 하는 "
            "self-report 방식을 권장. 비용·보안 부담이 없고 즉시 구현 가능하나, 사용자가 갱신 후 값을 다시 "
            "입력해주지 않으면 날짜가 낡을 수 있어 만료일 재확인을 유도하는 UX가 필요함."
        ),
        "jurisdiction_lookup_is_different": (
            "반면 관할청(jurisdiction) 매칭은 정부의 개인정보 접근이 전혀 필요 없음 — 회원가입 시 수집하는 "
            "체류지 주소만 있으면 office_lookup.py로 즉시 자체 계산 가능. 이 데모가 보여주는 confirmAddress 흐름이 이에 해당함."
        ),
        "decision": "사용자 직접 입력(self-report) 방식 채택 — 온보딩 시 외국인등록증 만료일을 직접 입력받는다. CODEF 등 민간 스크래핑 연동은 채택하지 않음.",
        "how_it_is_used": (
            "서비스는 만료일을 계산하거나 추정하지 않는다. 사용자가 입력한 만료일 값을 그대로 저장해두고, "
            "'오늘 날짜 - 저장된 만료일'의 단순 뺄셈으로 D-day(예: D-30)만 표시한다. 계산이 필요한 대상은 "
            "'만료일이 언제인지'가 아니라 '만료일까지 며칠 남았는지'뿐이다."
        ),
        "application_window_note": (
            "'체류기간 만료 몇 개월 전부터 신청 가능한지'는 매뉴얼 전체(798페이지)를 재검색했지만 D-2에 대한 "
            "명시적 규정을 찾지 못해 이 필드에는 포함하지 않는다(사용자 확인 후 보류 결정, 2026-09-15). "
            "매뉴얼에서 확인된 건 선원취업(E-10) 재고용 연장 '만료일 2개월 전부터', 결혼이민(F-6) 자녀 성년 전 "
            "자격변경 '성년 되기 4개월 전부터' 뿐으로, 둘 다 D-2와 무관한 별개 규정이다. 추후 하이코리아(1345) "
            "등으로 확인되면 이 필드에 추가한다."
        ),
        "decided_by_user_on": "2026-09-15",
        "researched_at": "2026-09-15",
    }

    # 서류 유효기간 공통규칙 — 매뉴얼 맨 앞 "유의사항" 1번 원문에서 확인(2026-09-15 재조사).
    document_validity_common_rule = {
        "rule": DOC_VALIDITY_DEFAULT,
        "source": "매뉴얼 공통 유의사항 1번(문서 맨 앞, D-2 전용이 아니라 전체 매뉴얼에 적용되는 공통 규정)",
        "quote": "본 매뉴얼에서 주민등록등본 등 국내에서 발급·제출하는 서류의 유효기간은 별도의 유효기간이 설정되지 않은 경우 발급일로부터 3개월 이내임.",
        "why_not_in_stage1": "stage1은 D-2 챕터(42~44p)만 스코프로 하는 원문 미러라서, 문서 맨 앞의 전체 공통 유의사항은 포함되지 않았다. 대신 여기 stage2에서 관련 required_documents 항목에 반영한다.",
    }

    output = {
        "visa_code": "D-2",
        "visa_name": "유학",
        "topic": "체류기간 연장허가",
        "expiration_date_sourcing": expiration_date_sourcing,
        "operational_data_refresh": operational_data_refresh,
        "process_overview": {
            "summary": [f"{s['step']}. {s['name']}" for s in process["process"]],
            "detail_ref": "process_overview_easylaw.json",
            "penalty_note": process["cancellation_and_penalty"]["penalty_for_overstay"]["quote"],
        },
        "eligibility_rules": rules,
        "chatbot_flow_design": chatbot_flow_design,
        "hard_caps_by_degree": hard_caps_by_degree,
        "document_validity_common_rule": document_validity_common_rule,
        "required_documents": documents,
        "university_tier_guidance": university_tier_guidance,
        "office_lookup": {
            "source_file": "immigration_offices.json",
            "matching_module": "office_lookup.py",
            "total_offices": offices["dataset"]["total_count"],
            # 사용자 요청으로 참조(파일명)만이 아니라 실제 기관명·주소·전화번호를 이 파일 안에도
            # 그대로 embed — office_lookup.py가 매칭할 대상 데이터 그 자체이기도 하다.
            "offices": offices["offices"],
            "full_region_index": full_region_index,
            "full_region_index_note": (
                "전국 시/도 16개 + 시/군/구 228개 전체를 각각 office_lookup.py의 "
                "find_offices_two_step()에 실제로 돌려서 나온 결과를 미리 계산해 둔 표다(2026-09-16 추가). "
                "이제 UI가 굳이 이 로직을 다시 호출하거나 16개 시/도 예시 버튼만으로 제한할 필요 없이, "
                "이 표를 그대로 드롭다운/자동완성의 값 목록이나 조회 캐시로 써도 된다. "
                "다만 이 228개 목록 자체는 행정표준코드관리시스템(code.go.kr) 같은 공식 기계판독 데이터를 "
                "한 줄씩 기계적으로 대조해서 만든 게 아니라, 안정적인 일반 지식 + 부분적인 웹 검색 교차검증"
                "(전남광주통합특별시가 2026-07-01에 실제 출범했고 5개 시·5개 구·17개 군 그대로 유지된다는 것, "
                "반면 같이 추진되던 대전충남통합·대구경북통합은 둘 다 아직 출범 전이라 대전/충남, 대구/경북을 "
                "별도로 둔 게 맞다는 것을 직접 확인함)으로 사람이 정리한 것이다. 총합이 잘 알려진 228개와 "
                "맞아떨어지는 것으로 자체 검산은 했지만, 프로덕션 투입 전에는 공식 코드표로 최종 한 번 더 "
                "기계적으로 대조하는 걸 권장한다. 이 표를 만드는 전수 실행 과정에서 실제로 대구·대전·울산 "
                "소속 19개 구/군이 매칭 실패로 잘못 나오는 알고리즘 버그를 발견해 find_offices_two_step() "
                "자체를 고쳤다 — 자세한 내용은 아래 known_blanket_coverage_bug_and_fix 참고."
            ),
            "known_blanket_coverage_bug_and_fix": (
                "2026-09-16, 전국 228개 시/군/구 전수 감사 중 발견: 대구광역시·대전광역시·울산광역시는 "
                "관할구역 텍스트가 괄호 하나 없이 그냥 '대구광역시'처럼 통째로 한 줄로만 적혀 있다 "
                "(하위 구/군을 아무도 따로 떼어가지 않았다는 뜻). 그런데 기존 find_offices_two_step()의 "
                "fallback은 '도(...제외)'처럼 괄호가 붙은 경우만 '포괄 담당 사무소'로 인정하도록 짜여 있어서, "
                "괄호가 아예 없는 이 케이스는 fallback도 못 타고 매칭 실패로 빠졌다(대구 9개 구/군, 대전 5개, "
                "울산 5개, 총 19곳). 고친 방법: '(...제외)' 유무와 상관없이, 괄호를 지우고 남은 포함 목록 "
                "안에 그 도(道) 이름이 통째로 한 항목으로 들어있으면 똑같이 '포괄 담당 사무소'로 인정하도록 "
                "일반화했다 — 예전 정규식 fallback이 하던 일도 그대로 포함되면서 괄호 없는 케이스까지 한 번에 "
                "커버된다. office_lookup.py를 그대로 갖다 쓰는 백엔드는 이 최신 버전을 반영해야 한다."
            ),
            "how_to_use": (
                "2단계 매칭: (1) 사용자에게 offices[].지역 필드에서 나온 5개 광역 존(예: 서울/경기·인천/충청/전라·광주/경상/제주 등, "
                "데이터 자체에서 뽑아낸 값이므로 하드코딩된 '전라남도' 같은 명칭에 의존하지 않음) 중 하나를 먼저 고르게 해 후보를 좁히고, "
                "(2) 그 후보 안에서만 시/군/구 이름을 관할구역 텍스트와 정밀 매칭한다. "
                "관할구역 텍스트는 자유서식이며 '(김해시, 밀양시, 양산시 제외)'처럼 제외 조건이 괄호로 섞여 있어 "
                "단순 포함검사(city in text)는 오탐을 낸다 — 예: '김해시'를 naive하게 검색하면 정작 관할이 아닌 창원출입국·외국인사무소가 잘못 걸림. "
                "office_lookup.py의 parse_jurisdiction()으로 포함/제외 지역을 분리해 제외 목록에 걸리면 무조건 배제한 뒤 포함 목록만 매칭한다(find_offices). "
                "(2)단계에서 시/군/구 이름이 어느 관할구역 텍스트에도 직접 등장하지 않는 경우(예: '창원시'), 그 광역 명칭 바로 뒤에 "
                "'(...제외)'가 붙은 사무소를 '나머지 지역을 전담하는 본청'으로 자동 귀속시킨다(find_offices_two_step의 fallback) — "
                "이 fallback 덕분에 전국 시/군/구 전체 목록(약 228개)을 별도로 관리하지 않고도 이 54개 관할구역 데이터만으로 결론을 낼 수 있다. "
                "다만 행정구역 명칭은 실제로 개편될 수 있으므로(예: 2026-07-07 데이터 기준 '전라남도' 대신 '전남광주통합특별시'가 쓰인 사례 확인), "
                "자동 매칭 결과를 최종 확정으로 보여주지 말고 반드시 후보 1~2곳을 사용자에게 시각적으로 보여주고 확인받는 절차(화면 표시)를 두는 것을 권장한다. "
                "공항·보호소 등 특수목적 지점(known_special_purpose_entries 참고)은 일반 매칭에서 제외 권장."
            ),
            "recommended_ui_selection_unit": (
                "위 '5개 광역 존'(지역 필드: 서울·인천·경기 / 부산·경남 / 광주·전라·제주 / 대구·경북·강원 / "
                "대전·충청)은 data.go.kr 원본 데이터셋이 관리 편의상 묶어놓은 값일 뿐, 사용자가 자기 주소를 "
                "떠올릴 때 쓰는 단위(시/도)와는 다르다 — '대구, 경북, 강원'처럼 서로 멀리 떨어진 지역을 한 "
                "선택지로 묶으면 사용자가 자신이 어디를 골라야 할지 직관적으로 알기 어렵다는 점이 2026-09-15 "
                "데모 사용자 피드백으로 확인됨. 그래서 화면에는 이 '지역' 필드를 그대로 노출하지 말고, 회원가입 "
                "때 이미 입력받는 실제 시/도(예: 경상남도, 강원특별자치도 등 약 16개)를 선택지로 쓰는 것을 "
                "권장한다."
            ),
            "known_naming_drift_bug_and_fix": (
                "2026-09-16 사용자 리포트로 실제 매칭 오류를 하나 발견했다: 강원특별자치도(신 명칭)로 검색하면 "
                "양주출입국·외국인사무소가 후보에서 통째로 빠진다 — 이 레코드는 철원군 관할을 '강원도'(구 명칭)로 "
                "적어놨기 때문이다. 그 결과 남은 후보(춘천 계열)는 철원군을 명시적으로 '제외'하고 있어서 안전장치가 "
                "발동해 '매칭 실패'로 잘못 끝났다(실제로는 양주가 담당하는데도). 원인은 제외조건 처리(2단계)가 "
                "아니라 후보 좁히기(1단계)가 province 문자열을 완전 일치로만 찾았다는 데 있었다. office_lookup.py에 "
                "province_root(province) 함수를 추가해 '도/특별자치도/광역시/특별시' 같은 접미사를 뗀 핵심 지명"
                "(예: '강원특별자치도'→'강원')으로 후보를 찾도록 고쳤다 — 이러면 레코드가 신/구 명칭 중 무엇을 "
                "썼든 같은 지역으로 인식된다. find_offices_two_step을 그대로 쓰는 백엔드는 이 수정을 반드시 "
                "반영해야 하며, 54개 레코드 중 이런 신/구 명칭 혼용이 다른 곳에도 더 있을 수 있으므로(전수 검증은 "
                "아직 안 함) 실제 서비스 전에 전체 레코드를 한 번 더 사람이 검토하는 걸 권장한다."
            ),
        },
        "sources": [
            {"id": "manual", "document": "외국인체류 안내매뉴얼(260901 체류민원 자격별 안내 매뉴얼)", "publisher": "법무부 출입국·외국인정책본부", "version": "2026.9", "pages": stage1["source"]["pages_1_indexed"]},
            {"id": "process", "document": "체류기간 연장허가 절차", "publisher": "찾기쉬운 생활법령정보(easylaw.go.kr)", "url": process["source"]["url"], "retrieved_at": process["source"]["retrieved_at"]},
            {"id": "offices", "document": "출입국 관리사무소 현황", "publisher": "법무부 / data.go.kr", "dataset_id": "15040715", "retrieved_at": offices["dataset"]["retrieved_at"]},
        ],
        "disclaimer": (
            stage1["disclaimer"] + " 대학 인증등급 및 서류 발급경로 안내는 참고용이며 기관별 절차가 변경될 수 있으니 "
            "최종 확인은 관할 출입국·외국인청 또는 하이코리아(hikorea.go.kr)를 통해 진행하시기 바랍니다."
        ),
        "generated_at": datetime.date.today().isoformat(),
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print('저장 완료:', OUT)


if __name__ == '__main__':
    main()
