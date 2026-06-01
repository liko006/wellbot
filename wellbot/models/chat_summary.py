"""챗봇요약상세 ORM 모델 (DB 테이블: chtb_smry_d)"""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ChatSummary(Base):
    """챗봇요약상세 (DB 테이블: chtb_smry_d)"""

    __tablename__ = "chtb_smry_d"

    chtb_tlk_smry_id: Mapped[str] = mapped_column(
        "CHTB_TLK_SMRY_ID", String(50), primary_key=True, comment="챗봇대화요약아이디",
    )
    emp_no: Mapped[str] = mapped_column(
        "EMP_NO", String(15), comment="사원번호",
    )
    chtb_tlk_smry_ttl: Mapped[str | None] = mapped_column(
        "CHTB_TLK_SMRY_TTL", String(500), comment="챗봇대화요약제목",
    )
    chtb_mdl_nm: Mapped[str | None] = mapped_column(
        "CHTB_MDL_NM", String(100), comment="챗봇모델명",
    )
    bkmr_yn: Mapped[str | None] = mapped_column(
        "BKMR_YN", String(1), comment="즐겨찾기여부",
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
ChtbSmryD = ChatSummary
