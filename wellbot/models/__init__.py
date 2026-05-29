"""Wellbot database models.

각 모델은 도메인 이름(`Employee`, `ChatMessage` 등) 으로 정의되어 있으며,
기존 SI 약어(`EmpM`, `ChtbMsgD` 등) 는 같은 클래스에 대한 alias 로 유지된다.
새 코드는 도메인 이름 사용을 권장한다.
"""

from .agent import Agent, AgntM
from .agent_memory import AgentMemory, AgntMmryUseN
from .attachment import Attachment, AtchFileM
from .auth_token import AuthToken, CrtfToknN
from .base import Base
from .chat_message import ChatMessage, ChtbMsgD
from .chat_message_attachment import ChatMessageAttachment, ChtbMsgAtchFileD
from .chat_summary import ChatSummary, ChtbSmryD
from .dept import Dept, DeptM
from .employee import Employee, EmpM

__all__ = [
    "Base",
    # 도메인 이름 (권장)
    "Agent",
    "AgentMemory",
    "Attachment",
    "AuthToken",
    "ChatMessage",
    "ChatMessageAttachment",
    "ChatSummary",
    "Dept",
    "Employee",
    # SI 약어 alias (하위 호환)
    "AgntM",
    "AgntMmryUseN",
    "AtchFileM",
    "CrtfToknN",
    "ChtbMsgD",
    "ChtbMsgAtchFileD",
    "ChtbSmryD",
    "DeptM",
    "EmpM",
]
