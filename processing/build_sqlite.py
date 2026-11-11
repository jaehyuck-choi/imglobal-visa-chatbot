# -*- coding: utf-8 -*-
"""
3단계: D2_stay_extension_stage2.json(2단계 산출물)을 그대로 옮기지 않고, 실제 백엔드가
SQL로 조회하기 좋은 형태로 정규화해서 SQLite 데이터베이스로 만든다.

설계 원칙(왜 이렇게 나눴는지):
  - "표(테이블)"로 관리하는 게 자연스러운 데이터(관할청, 지역, 세부전공, 서류)는
    각각 진짜 SQL 테이블로 정규화한다 -> WHERE/JOIN으로 바로 조회 가능.
  - "규칙"(eligibility_rules)은 룰마다 필드 구성 자체가 다르다(cycle_degree는 cycle/explanation/
    example, night_class는 restriction/exception, tier_top은 max_stay_per_grant/documents_waived...).
    이런 걸 억지로 고정된 컬럼에 맞추면 rule마다 대부분 컬럼이 NULL인 지저분한 테이블이 되고,
    새 규칙 하나 추가할 때마다 스키마를 또 바꿔야 한다. 그래서 rule의 "본문"은 JSON 문자열
    그대로 한 컬럼(payload_json)에 저장하고, 조회에 실제로 필요한 값(asked_in_chatbot 등)만
    별도 컬럼으로 꺼내둔다 - 실무에서도 이렇게 자주 한다.
  - "왜 이렇게 설계했는지"를 설명하는 문서성 필드(university_tier_guidance,
    operational_data_refresh, full_region_index_note 등)는 챗봇이 실행 중에 SQL로 조회할
    필요가 없는 사람(팀원/후임 개발자)용 참고 자료다. 테이블 하나(service_notes)에 key-value로
    몰아넣어서, 나중에 "왜 이렇게 했더라?"가 궁금할 때 DB 하나만 열어도 다 나오게 한다.

테이블 목록:
  offices               (54행)  관할청 원본 정보
  regions               (228행) 전국 시/도+시/군/구
  region_offices        (다대다) 어떤 지역을 어떤 관할청(들)이 담당하는지 + 대표 여부
  visa_subtypes         (8행)   D-2 세부전공 코드/이름/적용 규칙 그룹
  eligibility_rules     (8행)   조건-결과 규칙 (본문은 JSON)
  required_documents    (7행)   준비 서류 (본문은 JSON)
  hard_caps_by_degree   (4행)   학위과정별 체류기간 절대 상한
  service_notes         (key-value) 설계 근거/한계 등 문서성 기록
"""
import json
import sqlite3
import os

BASE = os.path.dirname(os.path.abspath(__file__))
STAGE2 = f'{BASE}/D2_stay_extension_stage2.json'
DB_PATH = f'{BASE}/d2_chatbot.db'

SCHEMA = """
CREATE TABLE offices (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL UNIQUE,   -- 기관명
    jurisdiction_text     TEXT NOT NULL,          -- 관할구역 원문 (자유서식, 참고용 그대로 보존)
    address               TEXT NOT NULL,
    phone                 TEXT,
    source_region_cluster TEXT                    -- 원본 '지역' 필드(5개 클러스터). UI 노출 금지, 참고용.
);

CREATE TABLE regions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    province TEXT NOT NULL,   -- 시/도 (16개 중 하나)
    city     TEXT NOT NULL,   -- 시/군/구
    UNIQUE (province, city)
);

CREATE TABLE region_offices (
    region_id  INTEGER NOT NULL REFERENCES regions(id),
    office_id  INTEGER NOT NULL REFERENCES offices(id),
    is_primary INTEGER NOT NULL DEFAULT 0,  -- 화면에 먼저 보여줄 대표 관할청이면 1
    PRIMARY KEY (region_id, office_id)
);

CREATE TABLE visa_subtypes (
    code         TEXT PRIMARY KEY,   -- 'D-2-1'
    label        TEXT NOT NULL,      -- '전문학사과정'
    rule_group   TEXT NOT NULL,      -- eligibility_rules.id 중 어떤 cycle 규칙을 적용받는지
    degree_level TEXT                -- '전문학사'/'학사'/'석사'/'박사' (학위과정 아니면 NULL)
);

CREATE TABLE eligibility_rules (
    id               TEXT PRIMARY KEY,        -- 'cycle_degree', 'tier_top' 등
    asked_in_chatbot INTEGER NOT NULL,        -- 0/1 - 챗봇이 사용자에게 직접 묻는 조건인지
    payload_json     TEXT NOT NULL            -- condition/cycle/explanation/example/... 원본 그대로
);

CREATE TABLE required_documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    payload_json TEXT NOT NULL   -- examples/how_to_obtain/obtain_by_example/fallback/note/validity
);

CREATE TABLE hard_caps_by_degree (
    degree_level TEXT PRIMARY KEY,  -- '전문학사'/'학사'/'석사'/'박사'
    base_cap     TEXT NOT NULL,
    exception    TEXT,
    example      TEXT
);

CREATE TABLE service_notes (
    note_key   TEXT PRIMARY KEY,
    note_value TEXT NOT NULL
);
"""


def main():
    with open(STAGE2, encoding='utf-8') as f:
        data = json.load(f)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)  # 매번 처음부터 새로 만든다 (증분 업데이트 아님 - 소스는 항상 JSON)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA)

    # ── offices ──
    office_id_by_name = {}
    for o in data['office_lookup']['offices']:
        cur = conn.execute(
            "INSERT INTO offices (name, jurisdiction_text, address, phone, source_region_cluster) "
            "VALUES (?, ?, ?, ?, ?)",
            (o['기관명'], o['관할구역'], o['주소'], o.get('전화'), o.get('지역')),
        )
        office_id_by_name[o['기관명']] = cur.lastrowid

    # ── regions + region_offices (full_region_index를 그대로 적재) ──
    for row in data['office_lookup']['full_region_index']:
        cur = conn.execute(
            "INSERT INTO regions (province, city) VALUES (?, ?)",
            (row['시도'], row['시군구']),
        )
        region_id = cur.lastrowid
        for idx, office_name in enumerate(row['관할청_전체']):
            conn.execute(
                "INSERT INTO region_offices (region_id, office_id, is_primary) VALUES (?, ?, ?)",
                (region_id, office_id_by_name[office_name], 1 if idx == 0 else 0),
            )

    # ── visa_subtypes ── (stage2.json 자체엔 subtype 카탈로그가 없고 데모 JS에만 있었으므로 여기서 정의)
    subtype_catalog = [
        ('D-2-1', '전문학사과정', 'cycle_degree', '전문학사'),
        ('D-2-2', '학사과정', 'cycle_degree', '학사'),
        ('D-2-3', '석사과정', 'cycle_degree', '석사'),
        ('D-2-4', '박사과정', 'cycle_degree', '박사'),
        ('D-2-7', '연구과정 등', 'cycle_degree', '박사'),
        ('D-2-5', '연구유학', 'cycle_research', None),
        ('D-2-6', '교환학생', 'cycle_exchange', None),
        ('D-2-8', '방문학생', 'cycle_exchange', None),
    ]
    conn.executemany(
        "INSERT INTO visa_subtypes (code, label, rule_group, degree_level) VALUES (?, ?, ?, ?)",
        subtype_catalog,
    )

    # ── eligibility_rules ──
    for r in data['eligibility_rules']:
        payload = {k: v for k, v in r.items() if k not in ('id', 'asked_in_chatbot')}
        conn.execute(
            "INSERT INTO eligibility_rules (id, asked_in_chatbot, payload_json) VALUES (?, ?, ?)",
            (r['id'], 1 if r.get('asked_in_chatbot') else 0, json.dumps(payload, ensure_ascii=False)),
        )

    # ── required_documents ──
    for d in data['required_documents']:
        payload = {k: v for k, v in d.items() if k != 'name'}
        conn.execute(
            "INSERT INTO required_documents (name, payload_json) VALUES (?, ?)",
            (d['name'], json.dumps(payload, ensure_ascii=False)),
        )

    # ── hard_caps_by_degree ──
    for level, cap in data['hard_caps_by_degree'].items():
        if level == '설명':
            continue
        conn.execute(
            "INSERT INTO hard_caps_by_degree (degree_level, base_cap, exception, example) VALUES (?, ?, ?, ?)",
            (level, cap.get('기본상한'), cap.get('예외'), cap.get('example')),
        )

    # ── service_notes (문서성 필드 전부 key-value로 보존) ──
    notes = {
        'hard_caps_설명': data['hard_caps_by_degree'].get('설명'),
        'expiration_date_sourcing': json.dumps(data['expiration_date_sourcing'], ensure_ascii=False),
        'operational_data_refresh': json.dumps(data['operational_data_refresh'], ensure_ascii=False),
        'university_tier_guidance': json.dumps(data['university_tier_guidance'], ensure_ascii=False),
        'chatbot_flow_design': json.dumps(data['chatbot_flow_design'], ensure_ascii=False),
        'document_validity_common_rule': json.dumps(data['document_validity_common_rule'], ensure_ascii=False),
        'office_lookup_how_to_use': data['office_lookup']['how_to_use'],
        'office_lookup_recommended_ui_selection_unit': data['office_lookup']['recommended_ui_selection_unit'],
        'office_lookup_full_region_index_note': data['office_lookup']['full_region_index_note'],
        'office_lookup_known_naming_drift_bug_and_fix': data['office_lookup']['known_naming_drift_bug_and_fix'],
        'office_lookup_known_blanket_coverage_bug_and_fix': data['office_lookup']['known_blanket_coverage_bug_and_fix'],
        'disclaimer': data['disclaimer'],
        'generated_at': data['generated_at'],
    }
    conn.executemany(
        "INSERT INTO service_notes (note_key, note_value) VALUES (?, ?)",
        list(notes.items()),
    )

    conn.commit()
    return conn


if __name__ == '__main__':
    conn = main()
    print('저장 완료:', DB_PATH)
    conn.close()
