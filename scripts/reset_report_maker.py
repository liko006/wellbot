"""report_maker 저장 내용 초기화 스크립트 (테스트용).

옛 테스트 이력(대화·유형·스타일·AgentCore 기록)이 남아 검증이 어려울 때, 특정 사원의
report_maker 데이터를 한 번에 정리한다. 지우는 대상:

  1. DB  대화/메시지  (chtb_smry_d / chtb_msg_d, AGNT_ID 태깅분) — 하드 삭제
  2. DB  보고서 유형  (agnt_mmry_use_n, AGNT_ID 태깅분, use_yn 무관) — 하드 삭제
  3. S3  report_maker/{emp_no}/*  (스타일 정본·문서·주제첨부·메타 전부)
  4. AgentCore  /writing/{actor}/ · /preference/{actor}/  (유형별 스타일 메모리)

건드리지 않는 것: 메인 WellBot 챗(AGNT_ID 미태깅), 일반 첨부 blob(attachments-* 프리픽스).

안전장치:
  - 기본은 DRY-RUN (무엇을 지울지 출력만). 실제 삭제는 --yes 필요.
  - --emp-no 로 대상 사원을 명시(본인 테스트 데이터). --all 은 경고 + --yes 이중 확인.
  - 실행 전 대상 S3 프리픽스를 출력 → 잘못된 환경(운영) 오삭제 방지.

Usage:
    uv run --native-tls python scripts/reset_report_maker.py --emp-no ~             # dry-run
    uv run --native-tls python scripts/reset_report_maker.py --emp-no ~ --yes       # 실제 삭제
    uv run --native-tls python scripts/reset_report_maker.py --all --yes            # 전체(주의)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wellbot.env import init_env

init_env()  # .env 로드 (DB_URL / S3 / AgentCore 설정)

from wellbot.models.agent_memory import AgentMemory  # noqa: E402
from wellbot.models.chat_summary import ChatSummary  # noqa: E402
from wellbot.models.chat_message import ChatMessage  # noqa: E402
from wellbot.services.core.database import get_session  # noqa: E402
from wellbot.services.files import storage_service  # noqa: E402
from wellbot.services.report_maker import db as rmdb  # noqa: E402
from wellbot.services.report_maker import memory, storage  # noqa: E402
from wellbot.services.report_maker.config import get_config  # noqa: E402


def _agnt_id() -> str:
    return get_config().agent_id


def _template_rows(session, emp_no: str):
    """emp_no 의 report_maker 유형 행 전체(use_yn 무관)."""
    return (
        session.query(AgentMemory)
        .filter(AgentMemory.agnt_id == _agnt_id(), AgentMemory.emp_no == emp_no)
        .all()
    )


def _all_emp_nos() -> list[str]:
    """report_maker 데이터가 있는 모든 사원번호(유형 + 태깅 대화 기준)."""
    with get_session() as session:
        tpl = {
            r.emp_no
            for r in session.query(AgentMemory.emp_no)
            .filter(AgentMemory.agnt_id == _agnt_id())
            .distinct()
        }
        tagged_smry = session.query(ChatMessage.chtb_tlk_smry_id).filter(
            ChatMessage.agnt_id == _agnt_id()
        ).distinct()
        conv = {
            r.emp_no
            for r in session.query(ChatSummary.emp_no)
            .filter(ChatSummary.chtb_tlk_smry_id.in_(tagged_smry))
            .distinct()
        }
    return sorted(tpl | conv)


def plan(emp_no: str) -> dict:
    """삭제 대상 집계(읽기만)."""
    with get_session() as session:
        rows = _template_rows(session, emp_no)
        tids = [rmdb._parse_template_row(r)["id"] for r in rows]
        tids = [t for t in tids if t]
    convs = rmdb.list_conversations(emp_no)
    return {
        "emp_no": emp_no,
        "template_ids": tids,
        "template_row_count": len(tids),
        "conversation_count": len(convs),
        "conversation_ids": [c["id"] for c in convs],
        "s3_prefix": storage.emp_prefix(emp_no),
    }


def reset_emp(emp_no: str, dry_run: bool) -> None:
    p = plan(emp_no)
    print(f"\n{'=' * 66}")
    print(f"emp_no = {emp_no}")
    print(f"  · 보고서 유형 행 : {p['template_row_count']}개  {p['template_ids']}")
    print(f"  · 대화(보고서)   : {p['conversation_count']}개")
    print(f"  · S3 프리픽스     : {p['s3_prefix']}   (이하 전부 삭제)")
    print(f"  · AgentCore       : 유형별 /writing·/preference 레코드")
    print(f"{'=' * 66}")

    if dry_run:
        print("[DRY-RUN] 실제 삭제 안 함. 삭제하려면 --yes 를 붙이세요.")
        return

    # 1) AgentCore(+유형별 스타일 S3) — 유형 삭제 전에 actor 로 정리
    ac_deleted = 0
    for tid in p["template_ids"]:
        try:
            ac_deleted += memory.clear_style(emp_no, tid)
        except Exception as exc:  # 네트워크/권한 실패해도 계속
            print(f"  [WARN] AgentCore 정리 실패 tid={tid}: {exc}")

    # 2) S3 report_maker/{emp_no}/* 전부
    s3_deleted = storage_service.delete_prefix(p["s3_prefix"])

    # 3) DB 대화/메시지 (하드 삭제)
    for cid in p["conversation_ids"]:
        rmdb.delete_conversation(cid, emp_no)

    # 4) DB 유형 행 하드 삭제 (use_yn 무관)
    with get_session() as session:
        tpl_deleted = (
            session.query(AgentMemory)
            .filter(AgentMemory.agnt_id == _agnt_id(), AgentMemory.emp_no == emp_no)
            .delete(synchronize_session=False)
        )

    print(
        f"[DONE] emp_no={emp_no} — 대화 {p['conversation_count']}개, 유형 {tpl_deleted}행, "
        f"S3 {s3_deleted}객체, AgentCore {ac_deleted}레코드 삭제"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="report_maker 저장 내용 초기화(테스트용)")
    ap.add_argument("--emp-no", help="대상 사원번호")
    ap.add_argument("--all", action="store_true", help="report_maker 데이터가 있는 모든 사원(주의)")
    ap.add_argument("--yes", action="store_true", help="실제 삭제 수행(미지정 시 DRY-RUN)")
    args = ap.parse_args()

    if not args.emp_no and not args.all:
        ap.error("--emp-no 또는 --all 중 하나는 필수입니다.")
    if args.emp_no and args.all:
        ap.error("--emp-no 와 --all 은 함께 쓸 수 없습니다.")

    dry_run = not args.yes

    if args.all:
        emp_nos = _all_emp_nos()
        print(f"대상 사원 {len(emp_nos)}명: {emp_nos}")
        if not dry_run:
            print("!! --all 로 위 모든 사원의 report_maker 데이터를 삭제합니다 !!")
    else:
        emp_nos = [args.emp_no]

    if not emp_nos:
        print("대상 데이터가 없습니다.")
        return 0

    for emp_no in emp_nos:
        reset_emp(emp_no, dry_run)

    if dry_run:
        print("\n※ DRY-RUN 이었습니다. 실제 삭제하려면 --yes 를 추가하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
