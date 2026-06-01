"""부서마스터 ORM 모델 (DB 테이블: dept_m)"""

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Dept(Base):
    """부서마스터 (DB 테이블: dept_m)"""

    __tablename__ = "dept_m"

    dept_cd: Mapped[str] = mapped_column(
        "DEPT_CD", String(8), primary_key=True, comment="부서코드",
    )
    dept_nm: Mapped[Optional[str]] = mapped_column(
        "DEPT_NM", String(100), comment="부서명",
    )
    dd_clby_tokn_ecnt: Mapped[Optional[int]] = mapped_column(
        "DD_CLBY_TOKN_ECNT", Numeric(10, 0), comment="일별토큰개수",
    )
    mm_clby_tokn_ecnt: Mapped[Optional[int]] = mapped_column(
        "MM_CLBY_TOKN_ECNT", Numeric(10, 0), comment="월별토큰개수",
    )
    prmn_mdl_cntt: Mapped[Optional[dict]] = mapped_column(
        "PRMN_MDL_CNTT", JSON, comment="허용모델내용",
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
DeptM = Dept
