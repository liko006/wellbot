"""report_maker DB CRUD (MySQL).

대화/메시지는 기존 채팅 테이블(chtb_smry_d / chtb_msg_d)을 재사용하되 메시지에
AGNT_ID(=get_config().agent_id, 기본 RPT_DRFT_GEN)를 태깅해 메인 채팅과 분리한다
(메인 사이드바는 agnt_id 태깅 대화를 제외하므로 자동 분리됨). 보고서 유형(템플릿)은
agnt_mmry_use_n 을 재사용한다.

신원(emp_no)은 항상 서버가 세션에서 도출한 값이어야 한다(클라이언트 문자열 신뢰 금지).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from wellbot.constants import CONVERSATION_LIMIT, KST, MESSAGE_SEQ_MAX_RETRIES
from wellbot.models.agent_memory import AgentMemory
from wellbot.models.chat_message import ChatMessage
from wellbot.models.chat_summary import ChatSummary
from wellbot.services.core.database import get_session
from wellbot.services.report_maker.config import get_config

log = logging.getLogger(__name__)


def _agnt_id() -> str:
    """DB 태깅·조회에 쓰는 에이전트 식별자 (yaml agent_id, 미설정 시 기본값 RPT_DRFT_GEN)."""
    return get_config().agent_id


# ══════════════════════════════════════════════════════════════
# 대화 / 메시지 (chtb_smry_d / chtb_msg_d, AGNT_ID 태깅)
# ══════════════════════════════════════════════════════════════
def _verify_ownership(session, smry_id: str, emp_no: str) -> ChatSummary | None:
    return (
        session.query(ChatSummary)
        .filter(ChatSummary.chtb_tlk_smry_id == smry_id, ChatSummary.emp_no == emp_no)
        .first()
    )


def list_conversations(emp_no: str) -> list[dict]:
    """report_maker 대화 목록(최근순). AGNT_ID 태깅 메시지가 있는 대화만."""
    with get_session() as session:
        smry_ids = (
            session.query(ChatMessage.chtb_tlk_smry_id)
            .filter(ChatMessage.agnt_id == _agnt_id())
            .distinct()
        )
        rows = (
            session.query(ChatSummary)
            .filter(
                ChatSummary.emp_no == emp_no,
                ChatSummary.chtb_tlk_smry_id.in_(smry_ids),
            )
            .order_by(ChatSummary.rgst_dtm.desc())
            .limit(CONVERSATION_LIMIT)
            .all()
        )
        return [
            {
                "id": r.chtb_tlk_smry_id,
                "title": r.chtb_tlk_smry_ttl or "새 보고서",
                "created_at": r.rgst_dtm.timestamp() if r.rgst_dtm else 0.0,
                "created_label": r.rgst_dtm.strftime("%Y.%m.%d") if r.rgst_dtm else "",
                # CHTB_MDL_NM 재활용 — 대화가 속한 보고서 유형(없으면 legacy 대화)
                "template_id": r.chtb_mdl_nm or "",
            }
            for r in rows
        ]


def get_conversation_messages(smry_id: str, emp_no: str) -> list[dict]:
    """대화 메시지(시간순). 소유권 검증 + AGNT_ID 태깅 메시지만.

    role='usage' 행(보조 LLM 호출 토큰 집계용)은 대화가 아니므로 제외한다.
    """
    with get_session() as session:
        if not _verify_ownership(session, smry_id, emp_no):
            return []
        rows = (
            session.query(ChatMessage)
            .filter(
                ChatMessage.chtb_tlk_smry_id == smry_id,
                ChatMessage.agnt_id == _agnt_id(),
                ChatMessage.msg_role_nm != "usage",
            )
            .order_by(
                ChatMessage.chtb_tlk_seq.asc(),
                ChatMessage.rgst_dtm.asc(),
                ChatMessage.chtb_tlk_id.asc(),
            )
            .all()
        )
        return [
            {
                "role": r.msg_role_nm or "user",
                "content": r.chtb_msg_cntt or "",
                "timestamp": r.rgst_dtm.timestamp() if r.rgst_dtm else 0.0,
                "seq": int(r.chtb_tlk_seq) if r.chtb_tlk_seq is not None else 0,
                "msg_id": r.chtb_tlk_id or "",
            }
            for r in rows
        ]


def save_conversation(emp_no: str, conv_id: str, title: str, template_id: str = "") -> None:
    """대화 헤더 upsert (없으면 INSERT, 있으면 소유자 확인 후 UPDATE).

    report_maker 대화 헤더는 생성 모델명을 쓰지 않으므로(모델은 메시지 레벨 저장),
    CHTB_MDL_NM 컬럼을 재활용해 대화가 속한 보고서 유형(template_id)을 기록한다.
    이걸로 대화를 불러올 때 유형·스타일을 원래대로 맞춘다.
    """
    now = datetime.now(KST)
    with get_session() as session:
        existing = _verify_ownership(session, conv_id, emp_no)
        if existing:
            existing.chtb_tlk_smry_ttl = title
            existing.chtb_mdl_nm = template_id or existing.chtb_mdl_nm
            existing.upd_dtm = now
            existing.uppr_id = emp_no[:20]
        else:
            session.add(
                ChatSummary(
                    chtb_tlk_smry_id=conv_id,
                    emp_no=emp_no,
                    chtb_tlk_smry_ttl=title,
                    chtb_mdl_nm=template_id or None,
                    bkmr_yn="N",
                    rgsr_id=emp_no[:20],
                    rgst_dtm=now,
                    uppr_id=emp_no[:20],
                    upd_dtm=now,
                )
            )


def update_conversation_title(smry_id: str, title: str, emp_no: str) -> None:
    with get_session() as session:
        record = _verify_ownership(session, smry_id, emp_no)
        if record:
            record.chtb_tlk_smry_ttl = title
            record.upd_dtm = datetime.now(KST)
            record.uppr_id = emp_no[:20]


def append_message(
    smry_id: str,
    role: str,
    content: str,
    emp_no: str,
    model_name: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    reply_time: float | None = None,
    msg_id: str | None = None,
) -> str:
    """메시지 저장(seq 자동 발급, AGNT_ID 태깅).

    role 은 "user" | "assistant" | "outline"(최종 아웃라인) 을 쓴다 — 재조회 시
    "outline" 을 아웃라인 메시지로 복원한다(flow_state 는 저장하지 않으므로 이걸로
    편집 이어가기를 복원).
    """
    total_tokens = input_tokens + output_tokens
    tlk_id = msg_id or uuid.uuid4().hex[:50]

    for attempt in range(MESSAGE_SEQ_MAX_RETRIES):
        try:
            now = datetime.now(KST)
            with get_session() as session:
                max_seq = (
                    session.query(func.max(ChatMessage.chtb_tlk_seq))
                    .filter(ChatMessage.chtb_tlk_smry_id == smry_id)
                    .scalar()
                )
                seq = (int(max_seq) if max_seq is not None else 0) + 1
                session.add(
                    ChatMessage(
                        chtb_tlk_smry_id=smry_id,
                        chtb_tlk_id=tlk_id,
                        chtb_tlk_seq=seq,
                        agnt_id=_agnt_id(),
                        msg_role_nm=role,
                        chtb_msg_cntt=content,
                        chtb_mdl_nm=model_name or None,
                        chtb_inpt_tokn_ecnt=input_tokens or None,
                        chtb_otpt_tokn_ecnt=output_tokens or None,
                        chtb_tot_tokn_ecnt=total_tokens or None,
                        rply_time=Decimal(str(reply_time)) if reply_time else None,
                        rgsr_id=emp_no[:20],
                        rgst_dtm=now,
                        uppr_id=emp_no[:20],
                        upd_dtm=now,
                    )
                )
            return tlk_id
        except IntegrityError:
            if attempt == MESSAGE_SEQ_MAX_RETRIES - 1:
                raise
            continue
    return tlk_id


def record_usage(
    smry_id: str,
    emp_no: str,
    action: str,
    input_tokens: int,
    output_tokens: int,
    model_name: str = "",
) -> None:
    """비대화(보조) LLM 호출의 토큰 사용량을 chtb_msg_d 에 role='usage' 행으로 기록.

    스키마·구조를 유지하기 위해 기존 메시지 테이블·토큰 컬럼을 재사용한다(별도 usage
    테이블 없음). role='usage' 라 대화 조회(get_conversation_messages)에서 제외되므로
    화면엔 뜨지 않고 집계(SUM)에만 잡힌다. chtb_msg_cntt 에 action 라벨(slide 등)을 남긴다.
    smry_id/emp_no 가 없거나 토큰이 0 이면 기록하지 않는다(best-effort).
    """
    if not smry_id or not emp_no:
        return
    if (input_tokens or 0) + (output_tokens or 0) <= 0:
        return
    append_message(
        smry_id, "usage", (action or "llm")[:200], emp_no,
        model_name=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def delete_conversation(smry_id: str, emp_no: str) -> None:
    """대화 및 메시지 삭제(소유권 검증). report_maker 메시지만 삭제 후 헤더 제거."""
    with get_session() as session:
        if not _verify_ownership(session, smry_id, emp_no):
            return
        session.query(ChatMessage).filter(
            ChatMessage.chtb_tlk_smry_id == smry_id,
            ChatMessage.agnt_id == _agnt_id(),
        ).delete()
        session.query(ChatSummary).filter(
            ChatSummary.chtb_tlk_smry_id == smry_id
        ).delete()


# ══════════════════════════════════════════════════════════════
# 보고서 유형(템플릿) — agnt_mmry_use_n 재사용
#   agnt_mmry_path_addr : AgentCore actor_id (스타일 네임스페이스 루트)
#   agnt_type_dscr_cntt : JSON {"template_id": <safe_id>, "display": <표시명>}
# ══════════════════════════════════════════════════════════════
def _parse_template_row(r: AgentMemory) -> dict:
    meta = {}
    try:
        meta = json.loads(r.agnt_type_dscr_cntt or "{}")
    except (ValueError, TypeError):
        meta = {}
    return {
        "id": meta.get("template_id", ""),
        "display": meta.get("display", meta.get("template_id", "")),
        "actor": r.agnt_mmry_path_addr or "",
        "seq": int(r.agnt_seq) if r.agnt_seq is not None else 0,
    }


def list_templates(emp_no: str) -> list[dict]:
    """emp_no 의 보고서 유형 목록(표시명 오름차순)."""
    with get_session() as session:
        rows = (
            session.query(AgentMemory)
            .filter(
                AgentMemory.agnt_id == _agnt_id(),
                AgentMemory.emp_no == emp_no,
                AgentMemory.use_yn == "Y",
            )
            .all()
        )
        items = [_parse_template_row(r) for r in rows]
        items = [t for t in items if t["id"]]
        return sorted(items, key=lambda t: t["display"])


def get_template(emp_no: str, template_id: str) -> dict | None:
    for t in list_templates(emp_no):
        if t["id"] == template_id:
            return t
    return None


def save_template(emp_no: str, template_id: str, display_name: str, actor_id: str) -> bool:
    """보고서 유형 upsert. 신규는 max_templates 초과 시 False 반환."""
    now = datetime.now(KST)
    dscr = json.dumps({"template_id": template_id, "display": display_name}, ensure_ascii=False)
    with get_session() as session:
        rows = (
            session.query(AgentMemory)
            .filter(AgentMemory.agnt_id == _agnt_id(), AgentMemory.emp_no == emp_no)
            .all()
        )
        # 기존 template_id 매칭 → 업데이트
        for r in rows:
            meta = _parse_template_row(r)
            if meta["id"] == template_id:
                r.agnt_type_dscr_cntt = dscr
                r.agnt_mmry_path_addr = actor_id
                r.use_yn = "Y"
                r.upd_dtm = now
                r.uppr_id = emp_no[:20]
                return True
        # 신규 — 개수 제한 확인
        active = [r for r in rows if (r.use_yn or "Y") == "Y"]
        if len(active) >= get_config().max_templates:
            log.warning("템플릿 개수 초과 emp_no=%s (max=%d)", emp_no, get_config().max_templates)
            return False
        max_seq = max((int(r.agnt_seq) for r in rows), default=0)
        session.add(
            AgentMemory(
                agnt_id=_agnt_id(),
                agnt_seq=max_seq + 1,
                emp_no=emp_no,
                agnt_mmry_path_addr=actor_id,
                agnt_type_dscr_cntt=dscr,
                use_yn="Y",
                last_sync_dtm=now,
                rgst_dtm=now,
                rgsr_id=emp_no[:20],
                upd_dtm=now,
                uppr_id=emp_no[:20],
            )
        )
        return True


def delete_template(emp_no: str, template_id: str) -> None:
    """보고서 유형 비활성화(use_yn='N'). S3 파일 정리는 storage 계층에서 별도 수행."""
    now = datetime.now(KST)
    with get_session() as session:
        rows = (
            session.query(AgentMemory)
            .filter(AgentMemory.agnt_id == _agnt_id(), AgentMemory.emp_no == emp_no)
            .all()
        )
        for r in rows:
            if _parse_template_row(r)["id"] == template_id:
                r.use_yn = "N"
                r.upd_dtm = now
                r.uppr_id = emp_no[:20]
