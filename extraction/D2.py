"""
1단계: D-2(유학) 비자 '체류기간 연장허가' 섹션(가.기본원칙/나.세부심사기준/다.제출서류)을
PDF 원문 레이아웃 그대로 구조화된 JSON으로 추출한다.

방법론 요약 (사용자와 논의한 내용):
  1) pypdf의 PDF 북마크(outline)로 D-2 챕터의 정확한 페이지 범위를 자동으로 찾는다
     (하드코딩 대신, 문서가 바뀌어도 재사용 가능하게).
  2) pdfplumber로 단어 단위 좌표(x0, top)를 뽑아 좌측 라벨 컬럼(체류자격변경허가/체류기간연장허가/...),
     '목차' 바로가기, 페이지 하단 쪽번호 같은 페이지 부속 요소를 제거한다.
  3) 좌측 라벨이 바뀌는 지점(top 좌표)을 추적해 '체류기간연장허가' 라벨이 적용되는
     본문 영역만 정확히 스코프를 잘라낸다.
  4) 남은 본문 단어를 줄 단위로 묶되, 인접 단어 사이 간격(gap)이 실제 띄어쓰기인지
     (문서 특성상 숫자+단위가 낱글자 단위로 쪼개져 추출되는 경우가 많음) 판별해 재조합한다.
  5) 줄의 선행 마커(가./나./다., ❍, -, ※, * 등)와 들여쓰기(x0)를 근거로 계층 깊이를 판정,
     스택 기반으로 트리를 구성한다. 마커 없는 짧은 제목줄(x0≈155.2)은 소제목으로,
     더 깊게 들여쓰기된 마커 없는 줄은 직전 항목의 줄바꿈(계속줄)으로 처리한다.
"""
import pdfplumber
import pypdf
import re
import json
import datetime

SRC = '../docs/260901_체류민원_자격별_안내_매뉴얼.pdf'  # 원본 PDF는 저작권상 이 저장소에 미포함 — 같은 이름으로 받아서 이 경로에 두면 실행 가능
OUT = '../data/D2_stay_extension_stage1.json'

VISA_BOOKMARK = '유학(D-2)'
NEXT_BOOKMARK = '산업연수(D-3)'   # D-2 챕터의 끝 = 다음 챕터 시작 직전
TARGET_LABEL = '체류기간연장허가'

GAP_SPACE_THRESHOLD = 1.5   # pt. 이보다 넓은 간격만 실제 띄어쓰기로 간주
LABEL_X0_MAX = 140          # 좌측 라벨 컬럼 x0 상한
FOOTER_TOP_MIN = 780        # 페이지 하단 쪽번호 영역

MARKER_GANADA = re.compile(r'^([가-힣])\s*\.\s*(.+)$')
MARKER_CIRCLE = '❍'
MARKER_DASH_CHARS = ('-', '–', '‐')
MARKER_NOTE_CHARS = ('※',)
MARKER_FOOTNOTE_CHARS = ('*',)
CIRCLED_NUM = set('①②③④⑤⑥⑦⑧⑨⑩')
BLACK_CIRCLED_NUM = set('❶❷❸❹❺❻❼❽❾')
MARKER_CHECK = ('✔',)
MARKER_TICK = ('▪',)


def find_chapter_page_range(pdf_path, start_title, end_title):
    reader = pypdf.PdfReader(pdf_path)
    outline = reader.outline

    flat = []
    def walk(items):
        for it in items:
            if isinstance(it, list):
                walk(it)
            else:
                flat.append(it)
    walk(outline)

    start_idx = end_idx = None
    for i, item in enumerate(flat):
        if item.title.strip() == start_title:
            start_idx = i
        elif item.title.strip() == end_title and start_idx is not None and end_idx is None:
            end_idx = i
            break
    if start_idx is None:
        raise ValueError(f'북마크를 찾지 못함: {start_title}')
    start_page = reader.get_destination_page_number(flat[start_idx])
    end_page = reader.get_destination_page_number(flat[end_idx]) if end_idx is not None else None
    return start_page, end_page   # 0-indexed, end_page는 '다음 챕터 시작 페이지'(포함 안 함)


def get_label_runs(page):
    """페이지 좌측 라벨 컬럼에서 (top, label_text) 목록을 반환. '목차' 링크/쪽번호 폰트는 제외."""
    words = page.extract_words(extra_attrs=['fontname'])
    label_words = [
        w for w in words
        if w['x0'] < LABEL_X0_MAX
        and w['top'] < FOOTER_TOP_MIN
        and w['fontname'] != 'INPILL+Gulim'
        and '목차' not in w['text']
        and w['text'] != ''   # '목차' 바로가기 아이콘(private-use 글리프) 제외
    ]
    label_words.sort(key=lambda w: w['top'])
    runs = []
    cur_top, cur_text = None, []
    for w in label_words:
        if cur_top is None or w['top'] - cur_top > 20:
            if cur_text:
                runs.append((cur_top, ''.join(cur_text)))
            cur_text, cur_top = [w['text']], w['top']
        else:
            cur_text.append(w['text'])
    if cur_text:
        runs.append((cur_top, ''.join(cur_text)))
    return runs


def body_words(page):
    words = page.extract_words(extra_attrs=['fontname'], keep_blank_chars=False)
    return [
        w for w in words
        if w['x0'] >= LABEL_X0_MAX
        and w['top'] < FOOTER_TOP_MIN
        and w['fontname'] != 'INPILL+Gulim'
        and '목차' not in w['text']
    ]


def join_words_to_line(words):
    """x-gap 기반으로 단어를 재조합. 간격이 좁으면(낱글자 분리 아티팩트) 공백 없이 붙인다."""
    words = sorted(words, key=lambda w: w['x0'])
    out = words[0]['text']
    prev_x1 = words[0]['x1']
    for w in words[1:]:
        gap = w['x0'] - prev_x1
        out += (' ' if gap > GAP_SPACE_THRESHOLD else '') + w['text']
        prev_x1 = w['x1']
    return out


def group_lines(words, tol=3):
    words = sorted(words, key=lambda w: w['top'])
    lines, cur_top, cur = [], None, []
    for w in words:
        if cur_top is None or abs(w['top'] - cur_top) > tol:
            if cur:
                lines.append(cur)
            cur, cur_top = [w], w['top']
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    out = []
    for ln in lines:
        raw = join_words_to_line(ln)
        is_example = raw.lstrip().startswith('')  # 원문의 '예)' 아이콘 표시
        text = raw.replace('', '').strip()
        if text:
            out.append({
                'x0': min(w['x0'] for w in ln),
                'top': ln[0]['top'],
                'text': text,
                'is_example': is_example,
            })
    return out


def extract_scoped_lines(pdf, start_page, end_page, target_label):
    """target_label이 활성화된 구간의 본문 줄만 순서대로 모은다."""
    scoped = []
    for pi in range(start_page, end_page if end_page is not None else start_page + 1):
        page = pdf.pages[pi]
        runs = get_label_runs(page)
        if not runs:
            continue
        # 이 페이지에서 target_label이 유효한 top 구간들을 계산
        active_ranges = []
        for i, (top, label) in enumerate(runs):
            if label == target_label:
                range_end = runs[i + 1][0] if i + 1 < len(runs) else FOOTER_TOP_MIN
                active_ranges.append((top, range_end))
        if not active_ranges:
            continue
        lines = group_lines(body_words(page))
        for ln in lines:
            if any(lo <= ln['top'] < hi for lo, hi in active_ranges):
                scoped.append((pi, ln['x0'], ln['top'], ln['text'], ln['is_example']))
    return scoped


def classify(line_text):
    """(marker_type, depth_rank, cleaned_text) 반환. marker 없으면 None."""
    m = MARKER_GANADA.match(line_text)
    if m and len(m.group(1)) == 1:
        return ('ganada', 0, f"{m.group(1)}. {m.group(2).strip()}")
    if line_text.startswith(MARKER_CIRCLE):
        return ('circle', 2, line_text[1:].strip())
    if line_text[0] in MARKER_DASH_CHARS:
        return ('dash', 3, line_text[1:].strip())
    if line_text[0] in MARKER_NOTE_CHARS:
        return ('note', 4, line_text[1:].strip())
    if line_text[0] in MARKER_FOOTNOTE_CHARS:
        return ('footnote', 4, line_text[1:].strip())
    if line_text[0] in CIRCLED_NUM or line_text[0] in BLACK_CIRCLED_NUM:
        return ('numbered', 3, line_text[1:].strip())
    if line_text[0] in MARKER_CHECK or line_text[0] in MARKER_TICK:
        return ('callout', 4, line_text[1:].strip())
    return None


def build_tree(scoped_lines, subheading_x0=155.2, x0_tol=1.5):
    """스택 기반으로 계층 트리를 만든다."""
    root = {'title': 'root', 'depth_rank': -1, 'children': [], 'text': None}
    stack = [root]

    def new_node(depth_rank, text):
        node = {'text': text, 'children': []}
        while stack[-1]['depth_rank'] >= depth_rank if 'depth_rank' in stack[-1] else False:
            stack.pop()
        node['depth_rank'] = depth_rank
        stack[-1]['children'].append(node)
        stack.append(node)
        return node

    for pi, x0, top, text, is_example in scoped_lines:
        text = text.strip()
        if not text:
            continue
        cls = classify(text)
        if cls is not None:
            _, depth_rank, cleaned = cls
            new_node(depth_rank, cleaned)
            continue
        # marker 없는 줄: 소제목인지 이어지는 줄(줄바꿈)인지 x0로 판별
        if abs(x0 - subheading_x0) <= x0_tol and not is_example:
            new_node(1, text)
        else:
            # 직전(top of stack)의 텍스트에 이어붙임 ('예)' 아이콘 줄은 괄호로 구분)
            addition = f' (예: {text})' if is_example else ' ' + text
            if stack[-1] is root or stack[-1]['text'] is None:
                # 방어적 처리: 이어붙일 대상이 없으면 새 소제목으로
                new_node(1, text)
            else:
                stack[-1]['text'] += addition
    return root['children']


def nodes_to_json(nodes):
    out = []
    for n in nodes:
        item = {'text': n['text']}
        if n['children']:
            item['children'] = nodes_to_json(n['children'])
        out.append(item)
    return out


def main():
    start_page, end_page = find_chapter_page_range(SRC, VISA_BOOKMARK, NEXT_BOOKMARK)
    print(f'D-2 챕터 페이지 범위(0-indexed): {start_page} ~ {end_page} '
          f'(1-indexed {start_page+1} ~ {end_page})')

    pdf = pdfplumber.open(SRC)
    scoped = extract_scoped_lines(pdf, start_page, end_page, TARGET_LABEL)
    pages_used = sorted(set(pi + 1 for pi, *_ in scoped))
    print(f'"{TARGET_LABEL}" 라벨 구간에서 추출된 줄 수: {len(scoped)}, 사용된 페이지: {pages_used}')

    tree = build_tree(scoped)

    # tree의 최상위는 가./나./다. 3개 노드여야 함
    result = {
        '가.기본원칙': None,
        '나.세부심사기준': None,
        '다.제출서류': None,
    }
    key_map = {
        '가': '가.기본원칙',
        '나': '나.세부심사기준',
        '다': '다.제출서류',
    }
    for n in tree:
        prefix = n['text'][0]
        key = key_map.get(prefix)
        if key:
            result[key] = nodes_to_json(n['children'])
        else:
            print('경고: 예상 밖 최상위 노드 ->', n['text'])

    output = {
        'visa_code': 'D-2',
        'visa_name': '유학',
        'section': '체류기간 연장허가',
        'content': result,
        'source': {
            'document': '외국인체류 안내매뉴얼 (260901 체류민원 자격별 안내 매뉴얼)',
            'file': SRC.split('/')[-1],
            'pages_1_indexed': pages_used,
            'extracted_at': datetime.date.today().isoformat(),
        },
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
    import os
    os.makedirs('../data', exist_ok=True)
    main()