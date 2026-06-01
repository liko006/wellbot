"""첨부파일마스터 ORM 모델 (DB 테이블: atch_file_m)"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Attachment(Base):
    """첨부파일마스터 (DB 테이블: atch_file_m)"""

    __tablename__ = "atch_file_m"

    atch_file_no: Mapped[int] = mapped_column(
        "ATCH_FILE_NO", BigInteger, primary_key=True, autoincrement=True, comment="첨부파일번호",
    )
    atch_file_nm: Mapped[str | None] = mapped_column(
        "ATCH_FILE_NM", String(300), comment="첨부파일명",
    )
    atch_file_url_addr: Mapped[str | None] = mapped_column(
        "ATCH_FILE_URL_ADDR", String(500), comment="첨부파일URL주소",
    )
    atch_file_tokn_ecnt: Mapped[int | None] = mapped_column(
        "ATCH_FILE_TOKN_ECNT", Numeric(10, 0), comment="첨부파일토큰개수",
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
AtchFileM = Attachment
