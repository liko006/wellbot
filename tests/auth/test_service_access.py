"""can_use_service — AI 서비스 접근 게이트 (WB-SEC-004 1패스).

허용목록은 .env 에서 읽고, **미설정이면 전원 허용**(배포만으로는 아무도 차단되지 않음)
이라는 성질이 이 기능의 안전판이다. 이 성질과 부서/사번 OR 판정이 깨지면 배포 즉시
전원 차단 또는 전원 통과 사고가 되므로 회귀 테스트로 고정한다.

관리자(``user_role_nm='ADMIN'``)에게도 예외를 두지 않는다(운영자 결정) — 판정 입력이
``emp_no``·``dept_cd`` 뿐이라는 점 자체가 그 보장이다.
"""

from collections.abc import Callable, Iterator

import pytest

from wellbot.services.auth import policy_service

SVC = policy_service.SVC_REPORT_GENERATOR
_DEPT_KEY = "AI_SVC_ALLOW_DEPT_REPORT_GENERATOR"
_EMP_KEY = "AI_SVC_ALLOW_EMP_REPORT_GENERATOR"


@pytest.fixture
def set_allowlist(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    """허용목록 환경변수를 설정하고 판정 캐시를 비우는 헬퍼를 반환한다."""

    def _set(dept: str | None = None, emp: str | None = None) -> None:
        for key, value in ((_DEPT_KEY, dept), (_EMP_KEY, emp)):
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        policy_service.reset_cache()

    _set()
    yield _set
    policy_service.reset_cache()


def test_unset_allowlist_allows_everyone(set_allowlist: Callable[..., None]) -> None:
    """게이트를 켜지 않은 상태 = 기존 동작. 배포 자체로는 아무도 막히지 않는다."""
    set_allowlist()
    assert policy_service.can_use_service("100123", "A100", SVC) is True


def test_empty_allowlist_allows_everyone(set_allowlist: Callable[..., None]) -> None:
    """.env 에 key 만 있고 값이 비면 제한 없음으로 본다(원복 경로)."""
    set_allowlist(dept="", emp="")
    assert policy_service.can_use_service("100123", "A100", SVC) is True


def test_missing_emp_no_is_denied(set_allowlist: Callable[..., None]) -> None:
    set_allowlist()
    assert policy_service.can_use_service("", "A100", SVC) is False


def test_dept_allowlist_allows_only_listed_depts(set_allowlist: Callable[..., None]) -> None:
    set_allowlist(dept="A100, B200")   # 값 사이 공백 허용
    assert policy_service.can_use_service("100123", "A100", SVC) is True
    assert policy_service.can_use_service("100123", "B200", SVC) is True
    assert policy_service.can_use_service("100123", "C300", SVC) is False
    assert policy_service.can_use_service("100123", "", SVC) is False


def test_emp_allowlist_allows_only_listed_emps(set_allowlist: Callable[..., None]) -> None:
    set_allowlist(emp="100123,100124")
    assert policy_service.can_use_service("100123", "C300", SVC) is True
    assert policy_service.can_use_service("999999", "C300", SVC) is False


def test_dept_and_emp_allowlists_are_or(set_allowlist: Callable[..., None]) -> None:
    set_allowlist(dept="A100", emp="999999")
    assert policy_service.can_use_service("100123", "A100", SVC) is True   # 부서로 통과
    assert policy_service.can_use_service("999999", "C300", SVC) is True   # 사번으로 통과
    assert policy_service.can_use_service("100123", "C300", SVC) is False


def test_other_service_keeps_its_own_default(set_allowlist: Callable[..., None]) -> None:
    """서비스별로 독립 — 한쪽을 제한해도 다른 쪽은 기본(전원 허용)을 유지한다."""
    set_allowlist(dept="A100")
    assert policy_service.can_use_service(
        "100123", "C300", policy_service.SVC_REPORT_CHECKER,
    ) is True


def test_empty_service_id_is_denied(set_allowlist: Callable[..., None]) -> None:
    set_allowlist()
    assert policy_service.can_use_service("100123", "A100", "") is False


def test_allowed_service_ids_lists_only_permitted(set_allowlist: Callable[..., None]) -> None:
    """카탈로그(config/ai_services.yaml) 기준 목록 — 카드·사이드바 노출에 사용."""
    set_allowlist(dept="A100")
    denied = policy_service.allowed_service_ids("100123", "C300")
    assert SVC not in denied
    assert policy_service.SVC_REPORT_CHECKER in denied
    assert SVC in policy_service.allowed_service_ids("100123", "A100")
    assert policy_service.allowed_service_ids("", "A100") == []
