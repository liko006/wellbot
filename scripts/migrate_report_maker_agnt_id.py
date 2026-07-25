"""
migrate_report_maker_agnt_id.py

report_maker 서비스의 DB 태그(AGNT_ID)를 구 값 'report-maker' → 신 값 'RPT_DRFT_GEN'
으로 일괄 변경하는 일회성 마이그레이션 스크립트.

배경:
    report_maker.yaml 의 agent_id 를 'RPT_DRFT_GEN' 으로 바꾸고, db.py 가 이제
    get_config().agent_id 로 태깅·조회하도록 수정했다. 기존에 'report-maker' 로
    태깅되어 저장된 대화·메시지(chtb_msg_d)와 보고서 유형(agnt_mmry_use_n)은
    새 코드의 조회 필터에 걸리지 않으므로 태그를 옮겨준다.

대상 테이블:
    - chtb_msg_d.AGNT_ID          (PK 아님 — 단순 UPDATE)
    - agnt_mmry_use_n.AGNT_ID     (복합 PK 의 일부지만 RPT_DRFT_GEN 은 신규 값이라 충돌 없음)

특징:
    - 멱등: 이미 옮겨졌으면 0건 처리로 안전
    - --dry-run: 몇 건이 바뀔지 미리보기만(실제 변경 없음)
    - --yes: 확인 프롬프트 건너뛰기(자동화 호출용)

사용 예:
    python scripts/migrate_report_maker_agnt_id.py --dry-run
    python scripts/migrate_report_maker_agnt_id.py
    python scripts/migrate_report_maker_agnt_id.py --yes
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

init_env()  # 다른 wellbot 모듈 import 전에 호출 (모듈레벨 os.getenv 보장)

from wellbot.models.agent_memory import AgentMemory  # noqa: E402
from wellbot.models.chat_message import ChatMessage  # noqa: E402
from wellbot.services.core.database import get_session  # noqa: E402

OLD_AGNT_ID = "report-maker"
NEW_AGNT_ID = "RPT_DRFT_GEN"


def _count_old() -> tuple[int, int]:
    """구 태그로 남아있는 행 수 (chtb_msg_d, agnt_mmry_use_n)."""
    with get_session() as session:
        msgs = (
            session.query(ChatMessage)
            .filter(ChatMessage.agnt_id == OLD_AGNT_ID)
            .count()
        )
        tmpls = (
            session.query(AgentMemory)
            .filter(AgentMemory.agnt_id == OLD_AGNT_ID)
            .count()
        )
    return msgs, tmpls


def _migrate() -> tuple[int, int]:
    """구 태그 → 신 태그로 UPDATE. 변경된 행 수 반환."""
    with get_session() as session:
        msgs = (
            session.query(ChatMessage)
            .filter(ChatMessage.agnt_id == OLD_AGNT_ID)
            .update({ChatMessage.agnt_id: NEW_AGNT_ID}, synchronize_session=False)
        )
        tmpls = (
            session.query(AgentMemory)
            .filter(AgentMemory.agnt_id == OLD_AGNT_ID)
            .update({AgentMemory.agnt_id: NEW_AGNT_ID}, synchronize_session=False)
        )
    return msgs, tmpls


def main() -> int:
    parser = argparse.ArgumentParser(description="report_maker AGNT_ID 마이그레이션")
    parser.add_argument("--dry-run", action="store_true", help="변경 없이 대상 건수만 출력")
    parser.add_argument("--yes", action="store_true", help="확인 프롬프트 건너뛰기")
    args = parser.parse_args()

    msgs, tmpls = _count_old()
    print(f"대상: chtb_msg_d {msgs}건, agnt_mmry_use_n {tmpls}건 "
          f"('{OLD_AGNT_ID}' → '{NEW_AGNT_ID}')")

    if msgs == 0 and tmpls == 0:
        print("옮길 데이터가 없습니다. (이미 마이그레이션됨)")
        return 0

    if args.dry_run:
        print("[dry-run] 실제 변경 없음.")
        return 0

    if not args.yes:
        resp = input("진행할까요? [y/N] ").strip().lower()
        if resp != "y":
            print("취소되었습니다.")
            return 1

    m, t = _migrate()
    print(f"완료: chtb_msg_d {m}건, agnt_mmry_use_n {t}건 변경.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
