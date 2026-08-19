"""
cleanup_kb.py

개인 또는 팀 Knowledge Base 관련 리소스를 일괄 정리하는 관리자 CLI
(퇴사자 정리, 팀 해체 등).

**로직은 ``wellbot/services/knowledgebase/kb_cleanup_service.py`` 에 있고 이
스크립트는 얇은 래퍼다** — 인자 파싱, 확인 프롬프트, 결과 출력만 한다. 같은
서비스를 admin UI 도 호출하므로 동작은 한 곳에서만 바뀐다.

삭제 대상 (의존성 역순):
    1. Bedrock Data Source
    2. Bedrock Knowledge Base
    3. S3 Vectors Index
    4. S3 main bucket 의 users{env}/{emp_no}/ · teams{env}/{dept_cd}/
    5. S3 intermediate bucket 의 .../processed/
    6. DB 의 AGNT_MMRY_USE_N 행 (개인=본인 1행 / 팀=그 KB 를 쓰는 전원)

리소스 이름과 S3 경로는 kb_utils 의 헬퍼로 만든다 — 생성할 때와 같은 이름을 써야
하고, 특히 APP_ENV 접미사(env_suffix)가 빠지면 다른 환경의 리소스를 지우게 된다.
따라서 이 스크립트는 실행 환경의 .env(APP_ENV)를 대상 환경에 맞춰 실행해야 한다.

특징:
    - 멱등: DB 행이 이미 없어도 이름으로 조회해 남은 리소스를 정리
    - --dry-run: 어떤 리소스가 삭제될지 미리보기만 (실제 삭제 없음)
    - --yes: 확인 프롬프트 건너뛰기 (자동화 호출용)
    - 실패 시 그 단계에서 중단 (재실행하면 이어서 정리)

사용 예:
    # 개인 — 미리보기
    python scripts/cleanup_kb.py --emp-no 12345678 --dry-run

    # 개인 — 실제 삭제 (y/N 프롬프트)
    python scripts/cleanup_kb.py --emp-no 12345678

    # 팀 — 부서 전원의 팀 KB 정리 (영향 범위가 크므로 미리보기 먼저)
    python scripts/cleanup_kb.py --dept-cd D0012 --dry-run

    # 프롬프트 건너뛰기
    python scripts/cleanup_kb.py --emp-no 12345678 --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path 에 추가 (scripts/ 에서 직접 실행하기 위한 목적)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wellbot.env import init_env  # noqa: E402
init_env()  # KB 모듈의 모듈레벨 os.getenv 보장 (다른 wellbot import 전에 호출)

from wellbot.services.knowledgebase import kb_cleanup_service as svc  # noqa: E402

_STATUS_MARK = {
    svc.STEP_DONE: "✓",
    svc.STEP_SKIPPED: "✓ (스킵)",
    svc.STEP_FAILED: "✗",
}


def print_preview(plan: svc.CleanupPlan) -> None:
    """삭제될 리소스 미리보기 출력."""
    scope_label = "개인" if plan.kind == svc.KIND_PERSONAL else "팀"
    target_label = "emp_no" if plan.kind == svc.KIND_PERSONAL else "dept_cd"
    print(f"\n📋 다음 리소스가 삭제됩니다 ({scope_label} / {target_label}={plan.owner}):")

    if plan.db_row_count:
        row_note = "전원" if plan.kind == svc.KIND_TEAM else ""
        print(f"   - DB 행: AGNT_MMRY_USE_N {plan.db_row_count}건 {row_note}".rstrip())
    else:
        print("   - DB 행: (없음 - 스킵)")

    if plan.kb_id:
        print(f"   - Bedrock KB: kb_id={plan.kb_id}")
        print(f"   - Data Source: ds_id={plan.data_source_id or '(없음)'}")
    else:
        print("   - Bedrock KB / Data Source: (없음 - 스킵)")

    print(f"   - S3 Vectors Index: {plan.vector_index}")
    print(
        f"   - S3 main: s3://{plan.main_bucket}/{plan.main_prefix} "
        f"({len(plan.main_keys)}개 객체)"
    )
    print(
        f"   - S3 intermediate: s3://{plan.intermediate_bucket}/{plan.intermediate_prefix} "
        f"({len(plan.intermediate_keys)}개 객체)"
    )

    if plan.kind == svc.KIND_TEAM and plan.db_row_count > 1:
        print(f"\n⚠ 부서 전원({plan.db_row_count}명)의 팀 KB 가 삭제됩니다.")


def run_cleanup(plan: svc.CleanupPlan) -> bool:
    """teardown 실행 + 단계별 진행 출력. 반환: 실패 단계가 있었는지.

    서비스가 단계마다 결과를 내보내므로 받는 즉시 찍는다 — DS/KB 삭제 완료 대기로
    한 단계가 수십 초를 쓸 수 있어, 어디서 기다리는 중인지 보여야 한다.
    """
    failed = False
    for i, step in enumerate(svc.execute_cleanup(plan), start=1):
        mark = _STATUS_MARK.get(step.status, step.status)
        detail = f" — {step.detail}" if step.detail else ""
        print(f"[{i}/{svc.TOTAL_STEPS}] {step.name}... {mark}{detail}", flush=True)
        failed = failed or step.is_failed
    return failed


def _parse_args():
    parser = argparse.ArgumentParser(
        description="개인/팀 KB 클린업 스크립트 (퇴사자 정리, 팀 해체 등)",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--emp-no", help="개인 KB 정리 대상 사번")
    target.add_argument("--dept-cd", help="팀 KB 정리 대상 부서코드")
    parser.add_argument(
        "--dry-run", action="store_true", help="실제 삭제 없이 미리보기만 출력",
    )
    parser.add_argument(
        "--yes", action="store_true", help="확인 프롬프트 건너뛰기 (자동화 호출용)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.emp_no:
        kind, owner = svc.KIND_PERSONAL, args.emp_no.strip()
    else:
        kind, owner = svc.KIND_TEAM, args.dept_cd.strip()
    if not owner:
        print("✗ 대상 값이 비어 있습니다.")
        sys.exit(1)

    try:
        plan = svc.gather_resources(kind, owner)
    except Exception as e:  # noqa: BLE001 - CLI 최상위 경계
        print(f"✗ 삭제 대상 조회 실패: {e}")
        sys.exit(1)

    print_preview(plan)

    if plan.has_nothing_to_delete:
        # S3 Vectors index 존재 여부는 조회 API 없이는 확신 불가 —
        # 나머지가 모두 비었으면 사실상 정리할 게 없다고 보고 알린다.
        print("\nℹ 정리할 리소스가 없는 것 같습니다. (S3 Vectors index 는 별도로 시도해주세요)")

    if args.dry_run:
        print("\n🔍 --dry-run 모드: 실제 삭제 없이 종료합니다.")
        return

    print("\n⚠ 이 작업은 되돌릴 수 없습니다.")
    if not args.yes:
        try:
            answer = input("정말 삭제하시겠습니까? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "y":
            print("취소되었습니다.")
            return

    print()
    if run_cleanup(plan):
        print("\n✗ 중단되었습니다. 원인을 해결한 뒤 같은 명령을 다시 실행하면 "
              "남은 리소스부터 이어서 정리합니다.")
        sys.exit(1)
    print(f"\n✅ 정리 완료: {kind}={owner}")


if __name__ == "__main__":
    main()
