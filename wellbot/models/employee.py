"""사원마스터 ORM 모델 (DB 테이블: emp_m)"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Employee(Base):
    """사원마스터 (DB 테이블: emp_m)"""

    __tablename__ = "emp_m"

    emp_no: Mapped[str] = mapped_column(
        "EMP_NO", String(15), primary_key=True, comment="사원번호",
    )
    ecr_pwd: Mapped[Optional[str]] = mapped_column(
        "ECR_PWD", String(80), comment="암호화비밀번호",
    )
    user_nm: Mapped[Optional[str]] = mapped_column(
        "USER_NM", String(50), comment="사용자명",
    )
    lgn_scs_dtm: Mapped[Optional[datetime]] = mapped_column(
        "LGN_SCS_DTM", DateTime, comment="로그인성공일시",
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
    user_role_nm: Mapped[str] = mapped_column(
        "USER_ROLE_NM", String(50), comment="사용자역할명",
    )
    pstn_dept_cd: Mapped[str] = mapped_column(
        "PSTN_DEPT_CD", String(8), comment="소속부서코드",
    )
    lgn_flr_tscnt: Mapped[Optional[int]] = mapped_column(
        "LGN_FLR_TSCNT", Numeric(5, 0), comment="로그인실패횟수",
    )
    lock_dsbn_dtm: Mapped[Optional[datetime]] = mapped_column(
        "LOCK_DSBN_DTM", DateTime, comment="잠금해제일시",
    )
    user_uuid: Mapped[Optional[str]] = mapped_column(
        "USER_UUID", String(36), comment="사용자UUID",
    )
    acnt_sts_nm: Mapped[str] = mapped_column(
        "ACNT_STS_NM", String(50), comment="계정상태명",
    )


# 하위 호환을 위한 약어 alias (구 코드/외부 import 용)
EmpM = Employee
