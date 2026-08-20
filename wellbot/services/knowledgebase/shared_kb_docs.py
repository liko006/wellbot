"""공용(shared) KB 문서별 속성.

`shared_kb.docs` 를 읽고 쓴다 — `"대분류/[소분류/]파일명"` → `{tier, dept, ...}`.
검색측(`kb_retriever`)이 재랭킹(권위 티어)과 담당 부서 표시에 쓰는 값으로, 지금까지는
수기로 편집하던 것을 admin UI 에서 관리하기 위한 계층이다.

**읽기는 설정(`get_kb_config`), 쓰기는 런타임 레지스트리(`kb_registry`)** 로 갈린다 —
런타임에 고치는 값을 git 이 추적하는 파일에 쓰면 배포 때 충돌·유실이 난다(`kb_registry`
모듈 docstring). 설정은 씨앗(knowBase.yaml) 위에 레지스트리를 덮은 결과다.

문서 키는 `kb_retriever._shared_doc_key` 산출과 **같은 논리 경로**여야 한다
(raw/originals 마커가 없는 경로). 어긋나면 조용히 재랭킹이 안 붙는다.

`shared_kb_service`(S3·Bedrock ops)와 분리한 이유:
    - 여기는 설정 파일 편집만 한다(AWS 호출 없음).
    - 향후 `docs` 를 DB 테이블로 옮길 때(문서 키 PK + tier·dept 컬럼) 이 파일만 바뀐다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

import yaml

from wellbot.services.knowledgebase import kb_registry
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
# 레지스트리 파일 편집 (줄 단위)
# ──────────────────────────────────────────────
def _format_doc_entry(doc_key: str, attrs: dict) -> str:
    """entry 한 줄. 표기 형식은 `kb_registry` 가 단일 출처(편집이 다시 찾아야 하므로)."""
    return kb_registry.format_doc_entry(doc_key, attrs, _DOC_ATTR_ORDER)


def _docs_block(lines: list[str]) -> tuple[int, int]:
    """shared_kb.docs 블록의 (헤더 줄 index, 마지막 entry 줄 index).

    yaml.dump 로 통째 쓰지 않는 이유: 파일 상단 안내 주석이 사라지고, 무엇보다 블록
    표기가 바뀌면 다음 편집이 기존 줄을 못 찾는다. 섹션을 못 찾으면 파일을 건드리지
    않고 예외를 던진다(형식이 바뀐 파일을 추측으로 수정하는 것보다 안전).
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
    lines = kb_registry.read_lines()
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

    kb_registry.write_lines(lines)

    docs = _cfg().get("docs") or {}
    if attrs:
        docs[doc_key] = dict(attrs)
    else:
        docs.pop(doc_key, None)
    _cfg()["docs"] = docs
    log.info("[Config] 문서 속성 기록: %s → %s", doc_key, attrs or "(제거)")


def _remove_entries(should_remove: Callable[[str], bool], label: str) -> int:
    """조건에 맞는 문서 entry 를 **한 번의 쓰기로** 제거. 반환: 제거된 건수.

    한 건씩 지우면 파일을 문서 수만큼 다시 쓰게 되고, 중간에 실패하면 절반만 지워진
    상태가 남는다.
    """
    lines = kb_registry.read_lines()
    header, last = _docs_block(lines)

    removed: list[str] = []
    kept: list[str] = []
    for i, line in enumerate(lines):
        match = _DOC_ENTRY_RE.match(line) if header < i <= last else None
        if match and should_remove(match.group("key")):
            removed.append(match.group("key"))
            continue
        kept.append(line)

    # 캐시는 **파일에서 지운 것만이 아니라 조건에 맞는 전부**를 비운다. 정상 경로에서는
    # 둘이 같지만, 어긋난 상태(다른 경로로 yaml 이 수정된 경우 등)에서 캐시에만 남은
    # 키는 삭제된 문서의 티어를 계속 적용해 검색 순위를 바꾼다.
    docs = _cfg().get("docs") or {}
    stale = [key for key in docs if should_remove(key)]
    if not (removed or stale):
        return 0

    if removed:
        kb_registry.write_lines(kept)
    for key in stale:
        docs.pop(key, None)
    _cfg()["docs"] = docs
    log.info(
        "[Config] 문서 속성 제거: %s (yaml %d건 · 캐시 %d건)",
        label, len(removed), len(stale),
    )
    return len(removed)


def remove_docs(doc_keys: list[str]) -> int:
    """지정한 문서들의 entry 를 제거. 반환: 제거된 건수.

    문서를 삭제할 때 반드시 함께 호출한다 — 남겨두면 문서 목록에 나타나지 않아
    **화면에서는 지울 수 없는** 키가 되고, 나중에 같은 경로로 다른 파일을 올리면
    그 문서가 예전 티어·담당부서를 그대로 물려받는다.
    """
    targets = {key for key in doc_keys if key}
    if not targets:
        return 0
    return _remove_entries(lambda key: key in targets, f"{len(targets)}개 문서")


def remove_docs_under(top: str) -> int:
    """대분류 하위 문서 entry 를 모두 제거. 반환: 제거된 건수. (폴더 삭제용)"""
    if not top:
        return 0
    prefix = f"{top}/"
    return _remove_entries(lambda key: key.startswith(prefix), f"top={top}")


def rekey_docs_under(old_top: str, new_top: str) -> int:
    """대분류 이름 변경에 맞춰 문서 키의 앞부분을 옮긴다. 반환: 옮긴 건수.

    폴더 이름이 바뀌면 문서의 논리 경로도 바뀐다. 키를 옮기지 않으면 **옮겨진 문서는
    티어·담당부서를 통째로 잃고**(미배정으로 되돌아가 검색 순위가 바뀐다), 옛 키는
    화면에서 지울 수 없는 잔여물로 남는다.
    """
    if not old_top or not new_top or old_top == new_top:
        return 0

    lines = kb_registry.read_lines()
    header, last = _docs_block(lines)
    old_prefix, new_prefix = f"{old_top}/", f"{new_top}/"

    moved: list[tuple[str, str]] = []
    for j in range(header + 1, last + 1):
        match = _DOC_ENTRY_RE.match(lines[j])
        if not match:
            continue
        key = match.group("key")
        if not key.startswith(old_prefix):
            continue
        new_key = new_prefix + key[len(old_prefix):]
        lines[j] = _format_doc_entry(new_key, _parse_doc_attrs(match.group("attrs")))
        moved.append((key, new_key))

    if not moved:
        return 0

    kb_registry.write_lines(lines)
    docs = _cfg().get("docs") or {}
    for old_key, new_key in moved:
        if old_key in docs:
            docs[new_key] = docs.pop(old_key)
    _cfg()["docs"] = docs
    log.info("[Config] 문서 키 이동: %s/ → %s/ (%d건)", old_top, new_top, len(moved))
    return len(moved)


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
