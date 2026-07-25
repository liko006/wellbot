"""report_maker 순수 텍스트 유틸 (LLM 비의존).

legacy util.py + chat_state.py 의 결정론적 함수들을 한곳으로 통합·정리.
- 페이지 수 파싱, 코드펜스 제거, 안전 ID 해시
- 마크다운 표 정규화(출력 후처리), 표 데이터 감지
- 표시용 줄바꿈/들여쓰기
"""

from __future__ import annotations

import hashlib
import re

# ──────────────────────────────────────────────────────────────
# 업로드 파일 시그니처(매직바이트) 검증 — 확장자만 믿지 않고 실제 내용 재검증
# .pptx 는 ZIP 컨테이너(PK), 이미지/문서는 고유 시그니처. WEBP 는 RIFF....WEBP.
# ──────────────────────────────────────────────────────────────
MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
    ".pptx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),  # 오프셋 8~11 의 'WEBP' 는 아래에서 별도 확인
}


def magic_bytes_ok(ext: str, data: bytes) -> bool:
    """파일 앞부분이 확장자에 맞는 시그니처인지 검증. 미정의 확장자는 통과."""
    sigs = MAGIC_SIGNATURES.get(ext)
    if not sigs:
        return True
    if not any(data.startswith(s) for s in sigs):
        return False
    if ext == ".webp":
        return len(data) >= 12 and data[8:12] == b"WEBP"
    return True


# ──────────────────────────────────────────────────────────────
# 페이지 수 파싱
# ──────────────────────────────────────────────────────────────
_KOREAN_NUMBERS: dict[str, float] = {
    "반": 0.5,
    "한": 1, "하나": 1, "일": 1,
    "두": 2, "둘": 2, "이": 2,
    "세": 3, "셋": 3, "삼": 3,
    "네": 4, "넷": 4, "사": 4,
    "다섯": 5, "오": 5,
    "여섯": 6, "육": 6,
    "일곱": 7, "칠": 7,
    "여덟": 8, "팔": 8,
    "아홉": 9, "구": 9,
    "열": 10, "십": 10,
}


def parse_page_count(user_input: str) -> float:
    """자연어에서 페이지 수를 추출. 실패 시 0."""
    user_input = user_input.lower()

    if re.search(r"반\s*(?:페이지|장|page)", user_input):
        return 0.5
    for kr_num, value in _KOREAN_NUMBERS.items():
        if re.search(f"{kr_num}\\s*(?:장|페이지|page)\\s*반", user_input):
            return value + 0.5
    for kr_num, value in _KOREAN_NUMBERS.items():
        if re.search(f"{kr_num}\\s*(?:장|페이지|page)", user_input):
            return float(value)
    m = re.search(r"(\d+)\s*(?:장|페이지|page)\s*반", user_input)
    if m:
        return float(m.group(1)) + 0.5
    m = re.search(r"(\d+\.?\d*)\s*(?:장|페이지|page)", user_input)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+\.?\d*)", user_input)
    if m:
        return float(m.group(1))
    return 0


def fmt_pages(n) -> str:
    """페이지 수 표시용: 1.0 → '1', 0.5 → '0.5'."""
    try:
        f = float(n)
    except (TypeError, ValueError):
        return str(n)
    return str(int(f)) if f == int(f) else str(f)


def strip_code_fences(text: str) -> str:
    """```로 감싼 코드펜스 제거."""
    text = re.sub(r"^\s*```[a-zA-Z]*\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.replace("```", "").strip()


def to_safe_id(raw: str) -> str:
    """템플릿 식별자를 안전한 ASCII ID 로. 한글·기호는 해시로 치환."""
    raw = (raw or "").strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]*", raw):
        return raw
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"tpl_{h}"


# ──────────────────────────────────────────────────────────────
# 표 데이터 감지 (TABLE_READING_RULES 주입 여부 판단)
# ──────────────────────────────────────────────────────────────
_DIFF_SIGN_RE = re.compile(
    r"(?:[△▲]\s*\d|\+\s*\d[\d,]*(?:\.\d+)?(?:%|p|억|원|대|천|명|건)?)"
)


def has_table_data(text: str) -> bool:
    """입력에 수치 비교표/차이 부호가 있는지(표 해석 규칙 주입 트리거)."""
    if not text:
        return False
    if "|" in text and text.count("|") >= 4:
        return True
    if len(_DIFF_SIGN_RE.findall(text)) >= 3:
        return True
    if len(re.findall(r"[△▲]\s*\d", text)) >= 2:
        return True
    return False


# ──────────────────────────────────────────────────────────────
# 마크다운 표 정규화 (LLM 출력 후처리)
# ① 한 줄로 붙은 표 분리  ② 구분선 칸 수를 헤더에 맞춰 재생성
# ──────────────────────────────────────────────────────────────
_SEP_SEG = re.compile(r"\|(?:\s*:?-{3,}:?\s*\|)+")


def _row_cells(s: str) -> list[str] | None:
    s = s.strip()
    if not s.startswith("|"):
        return None
    t = s[1:]
    if t.endswith("|"):
        t = t[:-1]
    return [c.strip() for c in t.split("|")]


def _is_sep_cells(cells) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells)


def _expand_table_line(line: str) -> list[str]:
    """한 줄에 붙은 표를 헤더/구분선/데이터 행으로 분리."""
    m = _SEP_SEG.search(line)
    if not m:
        return [line]
    if _SEP_SEG.fullmatch(line.strip()):
        return [line]
    before = line[: m.start()].strip()
    sep = line[m.start() : m.end()].strip()
    after = line[m.end() :].strip()
    rows: list[str] = []
    if before:
        rows.extend(before.replace("||", "|\n|").split("\n"))
    rows.append(sep)
    if after:
        rows.extend(
            _expand_table_line(after)
            if _SEP_SEG.search(after)
            else after.replace("||", "|\n|").split("\n")
        )
    return rows


def normalize_md_tables(text: str) -> str:
    """LLM 이 붙여 출력한 표를 행 단위로 복원하고 구분선 칸 수를 헤더에 맞춘다."""
    if not text or "|" not in text:
        return text
    expanded: list[str] = []
    for line in text.split("\n"):
        expanded.extend(_expand_table_line(line))
    out: list[str] = []
    for line in expanded:
        cells = _row_cells(line)
        if cells is not None and _is_sep_cells(cells):
            prev = _row_cells(out[-1]) if out else None
            ncol = len(prev) if prev else len(cells)
            out.append("|" + "|".join(["---"] * ncol) + "|")
        else:
            out.append(line)
    return "\n".join(out)


# ──────────────────────────────────────────────────────────────
# 표시용 줄바꿈/들여쓰기 (기호 계층 → 들여쓰기 + 하드 브레이크)
# ──────────────────────────────────────────────────────────────
_INDENT = " " * 5


def md_linebreaks(text: str) -> str:
    """□/-/· 기호 계층에 맞춰 들여쓰기하고 줄 끝에 하드 브레이크(2칸)를 붙인다."""
    text = text.replace("~", "～")
    out: list[str] = []
    for line in text.split("\n"):
        s = line.rstrip()
        if s.lstrip().startswith("|") or s == "":
            out.append(line)
            continue
        stripped = s.lstrip(" ")
        if stripped.startswith("□"):
            indent = ""
        elif stripped[:1] in ("-", "▪", "➜"):
            indent = _INDENT
        elif stripped[:1] in ("·", "•"):
            indent = _INDENT * 2
        else:
            indent = ""
        out.append(indent + stripped + "  ")
    return "\n".join(out)


# ──────────────────────────────────────────────────────────────
# 구조 제안 질문 블록 파싱 (레거시 텍스트 규약 유지 — 인터페이스는 구조화 반환)
# NOTE: M8(구조화 JSON 질문 프로토콜)로의 완전 전환은 후속 과제. 현재는 프롬프트가
#       생성한 '추가 정보가 필요합니다' 블록을 파싱하되, 상위엔 list 로 노출한다.
# ──────────────────────────────────────────────────────────────
def strip_question_block(text: str) -> str:
    """구조 제안 끝의 '추가 정보가 필요합니다' 질문 섹션 제거(빌드 골격용)."""
    if not text or "추가 정보가 필요합니다" not in text:
        return text
    out, skipping = [], False
    for ln in text.split("\n"):
        if "추가 정보가 필요합니다" in ln:
            skipping = True
            continue
        if skipping:
            if ln.lstrip().startswith("[") or ln.startswith("##"):
                skipping = False
                out.append(ln)
            continue
        out.append(ln)
    return "\n".join(out).rstrip()


def extract_questions(text: str) -> list:
    """구조 제안 텍스트의 '추가 정보가 필요합니다' 블록에서 질문 문장만 추출."""
    if not text or "추가 정보가 필요합니다" not in text:
        return []
    qs, capturing = [], False
    for ln in text.split("\n"):
        if "추가 정보가 필요합니다" in ln:
            capturing = True
            continue
        if capturing:
            s = ln.strip()
            if s.startswith("[") or s.startswith("##") or s.startswith("---"):
                break
            if not s:
                continue
            s = re.sub(r"^\s*(?:\d+[.)]|[-*·])\s*", "", s).strip()
            if s:
                qs.append(s)
    return qs
