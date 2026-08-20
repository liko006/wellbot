"""공용 KB 런타임 레지스트리 파일 (`config/kb_registry.yaml`).

폴더→Data Source 매핑(`folders`)과 문서별 속성(`docs`: 티어·담당부서)은 앱이 **런타임에
수정**한다(admin UI·CLI). 그 대상이 git 이 추적하는 `knowBase.yaml` 이면 배포마다
문제가 된다 — 서버 파일에 로컬 수정이 남아 그 파일을 건드리는 커밋이 오면 `git pull`
이 거부되고, 습관적으로 `git checkout` 으로 넘기면 **폴더 등록과 티어 큐레이션이 통째로
날아간다**. 그래서 쓰기 대상만 이 파일로 분리하고 git 에서 제외한다.

    config/knowBase.yaml    커밋 · 읽기 전용 · folders/docs 는 **씨앗**(초기값)
    config/kb_registry.yaml gitignore · 런타임 쓰기 · 있으면 씨앗을 대체

파일이 없으면 **씨앗을 그대로 복사해** 만든다. 복사하지 않으면 첫 편집에서 기존
큐레이션이 통째로 사라진다(레지스트리가 생긴 순간부터 그쪽이 전부이므로).

이 모듈은 파일의 **위치와 표기 형식**만 안다. 값의 의미(티어 검증, 병합 규칙)는
`shared_kb_docs`, `shared_kb_service` 가 갖는다.
"""

from __future__ import annotations

import logging

import yaml

from wellbot.paths import KB_REGISTRY_YAML, KNOWBASE_YAML

log = logging.getLogger(__name__)

REGISTRY_YAML = KB_REGISTRY_YAML

_HEADER = (
    "# 공용 KB 런타임 레지스트리 — **앱이 자동으로 수정합니다** (admin UI · CLI).\n"
    "# git 추적 대상이 아니며, 초기값은 config/knowBase.yaml 의 shared_kb 에서 복사됩니다.\n"
    "# 손으로 고쳐도 되지만, 형식(들여쓰기 4칸 · 키는 따옴표 · 값은 한 줄)은 유지해야\n"
    "# 앱의 편집이 기존 줄을 찾을 수 있습니다.\n"
)


# ──────────────────────────────────────────────
# 표기 형식 (편집이 다시 찾을 수 있어야 하므로 한 곳에서 정한다)
# ──────────────────────────────────────────────
def yaml_scalar(value: object) -> str:
    """flow 표기 한 줄에 넣을 스칼라. 숫자·불리언은 그대로, 그 외는 따옴표."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def format_doc_entry(doc_key: str, attrs: dict, order: tuple[str, ...]) -> str:
    """docs 한 줄. `    "대분류/파일명" : {tier: 0, dept: "법무팀"}`

    order 에 있는 속성을 먼저, 나머지는 이름순 — diff 를 사람이 읽을 수 있게 고정.
    """
    keys = [k for k in order if k in attrs]
    keys += sorted(k for k in attrs if k not in order)
    body = ", ".join(f"{k}: {yaml_scalar(attrs[k])}" for k in keys)
    return f'    "{doc_key}" : {{{body}}}'


def format_folder_entry(top: str, data_source_id: str) -> str:
    """folders 한 줄. `    사규: "OGGPZMJXWM"`"""
    return f'    {top}: "{data_source_id}"'


# ──────────────────────────────────────────────
# 파일 보장 / 읽기 / 쓰기
# ──────────────────────────────────────────────
def _seed_text() -> str:
    """knowBase.yaml 의 현재 folders/docs 로 초기 파일 내용을 만든다.

    docs 를 **마지막**에 둔다 — 새 문서 항목은 블록 끝에 덧붙므로, 뒤에 다른 섹션이
    없으면 삽입 지점이 단순해진다.
    """
    seed = yaml.safe_load(KNOWBASE_YAML.read_text(encoding="utf-8")) or {}
    shared = seed.get("shared_kb") or {}
    folders = shared.get("folders") or {}
    docs = shared.get("docs") or {}

    lines = [_HEADER.rstrip("\n"), "shared_kb:"]
    if folders:
        lines.append("  folders:")
        lines += [format_folder_entry(top, folders[top]) for top in sorted(folders)]
    else:
        lines.append("  folders: {}")
    lines.append("  docs:")
    lines += [
        format_doc_entry(key, docs[key] or {}, ("tier", "dept"))
        for key in sorted(docs)
    ]
    return "\n".join(lines) + "\n"


def ensure() -> None:
    """레지스트리 파일을 보장. 없으면 씨앗을 복사해 만든다."""
    if REGISTRY_YAML.exists():
        return
    REGISTRY_YAML.write_text(_seed_text(), encoding="utf-8")
    log.info("[Config] 런타임 레지스트리 생성: %s (knowBase.yaml 씨앗 복사)", REGISTRY_YAML)


def read_text() -> str:
    """레지스트리 파일 내용 (없으면 먼저 만든다)."""
    ensure()
    return REGISTRY_YAML.read_text(encoding="utf-8")


def write_text(content: str) -> None:
    REGISTRY_YAML.write_text(content, encoding="utf-8")


def read_lines() -> list[str]:
    return read_text().split("\n")


def write_lines(lines: list[str]) -> None:
    write_text("\n".join(lines))


def load() -> dict:
    """레지스트리 내용을 dict 로. **파일이 없으면 만들지 않고 빈 dict** 를 돌려준다.

    설정 로드 경로에서 부르므로 파일을 생성하지 않는다 — 읽기만 하는 프로세스(예:
    조회용 스크립트)가 파일을 만들어 두면 그 시점의 씨앗이 굳어버린다.
    """
    if not REGISTRY_YAML.exists():
        return {}
    try:
        return yaml.safe_load(REGISTRY_YAML.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - 설정 로드는 계속돼야 한다
        log.exception("런타임 레지스트리 파싱 실패 — 씨앗(knowBase.yaml)으로 진행: %s",
                      REGISTRY_YAML)
        return {}
