"""report_checker 사용 내역 DB 기록.

신규 테이블 없이 기존 채팅 테이블(chtb_smry_d / chtb_msg_d)을 재활용해
"누가·언제·어떤 파일·토큰 얼마" 원장을 남긴다. 메시지에 agnt_id 를 태깅하므로
채팅 대화 목록(list_conversations)에서는 agnt_id 기준으로 제외된다.

DB/ORM 의존을 이 파일 하나로 격리한다(모듈 자기완결 유지).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from wellbot.constants import KST
from wellbot.models.agent import Agent
from wellbot.models.chat_message import ChatMessage
from wellbot.models.chat_summary import ChatSummary
from wellbot.services.core.database import get_session

log = logging.getLogger(__name__)


def _agent_name(session, agent_id: str) -> str:
    """agnt_m 에서 에이전트명 조회 (없으면 agent_id 그대로)."""
    row = (
        session.query(Agent)
        .filter(Agent.agnt_id == agent_id)
        .order_by(Agent.agnt_seq)
        .first()
    )
    return (row.agnt_nm if row and row.agnt_nm else agent_id) or agent_id


def record_usage(
    *,
    emp_no: str,
    agent_id: str,
    source_file: str,
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    elapsed_sec: float,
    status: str,
    summary: str,
) -> None:
    """분석 1건을 chtb_smry_d + chtb_msg_d 에 기록 (best-effort).

    실패해도 예외를 밖으로 던지지 않는다(분석 결과 반환에 영향 없게).
    """
    if not emp_no:
        return
    try:
        now = datetime.now(KST)
        smry_id = uuid.uuid4().hex[:50]
        tlk_id = uuid.uuid4().hex[:50]
        rgsr = emp_no[:20]
        with get_session() as session:
            name = _agent_name(session, agent_id)
            title = f"{name} · {source_file}"
            if status != "done":
                title = f"{title} ({status})"

            session.add(
                ChatSummary(
                    chtb_tlk_smry_id=smry_id,
                    emp_no=emp_no,
                    chtb_tlk_smry_ttl=title[:500],
                    chtb_mdl_nm=(model_name or None),
                    bkmr_yn="N",
                    rgsr_id=rgsr,
                    rgst_dtm=now,
                    uppr_id=rgsr,
                    upd_dtm=now,
                )
            )
            session.add(
                ChatMessage(
                    chtb_tlk_smry_id=smry_id,
                    chtb_tlk_id=tlk_id,
                    chtb_tlk_seq=1,
                    agnt_id=agent_id,  # 에이전트 태깅 → 채팅 목록에서 제외 기준
                    msg_role_nm="assistant",
                    chtb_msg_cntt=summary,
                    chtb_mdl_nm=(model_name or None),
                    chtb_inpt_tokn_ecnt=input_tokens or None,
                    chtb_otpt_tokn_ecnt=output_tokens or None,
                    chtb_tot_tokn_ecnt=total_tokens or None,
                    rgsr_id=rgsr,
                    rgst_dtm=now,
                    uppr_id=rgsr,
                    upd_dtm=now,
                )
            )
        log.info(
            "report_checker DB 기록 완료 emp_no=%s agnt_id=%s status=%s tokens=%d",
            emp_no, agent_id, status, total_tokens,
        )
    except Exception:  # noqa: BLE001 - 기록 실패가 분석을 막지 않도록
        log.exception("report_checker DB 기록 실패 emp_no=%s status=%s", emp_no, status)
