"""챗봇메시지상세 ORM 모델 (DB 테이블: chtb_msg_d)"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ChatMessage(Base):
    """챗봇메시지상세 (DB 테이블: chtb_msg_d)"""

    __tablename__ = "chtb_msg_d"

    chtb_tlk_smry_id: Mapped[str] = mapped_column(
        "CHTB_TLK_SMRY_ID", String(50), primary_key=True, comment="챗봇대화요약아이디",
    )
    chtb_tlk_id: Mapped[str] = mapped_column(
        "CHTB_TLK_ID", String(50), primary_key=True, comment="챗봇대화아이디",
    )
    chtb_tlk_seq: Mapped[int] = mapped_column(
        "CHTB_TLK_SEQ", Numeric(10, 0), primary_key=True, comment="챗봇대화순번",
    )
    agnt_id: Mapped[Optional[str]] = mapped_column(
        "AGNT_ID", String(50), comment="에이전트아이디",
    )
    msg_role_nm: Mapped[Optional[str]] = mapped_column(
        "MSG_ROLE_NM", String(50), comment="메시지역할명",
    )
    chtb_msg_cntt: Mapped[Optional[str]] = mapped_column(
        "CHTB_MSG_CNTT", Text(16777215), comment="챗봇메시지내용",
    )
    chtb_mdl_nm: Mapped[Optional[str]] = mapped_column(
        "CHTB_MDL_NM", String(100), comment="챗봇모델명",
    )
    chtb_offr_mdl_nm: Mapped[Optional[str]] = mapped_column(
        "CHTB_OFFR_MDL_NM", String(50), comment="챗봇제공모델명",
    )
    chtb_inpt_tokn_ecnt: Mapped[Optional[int]] = mapped_column(
        "CHTB_INPT_TOKN_ECNT", Numeric(10, 0), comment="챗봇입력토큰개수",
    )
    chtb_otpt_tokn_ecnt: Mapped[Optional[int]] = mapped_column(
        "CHTB_OTPT_TOKN_ECNT", Numeric(10, 0), comment="챗봇출력토큰개수",
    )
    chtb_tot_tokn_ecnt: Mapped[Optional[int]] = mapped_column(
        "CHTB_TOT_TOKN_ECNT", Numeric(10, 0), comment="챗봇총토큰개수",
    )
    rply_time: Mapped[Optional[float]] = mapped_column(
        "RPLY_TIME", Numeric(5, 2), comment="응답시간",
    )
    atch_file_no: Mapped[Optional[int]] = mapped_column(
        "ATCH_FILE_NO", BigInteger, comment="첨부파일번호",
    )
    rgsr_id: Mapped[str] = mapped_column(
        "RGSR_ID", String(20), comment="등록자아이디",
    )
    rgst_dtm: Mapped[datetime] = mapped_column(
        "RGST_DTM", DateTime, comment="등록일시",
    )
    uppr_id: Mapped[str] = mapped_column(
        "UPPR_ID", String(20), comment="수정자아이디",
    )
    upd_dtm: Mapped[datetime] = mapped_column(
        "UPD_DTM", DateTime, comment="수정일시",
    )


# 하위 호환을 위한 약어 alias (구 코드/외부 import 용)
ChtbMsgD = ChatMessage
