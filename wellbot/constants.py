"""Wellbot 설정 상수 - 중앙 집중 관리"""

import os
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

# ── 턴 네비게이터(채팅 우측 레일) ──
# 레일이 나타나는 최소 질문 수. 이보다 적으면 스크롤만으로 충분해 레일을 숨긴다.
# (실측: 4턴 초과 대화 41%, 10턴 초과 13% — 노출 폭이 넓으면 체감 후 상향 가능)
TURN_RAIL_MIN_TURNS: int = 5
# 레일에 그릴 틱의 최대 개수. 메시지 페이지네이션(50개 ≈ 질문 25개)과 맞춘다.
# 초과분은 레일 위쪽 '···' 로 접히고, 호버 팝업에서는 전체가 보인다.
TURN_RAIL_MAX_TICKS: int = 25
# 팝업에 실어 보낼 질문 텍스트 길이 상한. 시각적 말줄임은 CSS(ellipsis)가 하고,
# 이 값은 상태 전송량이 질문 길이에 비례해 커지는 것만 막는다.
TURN_PREVIEW_MAX_CHARS: int = 100
# 입력창 하단 고지. 채팅·보고서 draft 등 LLM 입력창이 있는 화면이 공유하므로
# 문구가 갈라지지 않도록 한 곳에서 관리한다(스타일은 각 화면에서 지정).
AI_DISCLAIMER_TEXT: str = (
    "WellBot은 실수할 수 있습니다. WellBot의 출력 결과를 확인하고 활용하세요."
)

# ── 파일 첨부 ──
FILE_MAX_SIZE_MB: int = 50                # 파일 단일 최대 크기
FILE_MAX_PER_MESSAGE: int = 5             # 메시지당 첨부 개수
FILE_MAX_PER_CONVERSATION: int = 20       # 대화당 누적 첨부 개수
FILE_MAX_TOTAL_SIZE_MB: int = 200         # 대화당 누적 최대 용량

# ── KB 누적 문서 수 상한 (논리적 파일 기준; 배치당 5개 제한과 별개) ──
# 사용자가 올린 "파일 수"(pptx/pdf/xlsx 변환·분할본은 1개로 셈) 기준.
# scope 별 상한. 공용(shared)은 관리자 업로드라 상한 미적용.
KB_MAX_DOCS: dict[str, int] = {"personal": 5, "team": 10}

# 한 업로드 요청에 실을 수 있는 파일 수. 요청 본문 크기(프록시 상한)와 앱 메모리를
# 개수로 묶어두는 상한이며, 서버(엔드포인트)와 클라이언트(배치 전송 JS)가 같이 본다.
# 관리자가 이보다 많이 고르면 이 단위로 끊어 순차 전송한다.
KB_UPLOAD_MAX_PER_REQUEST: int = 5

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

# ── Upstage 호출 재시도 ──
# 502/503/504/429·연결/타임아웃 오류는 일시적일 수 있어 지수 백오프로 재시도한다.
# 413(페이지/용량 초과) 등 4xx 는 영구 오류라 재시도하지 않는다.
UPSTAGE_TIMEOUT_SEC: float = 300.0        # 요청당 타임아웃
UPSTAGE_MAX_RETRIES: int = 2              # 일시적 오류 최대 재시도 횟수(총 3회 시도)
UPSTAGE_RETRY_BASE_DELAY: float = 1.0     # 지수 백오프 기본 대기(초)

# ── 자동 분할 (PDF 전용) ──
AUTO_SPLIT_PDF_PAGES: int = 100           # 페이지 초과 시 분할
AUTO_SPLIT_PDF_SIZE_MB: int = 50          # 용량 초과 시 분할

# ── 분할 안전 마진 ──
SPLIT_SAFETY_PAGES: int = 90
SPLIT_SAFETY_SIZE_MB: int = 45

# ── 청킹 & 임베딩 ──
AVG_TOKENS_PER_WORD = 1.4                 # (레거시) 공백 어절 기반 평균값
CHUNK_SIZE_TOKENS: int = 1000
CHUNK_OVERLAP_TOKENS: int = 200
# 임베딩 모델 ID/차원은 config/models.yaml 의 embedding 섹션으로 이관됨.

# CJK 인식 토큰 추정.
# 공백 어절 기반 추정은 한국어/중국어/일본어를 5~15배 과소추정한다
# (띄어쓰기 없는 CJK 는 문단 전체가 1 어절 → Titan 8192 토큰 한도 초과로 임베딩 실패).
# 문자 단위로 상한적(=실제보다 크게) 추정해 오버플로를 원천 차단.
CJK_TOKENS_PER_CHAR: float = 1.0          # 한글/한자/가나: 문자당 추정 토큰(상한적)
LATIN_CHARS_PER_TOKEN: float = 4.0        # 비-CJK: 문자 4개 ≈ 1토큰
EMBED_TOKEN_HARD_MAX: int = 7000          # 임베딩 청크 하드 상한(8192 안전마진). 초과 시 강제 재분할.

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
KB_SEARCH_TOP_K: int = 5             # KB Retrieve 결과 수 (각 KB 별 + 최종 병합)
KB_MIN_SCORE: float = 0.4            # 이 미만 점수의 청크는 무관 결과로 간주하여 제외
# 공용 KB 권위 재랭킹: raw score 가 이 값 이상일 때만 폴더 authority 배수를 적용
# (min_score 0.4 보다 높아야 게이트가 의미 있음). 모델 score 분포 의존값이라 배포 무관 → 상수 단일 소스.
KB_AUTHORITY_FLOOR: float = 0.55
# LLM 답변에 다음 표현이 포함되면 KB 출처를 표시하지 않음
# (저점수 청크가 retrieve 되었지만 LLM 이 활용하지 못해 '정보 없음'으로 답한 케이스)
KB_NOT_FOUND_PATTERNS: tuple[str, ...] = (
    "찾을 수 없", "정보가 없", "포함되어 있지 않", "포함되지 않", "관련 내용이 없",
    "관련 정보가 없", "기재되어 있지 않", "언급되어 있지 않", "언급되지 않", "나와 있지 않", "확인되지 않",
)

# tool 결과(주로 kb_search) 텍스트를 이 토큰 예산으로 절단.
# tool-use 루프는 누적된 tool_result 를 매 반복마다 재전송하므로, 절단 없이는
# 대용량 검색 결과가 입력 토큰을 폭증시킨다(운영에서 200K~410K 관측).
# 결과는 점수 내림차순이라 상위(고관련) 청크가 보존된다.
TOOL_RESULT_MAX_TOKENS: int = int(os.environ.get("TOOL_RESULT_MAX_TOKENS", "6000"))

# read_attachment(전체 문서 읽기) 폴백 상한. 모델 컨텍스트를 알 수 없을 때만 쓰이는
# 고정 기본값. 실제로는 read_budget_for() 가 모델 context_window 기반으로 동적 산정한다.
READ_ATTACHMENT_MAX_TOKENS: int = int(os.environ.get("READ_ATTACHMENT_MAX_TOKENS", "60000"))

# read_attachment 예산을 모델 윈도우에서 산정할 때 남겨둘 여유분(시스템 프롬프트 +
# toolConfig 스펙 + 안전 마진). 예산 = context_window − 히스토리(LLM_CONTEXT_MAX_TOKENS)
# − 출력(max_tokens) − 이 값. 모델 윈도우에 맞춰 한 번에 싣기 때문에 offset 이어읽기
# 누적으로 인한 컨텍스트 초과가 원천 차단된다.
READ_ATTACHMENT_CONTEXT_RESERVE: int = int(
    os.environ.get("READ_ATTACHMENT_CONTEXT_RESERVE", "8000")
)
# 동적 예산이 과도하게 작아지지 않도록 하는 하한(초소형 윈도우 모델 방어).
READ_ATTACHMENT_MIN_TOKENS: int = int(
    os.environ.get("READ_ATTACHMENT_MIN_TOKENS", "8000")
)

TOOL_USE_MAX_ITERATIONS: int = 3          # tool 호출 무한루프 방지 (천장)
TOOL_USE_EMPTY_RESULT_LIMIT: int = 2      # 연속 빈 결과 시 강제 종료
TOOL_USE_DUPLICATE_QUERY_LIMIT: int = 1   # 동일 (query, file_ids/names) 재호출 차단

# ── 이미지 ──
IMAGE_MAX_SIZE_MB: int = 5                # Bedrock Converse 제한
IMAGE_MAX_DIMENSION: int = 8000           # px

# ── FAISS 캐시 ──
FAISS_CACHE_MAX_CONVERSATIONS: int = 10   # 메모리 LRU

# ── 동시성 / 성능 튜닝 ──
# 단일 이벤트 루프에서 블로킹 I/O(DB·Bedrock 토큰 스트리밍·S3)를 처리할
# 기본 스레드풀 크기. asyncio 기본값 min(32, cpu+4)는 vCPU 4 기준 8개로
# 다중 동시 사용자에 과소하다. I/O 바운드(대기 중 GIL 해제)라 코어 수보다
# 크게 잡아도 안전. 운영 튜닝을 위해 환경변수로 주입.
IO_EXECUTOR_MAX_WORKERS: int = int(os.environ.get("IO_EXECUTOR_MAX_WORKERS", "32"))

# CPU 바운드 파일 파싱(pdfplumber/pandas/python-pptx)을 메인 프로세스 GIL
# 밖으로 오프로드할 프로세스풀 크기. 0 이하면 비활성(스레드 내 파싱으로 폴백).
CPU_POOL_MAX_WORKERS: int = int(os.environ.get("CPU_POOL_MAX_WORKERS", "2"))

# ── 스트리밍 ──
# Bedrock 동기 스트림을 소비할 producer 스레드 전용 풀 크기 = 동시 스트림 상한.
# 토큰당 to_thread 대신 스트림(턴)당 스레드 1개만 점유하므로 이 값이 동시 실시간
# 응답의 상한이 된다. 초과 스트림은 슬롯이 빌 때까지 대기(backpressure).
STREAM_MAX_CONCURRENT: int = int(os.environ.get("STREAM_MAX_CONCURRENT", "24"))

# 스트리밍 상태 갱신(state 락 + WebSocket push) 배치 주기(초). 이 간격마다
# 최대 1회만 streaming_content 를 갱신해 토큰당 락/네트워크 폭주를 줄인다.
# 첫 토큰·thinking/tool 경계는 즉시 반영되므로 TTFT 체감에는 영향이 없다.
STREAM_FLUSH_INTERVAL_SEC: float = float(
    os.environ.get("STREAM_FLUSH_INTERVAL_SEC", "0.08")
)

# ── LLM 컨텍스트 / 히스토리 ──
# 매 턴 Bedrock 에 보낼 대화 히스토리의 최대 토큰(추정). 긴 대화에서 입력 토큰이
# 무한정 커지는 것을 막아 비용·지연·컨텍스트 한도 초과를 방지(최근 우선 슬라이딩 윈도우).
# 문서 회상은 kb_search·search_attachment 툴이 담당하므로 요약 없이 윈도우로 충분.
LLM_CONTEXT_MAX_TOKENS: int = int(os.environ.get("LLM_CONTEXT_MAX_TOKENS", "12000"))

# 대화를 열 때 처음 로드/표시할 최근 메시지 수. 이전 메시지는 "이전 대화 더 보기"로
# 커서 기반 추가 로드 → 무한정 state 적재·WS 전송 방지.
MESSAGE_PAGE_SIZE: int = int(os.environ.get("MESSAGE_PAGE_SIZE", "50"))

# ── 첨부 처리 대기 ──
# 첨부 처리(파싱·청킹·임베딩) 대기 상한(초). **업로드가 DB 에 안착한 뒤부터** 센다
# (파일 선택·전송 시간은 포함하지 않는다 — 그 둘을 포함하면 사용자가 파일 탐색기에서
# 뜸들인 시간만큼 처리 예산이 깎인다). 상한을 넘기면 화면에서만 '실패'로 표시해 입력
# 잠금을 푼다. DB 는 건드리지 않으므로 백그라운드가 나중에 끝내면 다음 조회에서 정상으로
# 돌아온다. 대형 PDF 의 Upstage 파싱까지 감안한 값.
ATTACHMENT_PROCESS_TIMEOUT_SEC: int = 300
# 업로드 완료 콜백을 기다리는 백스톱(초). 정상적으로는 JS 콜백이 즉시 단계를 넘기지만,
# 탭 종료 등으로 콜백이 영영 오지 않을 때 폴링이 무한히 도는 것을 막는다.
ATTACHMENT_UPLOAD_WAIT_MAX_SEC: int = 600

# ── AWS(boto3) 커넥션 풀 / 타임아웃 ──
# botocore 기본 풀은 10이라 STREAM_MAX_CONCURRENT(24)보다 작다 — 그대로 두면 동시
# 스트리밍 11개째부터 커넥션을 기다린다. 풀 크기는 "동시에 열 수 있는 소켓 상한"일
# 뿐 미리 만들어두지 않으므로, 여유를 둬도 비용이 거의 없다.
AWS_MAX_POOL_CONNECTIONS: int = int(
    os.environ.get("AWS_MAX_POOL_CONNECTIONS", str(STREAM_MAX_CONCURRENT + 8))
)
# 연결 수립 타임아웃 — 네트워크 이상 시 빠르게 실패시켜 스레드 점유를 줄인다.
AWS_CONNECT_TIMEOUT_SEC: int = int(os.environ.get("AWS_CONNECT_TIMEOUT_SEC", "5"))
# 비스트리밍 호출(임베딩·제목 생성·report 계열 단발 호출)의 응답 대기 상한.
AWS_READ_TIMEOUT_SEC: int = int(os.environ.get("AWS_READ_TIMEOUT_SEC", "120"))
# 스트리밍(ConverseStream)의 **이벤트 간 무전송 허용 시간**. thinking 구간처럼 토큰이
# 한동안 안 오는 구간이 있어 기본 60초는 짧다. nginx proxy_read_timeout(600s)보다는
# 작게 둬서 프록시가 끊기 전에 앱이 먼저 인지하도록 한다.
AWS_STREAM_READ_TIMEOUT_SEC: int = int(
    os.environ.get("AWS_STREAM_READ_TIMEOUT_SEC", "300")
)
# 재시도 횟수(총 시도 횟수) — throttling·5xx 같은 안전한 오류만 botocore 가 재시도한다.
# botocore 기본(legacy 모드)이 5회이므로 5로 맞춘다. standard 모드 기본값 3을 쓰면
# **스로틀링에서 기존보다 일찍 포기**하게 되어 동시 사용자가 늘 때 실패가 늘 수 있다.
AWS_MAX_ATTEMPTS: int = int(os.environ.get("AWS_MAX_ATTEMPTS", "5"))

# ── DB 커넥션 풀 ──
# SQLAlchemy QueuePool 기본값(pool_size=5 + max_overflow=10 = 15)은
# 다중 동시 사용자·스레드 오프로드 환경에 부족. 명시적으로 확대.
DB_POOL_SIZE: int = int(os.environ.get("DB_POOL_SIZE", "20"))
DB_MAX_OVERFLOW: int = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
DB_POOL_RECYCLE_SEC: int = int(os.environ.get("DB_POOL_RECYCLE_SEC", "3600"))
DB_POOL_TIMEOUT_SEC: int = int(os.environ.get("DB_POOL_TIMEOUT_SEC", "10"))

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
