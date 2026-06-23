"""Wellbot 설정 상수 - 중앙 집중 관리"""

from zoneinfo import ZoneInfo

# ── 타임존 ──
KST = ZoneInfo("Asia/Seoul")

# ── 인증/세션 ──
TOKEN_EXPIRE_HOURS: int = 6
TOKEN_EXPIRE_SECONDS: int = TOKEN_EXPIRE_HOURS * 3600  # 쿠키 max_age 환산값
REMEMBER_ME_EXPIRE_DAYS: int = 30  # 아이디 기억하기 쿠키 유효기간(일)
REMEMBER_ME_EXPIRE_SECONDS: int = REMEMBER_ME_EXPIRE_DAYS * 86400
LOCK_THRESHOLD: int = 5            # 로그인 실패 잠금 횟수
LOCK_DURATION_MINUTES: int = 30    # 계정 잠금 시간(분)
PASSWORD_MIN_LENGTH: int = 8       # 비밀번호 최소 길이

# ── 채팅 ──
CONVERSATION_LIMIT: int = 30       # 사이드바 대화 목록 최대 수
TITLE_MAX_LENGTH: int = 30         # 임시 제목 최대 글자 수
DEFAULT_CONVERSATION_TITLE: str = "새 대화"
MESSAGE_SEQ_MAX_RETRIES: int = 5   # 메시지 seq 동시 발급 충돌 시 재시도 횟수

# 제목 생성·임베딩 모델 설정은 config/models.yaml 의 title / embedding 섹션으로 이관됨.
# wellbot.services.core.settings.get_config().title / .embedding 으로 접근.

# ── UI ──
SCROLL_THRESHOLD: int = 100        # 자동 스크롤 유지 판정(px)
BTN_THRESHOLD: int = 30            # 스크롤 버튼 표시 판정(px)

# ── 파일 첨부 ──
FILE_MAX_SIZE_MB: int = 50                # 파일 단일 최대 크기
FILE_MAX_PER_MESSAGE: int = 5             # 메시지당 첨부 개수
FILE_MAX_PER_CONVERSATION: int = 20       # 대화당 누적 첨부 개수
FILE_MAX_TOTAL_SIZE_MB: int = 200         # 대화당 누적 최대 용량

# ── KB 누적 문서 수 상한 (논리적 파일 기준; 배치당 5개 제한과 별개) ──
# 사용자가 올린 "파일 수"(pptx/pdf/xlsx 변환·분할본은 1개로 셈) 기준.
# scope 별 상한. 공용(shared)은 관리자 업로드라 상한 미적용.
KB_MAX_DOCS: dict[str, int] = {"personal": 5, "team": 10}

# ── 파서 ──
FILE_PARSER_MODE: str = "upstage"           # "local" | "upstage" | "hybrid"
FILE_PARSER_FALLBACK: bool = True         # local 실패 시 upstage 폴백 (hybrid 모드)

# PDF 는 Upstage Document Parse 로 파싱(이미지/스캔 내용까지 읽기). xlsx 의
# FILE_PARSER_MODE 와 독립된 PDF 전용 노브 — 개인/팀/공용 KB 업로드 전부에 적용.
# 끄면(False) 변환 없이 원본 PDF 가 색인되어 Lambda 의 pdfplumber 커스텀 파싱으로 폴백.
PDF_VIA_UPSTAGE: bool = True

# ── Upstage Document Parse 공식 제약 ──
UPSTAGE_MAX_PAGES: int = 100
UPSTAGE_MAX_SIZE_MB: int = 50

# ── 자동 분할 (PDF 전용) ──
AUTO_SPLIT_PDF_PAGES: int = 100           # 페이지 초과 시 분할
AUTO_SPLIT_PDF_SIZE_MB: int = 50          # 용량 초과 시 분할

# ── 분할 안전 마진 ──
SPLIT_SAFETY_PAGES: int = 90
SPLIT_SAFETY_SIZE_MB: int = 45

# ── 청킹 & 임베딩 ──
AVG_TOKENS_PER_WORD = 1.4                 # 한국어 ~1.5·영어 ~1.3 기준 평균값
CHUNK_SIZE_TOKENS: int = 1000
CHUNK_OVERLAP_TOKENS: int = 200
# 임베딩 모델 ID/차원은 config/models.yaml 의 embedding 섹션으로 이관됨.

# ── 임베딩 병렬 처리 ──
EMBED_MAX_WORKERS: int = 5            # 동시 임베딩 요청 수
EMBED_MAX_RETRIES: int = 3            # 쓰로틀링 시 최대 재시도 횟수
EMBED_RETRY_BASE_DELAY: float = 0.5   # 지수 백오프 기본 대기(초)

# ── 첨부파일 처리 ──
S3_DERIVATIVE_UPLOAD_RETRIES: int = 3   # chunks/index 업로드 원자적 재시도 횟수
DB_UPDATE_RETRIES: int = 3              # token_count 갱신 재시도 횟수
DB_UPDATE_RETRY_BASE_DELAY: float = 0.2 # DB 재시도 지수 백오프 기본 대기(초)

# ── 검색 ──
SEARCH_TOP_K: int = 5

# ── KB (Knowledge Base) ──
KB_SEARCH_TOP_K: int = 10            # KB Retrieve 결과 수 (각 KB 별 + 최종 병합)
KB_MIN_SCORE: float = 0.4            # 이 미만 점수의 청크는 무관 결과로 간주하여 제외
# LLM 답변에 다음 표현이 포함되면 KB 출처를 표시하지 않음
# (저점수 청크가 retrieve 되었지만 LLM 이 활용하지 못해 '정보 없음'으로 답한 케이스)
KB_NOT_FOUND_PATTERNS: tuple[str, ...] = (
    "찾을 수 없", "정보가 없", "포함되어 있지 않", "포함되지 않", "관련 내용이 없",
    "관련 정보가 없", "기재되어 있지 않", "언급되어 있지 않", "언급되지 않", "나와 있지 않", "확인되지 않",
)

TOOL_USE_MAX_ITERATIONS: int = 3          # tool 호출 무한루프 방지 (천장)
TOOL_USE_EMPTY_RESULT_LIMIT: int = 2      # 연속 빈 결과 시 강제 종료
TOOL_USE_DUPLICATE_QUERY_LIMIT: int = 1   # 동일 (query, file_ids/names) 재호출 차단

# ── 이미지 ──
IMAGE_MAX_SIZE_MB: int = 5                # Bedrock Converse 제한
IMAGE_MAX_DIMENSION: int = 8000           # px

# ── FAISS 캐시 ──
FAISS_CACHE_MAX_CONVERSATIONS: int = 10   # 메모리 LRU

# ── 파일 타입 집합 ──
AUTO_SPLITTABLE_EXTS: frozenset[str] = frozenset({".pdf"})
IMAGE_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})
UPSTAGE_SUPPORTED_EXTS: frozenset[str] = frozenset({
    ".pdf", ".docx", ".xlsx", ".pptx", ".hwp", ".hwpx",
    ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp",
})
LOCAL_SUPPORTED_EXTS: frozenset[str] = frozenset({
    ".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md",
})
