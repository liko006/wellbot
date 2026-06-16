"""KB(Knowledge Base) 도메인 패키지.

개인/팀/공용 KB 의 생성·조회·의미검색·파일 관리를 담당

공개 진입점을 아래로 재노출. 단, personal/team 양쪽에 동명으로 존재하는
start_ingestion / upload_and_ingest 는 모호성 때문에 재노출하지 않으므로
해당 서브모듈에서 직접 import.
    예) from wellbot.services.knowledgebase.personal_kb_manager import upload_and_ingest
"""

from wellbot.services.knowledgebase.config import get_kb_config
from wellbot.services.knowledgebase.kb_retriever import retrieve
from wellbot.services.knowledgebase.personal_kb_manager import (
    create_personal_kb,
    get_or_create_personal_kb,
    get_user_kb,
)
from wellbot.services.knowledgebase.team_kb_manager import (
    create_team_kb,
    ensure_team_kb_membership,
    get_dept_cd,
    get_or_create_team_kb,
    get_user_team_kb,
)

__all__ = [
    "get_kb_config",
    "retrieve",
    "create_personal_kb",
    "get_or_create_personal_kb",
    "get_user_kb",
    "create_team_kb",
    "ensure_team_kb_membership",
    "get_dept_cd",
    "get_or_create_team_kb",
    "get_user_team_kb",
]
