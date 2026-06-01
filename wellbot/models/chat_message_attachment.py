"""챗봇메시지첨부파일상세 ORM 모델 (DB 테이블: chtb_msg_atch_file_d)"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ChatMessageAttachment(Base):
    """챗봇메시지첨부파일상세 (DB 테이블: chtb_msg_atch_file_d)"""

    __tablename__ = "chtb_msg_atch_file_d"

    chtb_tlk_id: Mapped[str] = mapped_column(
        "CHTB_TLK_ID", String(50), primary_key=True, comment="챗봇대화아이디",
    )
    atch_file_no: Mapped[int] = mapped_column(
        "ATCH_FILE_NO", BigInteger, primary_key=True, comment="첨부파일번호",
    )
    rgst_dtm: Mapped[datetime | None] = mapped_column(
        "RGST_DTM", DateTime, comment="등록일시",
    )
    rgsr_id: Mapped[str | None] = mapped_column(
        "RGSR_ID", String(20), comment="등록자아이디",
    )
    upd_dtm: Mapped[datetime | None] = mapped_column(
        "UPD_DTM", DateTime, comment="수정일시",
    )
    uppr_id: Mapped[str | None] = mapped_column(
        "UPPR_ID", String(20), comment="수정자아이디",
    )


# 하위 호환을 위한 약어 alias (구 코드/외부 import 용)
ChtbMsgAtchFileD = ChatMessageAttachment
