"""에이전트메모리사용내역 ORM 모델 (DB 테이블: agnt_mmry_use_n)"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AgentMemory(Base):
    """에이전트메모리사용내역 (DB 테이블: agnt_mmry_use_n)"""

    __tablename__ = "agnt_mmry_use_n"

    agnt_id: Mapped[str] = mapped_column(
        "AGNT_ID", String(50), primary_key=True, comment="에이전트아이디",
    )
    agnt_seq: Mapped[int] = mapped_column(
        "AGNT_SEQ", Numeric(10, 0), primary_key=True, comment="에이전트순번",
    )
    emp_no: Mapped[str] = mapped_column(
        "EMP_NO", String(15), primary_key=True, comment="사원번호",
    )
    agnt_mmry_path_addr: Mapped[Optional[str]] = mapped_column(
        "AGNT_MMRY_PATH_ADDR", String(300), comment="에이전트메모리경로주소",
    )
    agnt_type_dscr_cntt: Mapped[Optional[str]] = mapped_column(
        "AGNT_TYPE_DSCR_CNTT", Text(16777215), comment="에이전트유형설명내용",
    )
    use_yn: Mapped[Optional[str]] = mapped_column(
        "USE_YN", String(1), comment="사용여부",
    )
    last_sync_dtm: Mapped[Optional[datetime]] = mapped_column(
        "LAST_SYNC_DTM", DateTime, comment="최종동기화일시",
    )
    rgst_dtm: Mapped[Optional[datetime]] = mapped_column(
        "RGST_DTM", DateTime, comment="등록일시",
    )
    rgsr_id: Mapped[Optional[str]] = mapped_column(
        "RGSR_ID", String(20), comment="등록자아이디",
    )
    upd_dtm: Mapped[Optional[datetime]] = mapped_column(
        "UPD_DTM", DateTime, comment="수정일시",
    )
    uppr_id: Mapped[Optional[str]] = mapped_column(
        "UPPR_ID", String(20), comment="수정자아이디",
    )


# 하위 호환을 위한 약어 alias (구 코드/외부 import 용)
AgntMmryUseN = AgentMemory
