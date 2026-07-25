import re
import hashlib


def parse_page_count(user_input: str) -> float:
    user_input = user_input.lower()
    
    # 한글 숫자 매핑
    korean_numbers = {
        '반': 0.5,
        '한': 1, '하나': 1, '일': 1,
        '두': 2, '둘': 2, '이': 2,
        '세': 3, '셋': 3, '삼': 3,
        '네': 4, '넷': 4, '사': 4,
        '다섯': 5, '오': 5,
        '여섯': 6, '육': 6,
        '일곱': 7, '칠': 7,
        '여덟': 8, '팔': 8,
        '아홉': 9, '구': 9,
        '열': 10, '십': 10,
    }
    
    # 1. "반페이지", "반장" 패턴
    if re.search(r"반\s*(?:페이지|장|page)", user_input):
        return 0.5
    
    # 2. 한글 숫자 + 반 (예: "한장반", "두페이지반")
    for kr_num, value in korean_numbers.items():
        pattern = f"{kr_num}\\s*(?:장|페이지|page)\\s*반"
        if re.search(pattern, user_input):
            return value + 0.5
    
    # 3. 한글 숫자 단독 (예: "한장", "두페이지")
    for kr_num, value in korean_numbers.items():
        pattern = f"{kr_num}\\s*(?:장|페이지|page)"
        if re.search(pattern, user_input):
            return float(value)
    
    # 4. 아라비아 숫자 + 반 (예: "1장반", "2페이지반")
    m = re.search(r"(\d+)\s*(?:장|페이지|page)\s*반", user_input)
    if m:
        return float(m.group(1)) + 0.5
    
    # 5. 소수점 표기 (예: "0.5장", "1.5페이지", "2.5page")
    m = re.search(r"(\d+\.?\d*)\s*(?:장|페이지|page)", user_input)
    if m:
        return float(m.group(1))
    
    # 6. 숫자만 있는 경우 (예: "3", "5")
    m = re.search(r"(\d+\.?\d*)", user_input)
    if m:
        return float(m.group(1))
    
    # 7. 파싱 실패
    return 0
    

def fmt_pages(n) -> str:
    """페이지 수 표시용: 1.0 → '1', 0.5 → '0.5'."""
    try:
        f = float(n)
    except (TypeError, ValueError):
        return str(n)
    return str(int(f)) if f == int(f) else str(f)
    
    
def strip_code_fences(text: str) -> str:
    text = re.sub(r"^\s*```[a-zA-Z]*\s*\n?", "", text)  # 맨 앞 여는 펜스
    text = re.sub(r"\n?```\s*$", "", text)              # 맨 끝 닫는 펜스
    return text.replace("```", "").strip()              # 본문 잔여 펜스
    
    

# 해시변환

def to_safe_id(raw: str) -> str:
    raw = (raw or "").strip()
    # 영문/숫자로 시작 + 영문·숫자·_-만으로 구성된 경우만 그대로 통과
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]*", raw):
        return raw
    # 그 외(한글·점·공백·기호·언더스코어 시작 등) → 해시
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"tpl_{h}"   # 't'로 시작 → 시작 문자 규칙 충족
    
    

_SEP_CELL = re.compile(r"^:?-{2,}:?$")

def _split_cells(line):
    raw = [c.strip() for c in line.split("|")]
    while raw and raw[0] == "":
        raw.pop(0)
    while raw and raw[-1] == "":
        raw.pop()
    return raw

def _content_cells(cells):
    return [c for c in cells if c != "" and not _SEP_CELL.match(c)]

_SEP_CELL = re.compile(r"^:?-{2,}:?$")

def _split_cells(line):
    raw = [c.strip() for c in line.split("|")]
    while raw and raw[0] == "":
        raw.pop(0)
    while raw and raw[-1] == "":
        raw.pop()
    return raw

def _content_cells(cells):
    return [c for c in cells if c != "" and not _SEP_CELL.match(c)]

def _emit_rows(content, n):
    rows = []
    for i in range(0, len(content), n):
        row = content[i:i+n]
        if len(row) < n:
            row += [""] * (n - len(row))
        rows.append("| " + " | ".join(row) + " |")
    return rows

def _normalize_inline_tables(text: str) -> str:
    """뭉친 마크다운 표를 행 단위로 복원. 열 수는 헤더 기준으로 확정하므로
    구분선 개수가 틀려도 동작한다. 데이터행만 뭉친 경우도 처리한다."""
    if "|" not in text:
        return text
    out = []
    table_n = 0
    for line in text.split("\n"):
        stripped = line.strip()
        is_pipe_row = ("|" in line) and stripped.startswith("|")

        if is_pipe_row and re.search(r"\|\s*:?-{2,}", line):
            cells = _split_cells(line)
            sep_pos = [i for i, c in enumerate(cells) if _SEP_CELL.match(c)]
            first_sep = sep_pos[0]
            header = [c for c in cells[:first_sep] if c != ""]   # 구분선 앞 = 헤더
            n = len(header)
            if n < 2:                                            # 헤더 없으면 구분선 개수로
                n = sum(1 for c in cells if _SEP_CELL.match(c))
            if n >= 2:
                table_n = n
                last_sep = first_sep
                while last_sep + 1 < len(cells) and _SEP_CELL.match(cells[last_sep + 1]):
                    last_sep += 1
                if header:
                    out.append("| " + " | ".join(header) + " |")
                out.append("|" + "|".join(["---"] * n) + "|")
                out.extend(_emit_rows(_content_cells(cells[last_sep + 1:]), n))
                continue

        if table_n and is_pipe_row:
            out.extend(_emit_rows(_content_cells(_split_cells(line)), table_n))
            continue

        if not is_pipe_row:
            table_n = 0
        out.append(line)
    return "\n".join(out)
    
    
_INDENT = "\u00A0" * 5 

def md_linebreaks(text: str) -> str:
    text = _normalize_inline_tables(text)   # ← 추가: 뭉친 표 먼저 정규화
    text = text.replace("~", "～")
    out = []
    for line in text.split("\n"):
        s = line.rstrip()
        if s.lstrip().startswith("|") or s == "":
            out.append(line)            # 표 행·빈 줄은 그대로
            continue
        stripped = s.lstrip(" ")
        if stripped.startswith("□"):            # 대분류
            indent = ""
        elif stripped[:1] in ("-", "▪", "➜"):   # 중분류
            indent = _INDENT
        elif stripped[:1] in ("·", "•"):        # 소분류
            indent = _INDENT * 2
        else:
            indent = ""                          # 제목·좌/우 등은 들여쓰기 없음
        out.append(indent + stripped + "  ")     # 끝 2칸 = 하드 브레이크
    return "\n".join(out)