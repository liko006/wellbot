"""공용(shared) KB 문서별 속성 레지스트리.

`config/knowBase.yaml` 의 `shared_kb.docs` 를 읽고 쓴다 — `"대분류/[소분류/]파일명"` →
`{tier, dept, ...}`. 검색측(`kb_retriever`)이 재랭킹(권위 티어)과 담당 부서 표시에
쓰는 값으로, 지금까지는 수기로 편집하던 것을 admin UI 에서 관리하기 위한 계층이다.

문서 키는 `kb_retriever._shared_doc_key` 산출과 **같은 논리 경로**여야 한다
(raw/originals 마커가 없는 경로). 어긋나면 조용히 재랭킹이 안 붙는다.

`shared_kb_service`(S3·Bedrock ops)와 분리한 이유:
    - 여기는 설정 파일 편집만 한다(AWS 호출 없음).
    - 향후 `docs` 를 DB 테이블로 옮길 때(문서 키 PK + tier·dept 컬럼) 이 파일만 바뀐다.
"""

from __future__ import annotations

import logging
import re

import yaml

from wellbot.paths import KNOWBASE_YAML
from wellbot.services.knowledgebase.config import get_kb_config

log = logging.getLogger(__name__)

# entry 한 줄: `    "대분류/파일명" : {tier: 0, dept: "법무팀"}`
_DOC_ENTRY_RE = re.compile(r'^ {4}"(?P<key>[^"]*)"\s*:\s*\{(?P<attrs>.*)\}\s*$')

# 파일에 기록하는 속성 순서(그 외는 이름순) — diff 를 사람이 읽을 수 있게 고정.
_DOC_ATTR_ORDER = ("tier", "dept")

# 부서명은 yaml 한 줄에 기록되므로 길이를 제한한다.
_DEPT_MAX_LEN = 50


def _cfg() -> dict:
    """shared_kb 설정 섹션 (get_kb_config 가 캐싱하므로 매번 같은 dict)."""
    return get_kb_config()["shared_kb"]


# ──────────────────────────────────────────────
# 조회
# ──────────────────────────────────────────────
def list_doc_attrs() -> dict[str, dict]:
    """문서 논리 경로 → 속성({tier, dept, ...}) 매핑."""
    return _cfg().get("docs") or {}


def tier_options() -> list[int]:
    """설정된 권위 티어 번호 목록(오름차순, 0=최우선). 티어 사다리가 유일한 출처."""
    return sorted(int(t) for t in (_cfg().get("authority_tiers") or {}))


# ──────────────────────────────────────────────
# yaml 편집 (주석 보존을 위해 텍스트 단위로)
# ──────────────────────────────────────────────
def _yaml_scalar(value: object) -> str:
    """flow 표기 한 줄에 넣을 스칼라. 숫자·불리언은 그대로, 그 외는 따옴표."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _format_doc_entry(doc_key: str, attrs: dict) -> str:
    """entry 한 줄 생성. 기존 파일 형식(`    "키" : {tier: 0, dept: "X"}`)을 따른다."""
    keys = [k for k in _DOC_ATTR_ORDER if k in attrs]
    keys += sorted(k for k in attrs if k not in _DOC_ATTR_ORDER)
    body = ", ".join(f"{k}: {_yaml_scalar(attrs[k])}" for k in keys)
    return f'    "{doc_key}" : {{{body}}}'


def _docs_block(lines: list[str]) -> tuple[int, int]:
    """shared_kb.docs 블록의 (헤더 줄 index, 마지막 entry 줄 index).

    yaml.dump 로 통째 쓰지 않는 이유는 folders 레지스트리와 같다 — knowBase.yaml 에는
    운영 주석이 많아 덤프하면 전부 사라진다. 섹션을 못 찾으면 파일을 건드리지 않고
    예외를 던진다(형식이 바뀐 파일을 추측으로 수정하는 것보다 안전).
    """
    in_shared = False
    header = None
    for i, line in enumerate(lines):
        if line.strip() and not line.startswith((" ", "\t", "#")):
            in_shared = line.startswith("shared_kb:")   # 최상위 키 → 섹션 전환
            continue
        if in_shared and line.startswith("  docs:"):
            header = i
            break
    if header is None:
        raise RuntimeError("knowBase.yaml 에서 shared_kb.docs 섹션을 찾지 못했습니다.")

    last = header
    for j in range(header + 1, len(lines)):
        if not lines[j].strip() or not lines[j].startswith("    "):
            break                                       # 블록 종료(빈 줄 또는 들여쓰기 이탈)
        if _DOC_ENTRY_RE.match(lines[j]):
            last = j
    return header, last


def _parse_doc_attrs(raw: str) -> dict:
    """entry 의 flow 표기 안쪽(`tier: 0, dept: "X"`)을 dict 로 파싱."""
    parsed = yaml.safe_load("{" + raw + "}")
    if not isinstance(parsed, dict):
        raise RuntimeError(f"docs entry 형식을 해석할 수 없습니다: {raw!r}")
    return parsed


def _find_doc_entry(
    lines: list[str], header: int, last: int, doc_key: str,
) -> tuple[int | None, dict]:
    """docs 블록에서 문서의 (줄 index, 현재 속성). 없으면 (None, {}).

    현재 속성을 **파일에서** 읽는 이유: 메모리 캐시를 기준으로 병합하면 캐시와 파일이
    어긋난 순간(다른 경로로 yaml 이 수정된 경우 등) 이미 기록돼 있던 속성이 조용히
    지워진다. 파일이 기록의 단일 출처다.
    """
    for j in range(header + 1, last + 1):
        match = _DOC_ENTRY_RE.match(lines[j])
        if match and match.group("key") == doc_key:
            return j, _parse_doc_attrs(match.group("attrs"))
    return None, {}


def _update_doc_attr(doc_key: str, attr: str, value: object | None) -> None:
    """문서 한 건의 속성 하나를 기록(value=None 이면 제거). 나머지 속성은 보존.

    남는 속성이 없으면 entry 줄 자체를 지운다. 메모리 설정 캐시도 함께 갱신한다 —
    검색측 `kb_retriever._merge_results` 가 매 호출마다 `get_kb_config()` 를 읽으므로
    앱 재시작 없이 다음 검색부터 반영된다.
    """
    lines = KNOWBASE_YAML.read_text(encoding="utf-8").split("\n")
    header, last = _docs_block(lines)
    index, attrs = _find_doc_entry(lines, header, last, doc_key)

    if value is None:
        attrs.pop(attr, None)
    else:
        attrs[attr] = value

    if attrs:
        entry = _format_doc_entry(doc_key, attrs)
        if index is None:
            lines.insert(last + 1, entry)
        else:
            lines[index] = entry
    elif index is not None:
        del lines[index]
    else:
        return                                          # 지울 것도, 쓸 것도 없음

    KNOWBASE_YAML.write_text("\n".join(lines), encoding="utf-8")

    docs = _cfg().get("docs") or {}
    if attrs:
        docs[doc_key] = dict(attrs)
    else:
        docs.pop(doc_key, None)
    _cfg()["docs"] = docs
    log.info("[Config] 문서 속성 기록: %s → %s", doc_key, attrs or "(제거)")


def remove_docs_under(top: str) -> int:
    """대분류 하위 문서 entry 를 **한 번의 쓰기로** 모두 제거. 반환: 제거된 건수.

    폴더(대분류)를 삭제할 때 쓴다. 한 건씩 지우면 파일을 문서 수만큼 다시 쓰게 되고,
    중간에 실패하면 절반만 지워진 상태가 남는다.
    """
    if not top:
        return 0
    lines = KNOWBASE_YAML.read_text(encoding="utf-8").split("\n")
    header, last = _docs_block(lines)

    prefix = f"{top}/"
    removed: list[str] = []
    kept: list[str] = []
    for i, line in enumerate(lines):
        match = _DOC_ENTRY_RE.match(line) if header < i <= last else None
        if match and match.group("key").startswith(prefix):
            removed.append(match.group("key"))
            continue
        kept.append(line)

    if not removed:
        return 0

    KNOWBASE_YAML.write_text("\n".join(kept), encoding="utf-8")
    docs = _cfg().get("docs") or {}
    for key in removed:
        docs.pop(key, None)
    _cfg()["docs"] = docs
    log.info("[Config] 문서 속성 일괄 제거: top=%s (%d건)", top, len(removed))
    return len(removed)


# ──────────────────────────────────────────────
# 변경
# ──────────────────────────────────────────────
def set_doc_tier(doc_key: str, tier: int | None) -> None:
    """문서의 권위 티어를 설정(None=해제). 같은 문서의 다른 속성은 보존."""
    if tier is not None:
        # 사다리에 없는 티어는 배수가 1.0(=미배정)이 되어 아무 효과가 없다.
        # 조용히 무효가 되지 않도록 기록 전에 거부한다.
        options = tier_options()
        if int(tier) not in options:
            raise ValueError(f"등록되지 않은 티어입니다: {tier} (사용 가능: {options})")
        tier = int(tier)
    _update_doc_attr(doc_key, "tier", tier)


def set_doc_dept(doc_key: str, dept: str | None) -> None:
    """문서의 담당 부서를 설정(빈값=해제). 같은 문서의 다른 속성은 보존."""
    name = (dept or "").strip()
    if name:
        if len(name) > _DEPT_MAX_LEN:
            raise ValueError(f"담당 부서명은 최대 {_DEPT_MAX_LEN}자입니다.")
        # 관리자 입력이 그대로 yaml 한 줄이 되므로 줄바꿈·제어문자를 거부한다
        # (flow 표기가 깨지면 설정 파일 전체가 파싱 불가가 된다).
        if any(ord(ch) < 32 for ch in name):
            raise ValueError("담당 부서명에 줄바꿈·제어문자를 쓸 수 없습니다.")
    _update_doc_attr(doc_key, "dept", name or None)
