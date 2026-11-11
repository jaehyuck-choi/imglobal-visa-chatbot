# -*- coding: utf-8 -*-
"""
E-9(비전문취업) '체류기간연장허가' 섹션을 D2와 동일한 방법론(PDF 원문 좌표 기반 구조화 추출)으로
1차 원문 그대로 뽑아낸다. extract_d2_stay_extension.py와 같은 소스 PDF, 같은 헬퍼 함수를 재사용하되,
아래 한 가지를 고쳤다:

  D2는 대상 라벨('체류기간연장허가')이 여러 페이지에 걸쳐 매 페이지 좌측 라벨란에 다시 인쇄돼서
  기존 extract_scoped_lines()가 페이지 단위로만 라벨을 찾아도 문제가 없었다. 그런데 E-9 챕터를
  직접 좌표 단위로 까보니(디버그로 원문 라인을 전부 출력해서 확인), '체류기간연장허가' 섹션이
  page327(1-idx)에서 시작해 page328(1-idx) 상단(윗부분, '3.구직신청자특례'의 나머지 - 나.허가기간/
  다.제출서류 포함)까지 이어지는데, 정작 page328에는 '체류기간연장허가'라는 라벨이 좌측에
  다시 인쇄되지 않는다(대신 그 페이지는 곧바로 '재입국허가' 라벨로 시작). 기존 코드처럼 "이 페이지에
  target_label이 있는가"만 페이지별로 독립적으로 판정하면 이 이어지는 내용을 통째로 놓친다.

  그래서 extract_scoped_lines_v2()는 라벨 상태를 페이지 경계를 넘어 이월(carry-over)한다:
  이번 페이지에서 첫 라벨이 나타나기 전 구간은 '이전 페이지 마지막 라벨'이 계속되는 것으로 보고,
  라벨이 아예 하나도 없는 페이지는 페이지 전체가 이전 라벨의 연속으로 처리한다.
  (D2는 애초에 라벨이 매 페이지 다시 찍혀 있어서 이 변경으로 결과가 바뀌지 않는다 - 안전한 일반화.)
"""
import importlib.util
import pdfplumber
import json
import re
import datetime

# D2.py의 헬퍼(find_chapter_page_range, get_label_runs, body_words,
# group_lines, classify, build_tree, nodes_to_json)를 그대로 재사용
import os as _os
_spec = importlib.util.spec_from_file_location(
    'd2ext', _os.path.join(_os.path.dirname(__file__), 'D2.py')
)
d2ext = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(d2ext)

SRC = d2ext.SRC
OUT = '../data/E9_stay_extension_stage1.json'

VISA_BOOKMARK = '비전문취업(E-9)'
NEXT_BOOKMARK = '선원취업(E-10)'
TARGET_LABEL = '체류기간연장허가'
FOOTER_TOP_MIN = d2ext.FOOTER_TOP_MIN

# D2는 최상위 항목이 '가./나./다.'(한글 한 글자+점)라서 기존 classify()가 이것만 depth_rank=0으로
# 인식했다. E-9의 이 섹션은 최상위가 '1./2./3.'(아라비아 숫자+점)이라 기존 classify()로는
# 못 알아채고 직전 노드에 이어붙는 줄로 잘못 처리됐다(실제로 확인됨). classify()에 아라비아 숫자
# 패턴을 추가해서 같은 depth_rank=0(최상위)으로 인식하도록 확장한다.
MARKER_ARABIC = re.compile(r'^(\d+)\.\s*(.+)$')
_orig_classify = d2ext.classify


def _classify_ext(line_text):
    m = MARKER_ARABIC.match(line_text)
    if m:
        return ('arabic', 0, f"{m.group(1)}. {m.group(2).strip()}")
    # 이 섹션의 실제 위계는 '1./2./3.'(최상위) 안에 '가./나./다.'가 한 단계 아래로 들어가는 구조
    # (D2는 반대로 가/나/다 자체가 최상위라서 원본 classify()는 가/나/다를 depth_rank=0으로 봄).
    # 그래서 여기서는 가/나/다만 한 단계(depth_rank=1) 낮춰서 1/2/3의 자식이 되게 한다.
    m2 = d2ext.MARKER_GANADA.match(line_text)
    if m2 and len(m2.group(1)) == 1:
        return ('ganada', 1, f"{m2.group(1)}. {m2.group(2).strip()}")
    return _orig_classify(line_text)


d2ext.classify = _classify_ext


def extract_scoped_lines_v2(pdf, start_page, end_page, target_label):
    scoped = []
    current_label = None
    for pi in range(start_page, end_page if end_page is not None else start_page + 1):
        page = pdf.pages[pi]
        runs = d2ext.get_label_runs(page)  # 이미 top 오름차순
        lines = d2ext.group_lines(d2ext.body_words(page))

        boundaries = []
        prev_top, prev_label = 0, current_label
        for (top, label) in runs:
            boundaries.append((prev_top, top, prev_label))
            prev_top, prev_label = top, label
        boundaries.append((prev_top, FOOTER_TOP_MIN, prev_label))

        for ln in lines:
            for lo, hi, label in boundaries:
                if lo <= ln['top'] < hi:
                    if label == target_label:
                        scoped.append((pi, ln['x0'], ln['top'], ln['text'], ln['is_example']))
                    break

        if runs:
            current_label = runs[-1][1]
    return scoped


def main():
    start_page, end_page = d2ext.find_chapter_page_range(SRC, VISA_BOOKMARK, NEXT_BOOKMARK)
    print(f'E-9 챕터 페이지 범위(0-indexed): {start_page} ~ {end_page} (1-indexed {start_page+1} ~ {end_page})')

    pdf = pdfplumber.open(SRC)
    scoped = extract_scoped_lines_v2(pdf, start_page, end_page, TARGET_LABEL)
    pages_used = sorted(set(pi + 1 for pi, *_ in scoped))
    print(f'"{TARGET_LABEL}" 라벨 구간에서 추출된 줄 수: {len(scoped)}, 사용된 페이지: {pages_used}')

    tree = d2ext.build_tree(scoped)
    content = d2ext.nodes_to_json(tree)

    # 알려진 경계 오차 보정: 좌측 라벨란의 '재입국허가' 인쇄 위치(top)가 실제 본문에서
    # 주제가 바뀌는 지점보다 살짝(6pt, 한 줄 미만) 늦게 찍혀 있어서, 라벨 top을 그대로 경계로
    # 쓰면 다음 항목('1. 재입국허가 면제 제도 시행...')의 첫 줄이 한 줄 섞여 들어온다.
    # (직접 원문 좌표를 찍어서 확인한 값이며, PDF가 바뀌면 이 보정도 다시 확인해야 함.)
    trimmed = []
    for node in content:
        if isinstance(node.get('text'), str) and '재입국허가' in node['text'] and node['text'].lstrip().startswith(('1.', '２.')):
            print(f"[경계 보정] 다음 항목으로 새는 줄 제외: {node['text'][:40]}...")
            continue
        trimmed.append(node)
    content = trimmed

    output = {
        'visa_code': 'E-9',
        'visa_name': '비전문취업',
        'section': '체류기간 연장허가',
        'content': content,
        'source': {
            'document': '외국인체류 안내매뉴얼 (260901 체류민원 자격별 안내 매뉴얼)',
            'file': SRC.split('/')[-1],
            'pages_1_indexed': pages_used,
            'extracted_at': datetime.date.today().isoformat(),
        },
        'note': (
            '팀원이 이미 전체 E-9 12개 절차를 수집한 e-9_processed.json이 있지만, 그 자료는 아직 '
            '매뉴얼 원문을 기계적으로 파싱만 한 단계라 D2의 eligibility_rules처럼 해석·정제되지 '
            '않았음. 이 파일은 그 팀원 자료를 다시 가공하는 대신, D2 stage1을 만들 때 쓴 것과 '
            '완전히 동일한 방법(PDF 좌표 기반 구조화 추출)으로 "체류기간 연장허가" 섹션만 원문 '
            '그대로 다시 뽑아본 결과 - D2 stage1과 같은 레벨의 1차 자료.'
        ),
        'disclaimer': (
            '본 자료는 매뉴얼 원문을 구조화한 참고용 데이터이며, 출입국·외국인청(사무소·출장소)장은 '
            '심사를 위해 특히 필요하다고 인정되는 때에는 제출서류를 가감할 수 있고 허가 여부는 '
            '심사에 의해 결정될 수 있습니다. 실제 신청 전 관할 출입국·외국인관서 확인이 필요합니다.'
        ),
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print('저장 완료:', OUT)
    return output


if __name__ == '__main__':
    main()
