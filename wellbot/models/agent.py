"""에이전트마스터 ORM 모델 (DB 테이블: agnt_m)"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Agent(Base):
    """에이전트마스터 (DB 테이블: agnt_m)"""

    __tablename__ = "agnt_m"

    agnt_id: Mapped[str] = mapped_column(
        "AGNT_ID", String(50), primary_key=True, comment="에이전트아이디",
    )
    agnt_seq: Mapped[int] = mapped_column(
        "AGNT_SEQ", Numeric(10, 0), primary_key=True, comment="에이전트순번",
    )
    agnt_nm: Mapped[Optional[str]] = mapped_column(
        "AGNT_NM", String(100), comment="에이전트명",
    )
    agnt_frwk_nm: Mapped[Optional[str]] = mapped_column(
        "AGNT_FRWK_NM", String(100), comment="에이전트프레임워크명",
    )
    agnt_path_addr: Mapped[Optional[str]] = mapped_column(
        "AGNT_PATH_ADDR", String(300), comment="에이전트경로주소",
    )
    agnt_dscr_cntt: Mapped[Optional[str]] = mapped_column(
        "AGNT_DSCR_CNTT", Text(16777215), comment="에이전트설명내용",
    )
    use_yn: Mapped[Optional[str]] = mapped_column(
        "USE_YN", String(1), comment="사용여부",
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
AgntM = Agent
