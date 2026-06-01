"""인증토큰내역 ORM 모델 (DB 테이블: crtf_tokn_n)"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AuthToken(Base):
    """인증토큰내역 (DB 테이블: crtf_tokn_n)"""

    __tablename__ = "crtf_tokn_n"

    crtf_tokn_id: Mapped[str] = mapped_column(
        "CRTF_TOKN_ID", String(50), primary_key=True, comment="인증토큰아이디",
    )
    emp_no: Mapped[str] = mapped_column(
        "EMP_NO", String(15), primary_key=True, comment="사원번호",
    )
    crtf_ecr_tokn_val: Mapped[str] = mapped_column(
        "CRTF_ECR_TOKN_VAL", String(300), comment="인증암호화토큰값",
    )
    diss_yn: Mapped[Optional[str]] = mapped_column(
        "DISS_YN", String(1), comment="폐기여부",
    )
    trtn_dtm: Mapped[Optional[datetime]] = mapped_column(
        "TRTN_DTM", DateTime, comment="만료일시",
    )
    diss_dtm: Mapped[Optional[datetime]] = mapped_column(
        "DISS_DTM", DateTime, comment="폐기일시",
    )
    rgst_dtm: Mapped[datetime] = mapped_column(
        "RGST_DTM", DateTime, comment="등록일시",
    )
    rgsr_id: Mapped[str] = mapped_column(
        "RGSR_ID", String(20), comment="등록자아이디",
    )
    upd_dtm: Mapped[datetime] = mapped_column(
        "UPD_DTM", DateTime, comment="수정일시",
    )
    uppr_id: Mapped[str] = mapped_column(
        "UPPR_ID", String(20), comment="수정자아이디",
    )


# 하위 호환을 위한 약어 alias (구 코드/외부 import 용)
CrtfToknN = AuthToken
