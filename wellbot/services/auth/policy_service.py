"""AI 서비스 접근 정책 (WB-SEC-004 1패스).

판정 로직을 ``can_use_service()`` **한 함수 뒤에 숨긴다** — 호출 지점(페이지 on_load,
API 엔드포인트, state 이벤트)은 정책 저장소가 바뀌어도 손대지 않는다.

**1패스(현재) — 허용목록을 .env 에서 읽는다**::

    AI_SVC_ALLOW_DEPT_REPORT_GENERATOR=A100,B200
    AI_SVC_ALLOW_EMP_REPORT_GENERATOR=100123,100124

- 변수명 접미사 = ``config/ai_services.yaml`` 의 ``id`` 를 대문자화하고 ``-`` → ``_``
  치환 (``report-generator`` → ``REPORT_GENERATOR``).
- **둘 다 비었거나 없으면 전원 허용**(= 기존 동작). 배포만으로는 아무도 차단되지 않고
  .env 에 값을 넣는 순간 게이트가 켜진다. 되돌리기는 값을 비우고 재시작.
- 둘 다 있으면 OR — 부서가 걸리거나 사번이 걸리면 허용.
- 관리자(user_role_nm='ADMIN')도 예외 없음(운영자 결정) — 본인 사번/부서를 목록에
  넣어야 쓸 수 있다.
- 저장소가 config 인 데서 오는 한계(수용): 변경 시 앱 재시작 필요, 부여·회수 **이력
  없음**. 이 둘이 불편해지는 시점이 곧 2패스 전환 시점.

**2패스(Phase 5a) — DDL 완료 후**: 이 파일의 ``_allowlist()`` 조회부만
``ai_use_plcy_n`` 조회(EMP 행 > DEPT 행 > 코드 기본값 + 유효기간)로 교체한다.
시그니처와 호출 지점은 그대로 두고, .env 허용목록은 테이블 행으로 이관 후 제거.

컨벤션 예외(§18 "환경변수는 설정 모듈에서 읽는다"): 허용목록을
``services/core/settings.py`` 가 아닌 이 정책 계층에서 읽는다.
- 이유: 2패스에서 이 조회부가 통째로 DB 질의로 대체되므로, 설정 스키마에 넣으면
  이관 후 쓰이지 않는 설정 필드가 남는다. 서비스 id 별 동적 key 라 정적 스키마와도
  맞지 않는다.
- 위험 완화: 프로세스당 서비스별 1회만 읽고(``_cache``) 파싱을 한 곳에 모은다.
- 제거 조건: 2패스 전환 시 이 블록과 함께 삭제.
"""

from __future__ import annotations

import logging
import os

from wellbot.services.core.settings import get_ai_services

log = logging.getLogger(__name__)

# 서비스 식별자 — config/ai_services.yaml 의 id 와 일치해야 한다.
# (2패스에서는 ai_use_plcy_n.PLCY_OBJ_ID = AGNT_ID 로 대체될 자리)
SVC_REPORT_GENERATOR = "report-generator"
SVC_REPORT_CHECKER = "report-checker"

_ENV_PREFIX_DEPT = "AI_SVC_ALLOW_DEPT_"
_ENV_PREFIX_EMP = "AI_SVC_ALLOW_EMP_"

# service_id -> (허용 부서코드, 허용 사번). 빈 집합 = 해당 축 제한 없음
_cache: dict[str, tuple[frozenset[str], frozenset[str]]] = {}


def _env_suffix(service_id: str) -> str:
    """서비스 id → 환경변수 접미사 (report-generator → REPORT_GENERATOR)."""
    return service_id.strip().replace("-", "_").upper()


def _parse_csv(raw: str | None) -> frozenset[str]:
    """콤마 구분 문자열 → 집합. 공백·빈 항목 제거."""
    if not raw:
        return frozenset()
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def _allowlist(service_id: str) -> tuple[frozenset[str], frozenset[str]]:
    """서비스별 허용목록 조회(프로세스 캐시). 2패스에서 이 함수만 DB 조회로 교체."""
    cached = _cache.get(service_id)
    if cached is not None:
        return cached

    suffix = _env_suffix(service_id)
    entry = (
        _parse_csv(os.getenv(_ENV_PREFIX_DEPT + suffix)),
        _parse_csv(os.getenv(_ENV_PREFIX_EMP + suffix)),
    )
    _cache[service_id] = entry
    if entry[0] or entry[1]:
        log.info(
            "ai service allowlist loaded",
            extra={
                "service_id": service_id,
                "allow_dept_count": len(entry[0]),
                "allow_emp_count": len(entry[1]),
            },
        )
    return entry


def reset_cache() -> None:
    """허용목록 캐시 초기화 (테스트·설정 재적용용)."""
    _cache.clear()


def can_use_service(emp_no: str, dept_cd: str, service_id: str) -> bool:
    """사용자가 해당 AI 서비스를 사용할 수 있는지 여부.

    호출자는 UI 노출 여부와 무관하게 **기능 실행 시점마다** 이 함수를 호출한다
    (페이지 on_load·API 엔드포인트·LLM 을 호출하는 state 이벤트).

    Args:
        emp_no: 사원번호. 비어 있으면(미인증) 항상 False.
        dept_cd: 소속 부서코드. 빈 값이면 부서 허용목록으로는 통과할 수 없다.
        service_id: config/ai_services.yaml 의 서비스 id.

    Returns:
        허용 여부. 해당 서비스의 허용목록이 **설정되지 않았으면 True**(제한 없음).
    """
    if not emp_no or not service_id:
        return False

    allow_depts, allow_emps = _allowlist(service_id)
    if not allow_depts and not allow_emps:
        return True   # 미설정 = 제한 없음
    if emp_no in allow_emps:
        return True
    return bool(dept_cd) and dept_cd in allow_depts


def allowed_service_ids(emp_no: str, dept_cd: str) -> list[str]:
    """카탈로그에 등록된 서비스 중 사용 가능한 id 목록 (카드·네비 표시용)."""
    if not emp_no:
        return []
    return [
        svc.id
        for svc in get_ai_services()
        if can_use_service(emp_no, dept_cd, svc.id)
    ]
