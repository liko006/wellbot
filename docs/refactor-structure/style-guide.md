# Wellbot Docstring·주석 작성 가이드

> 작성일: 2026-05-29
> 적용 범위: `wellbot/`, `scripts/` 전체 Python 파일
> 테스트 코드(`tests/`)는 본 가이드를 따르되, 테스트 함수의 docstring·이름은 검증 행위를 서술하는 형태(예: `정상 토큰이면 사용자 dict 반환`)를 허용

본 가이드는 기존 코드베이스의 암묵적 관습을 명시화하고, 통일성이 부족했던 항목을 결정한 결과다.
조사 근거: 코드베이스 docstring·주석 전수 샘플링. 구조 진단은 [diagnosis.md](./diagnosis.md) 참조.

---

## 핵심 원칙 4가지

1. **언어**: 한국어 기본. 기술 키워드(API, DB, MIME 등 고유명사)만 영어 표기 유지.
2. **함수 docstring**: 파라미터 또는 리턴이 복잡한 경우 Google-style Args/Returns 섹션 의무화.
3. **함수명으로 역할이 자명하면 docstring 생략 가능**. 단, 의도·제약·반환 의미가 모호하면 작성.
4. **인라인 주석**: WHY(이유·제약·전제) 만 남기고, WHAT(코드 반복) 은 제거.

추가 결정:
- **어미는 명사형으로 작성** (`갱신`, `반환`, `조회`, `저장`). `~한다` / `~합니다` 금지, `~함`도 비권장 (2-1 참조).
- **마침표는 문장 구분이 필요할 때만 사용**. 한 줄 단일 명사구 docstring 및 Args/Returns 의 짧은 한 줄 설명에는 생략.

---

## 1. 언어 규칙

### 1-1. 한국어 기본
모든 docstring·주석은 한국어로 작성한다.

```python
# OK
"""채팅 메시지를 DB 에 저장"""

# 금지 (영어 단일)
"""Persist chat message to DB."""

# 금지 (영어 제목 + 한국어 본문 혼합)
"""Database engine and session management.

엔진은 첫 호출 시점에 생성.
"""
```

### 1-2. 영어가 허용되는 경우
다음만 영어로 그대로 둔다 (한국어 번역하지 않는다):

- 라이브러리·프레임워크 고유명사: SQLAlchemy, Reflex, Bedrock, Pydantic
- 표준 약어: DB, API, JWT, MIME, URL, JSON, UUID, LRU
- 함수·클래스·변수 이름 인용: get_session(), ChatState
- 외부 API 의 키 이름: toolUseId, inferenceConfig, image_blocks

### 1-3. 코드 식별자는 그대로 표기
docstring 본문 안에서 함수·변수·타입을 언급할 때 백틱(`` ` ``) 으로 감싸지 않는다.
한국어 문맥 안에서 백틱은 시각적 노이즈만 늘리고, 코드 식별자는 그 자체로 알아볼 수 있다.

```python
# OK
"""get_config().title 로 접근"""
"""세션 토큰 검증. 유효하면 사용자 정보 dict, 아니면 None"""

# 금지 (백틱으로 둘러쌈)
"""`get_config().title` 로 접근"""
"""세션 토큰 검증. 유효하면 사용자 정보 `dict`, 아니면 `None`"""
```

예외: 문법상 식별자임을 구분해야 가독성이 살아나는 경우만 허용.
- 인라인 코드 블록 안의 토큰 매핑 (Args 섹션 등) 에서 값 후보를 나열할 때
- 한국어 본문과 식별자가 붙어 의미가 모호해지는 경우

### 1-4. 영문 토큰과 조사는 한 칸 띄움
영문·식별자 토큰 뒤에 한국어 조사가 올 때 한 칸 띄운다. 토큰 경계를 시각적으로 분리해 가독성을 높인다.

```python
# OK
"""채팅 메시지를 DB 에 저장"""
"""get_session() 의 첫 호출 시점에 생성"""

# 금지 (조사 붙임)
"""채팅 메시지를 DB에 저장"""
"""get_session()의 첫 호출 시점에 생성"""
```

---

## 2. 어미·마침표 규칙

### 2-1. 명사형으로 끝낸다
```python
# OK
"""환영 메시지 갱신"""
"""대화 목록을 시간 역순으로 정렬해 반환"""

# 금지 (서술 어미)
"""환영 메시지를 갱신한다"""
"""환영 메시지를 갱신합니다"""
"""환영 메시지를 갱신함"""    # `~함` 도 비권장 (명사형이지만 어색함)
```

### 2-2. 마침표는 문장 구분이 필요할 때만
세 가지 케이스로 나뉜다.

**케이스 A. 단일 한 줄 — 마침표 생략**
```python
"""환영 메시지 갱신"""
"""사원의 대화 목록 조회 (최근 30개, 메시지 제외)"""
"""S3 클라이언트 싱글턴"""
```

**케이스 B. 한 줄 안에 두 절 이상 — 절 구분에만 마침표, 끝은 생략**
```python
"""세션 토큰 검증. 유효하면 사용자 정보 dict, 아니면 None"""
"""세션 토큰 발급. JWT + DB 기록"""
```

**케이스 C. 다단락 — 각 문장 끝에 마침표**
완전한 문장 형태로 서술하는 본문 단락에서는 가독성을 위해 문장마다 마침표 유지 (한 단락에 두 문장이면 둘 다 마침표).

```python
"""메시지 DB 저장 (seq 자동 발급).

동일 트랜잭션 내에서 MAX(chtb_tlk_seq) + 1 계산과 INSERT 수행.
UNIQUE(smry_id, seq) 충돌 시 제한된 횟수만큼 재시도.
"""
```

### 2-3. Args / Returns / Raises 항목은 마침표 생략
짧은 한 줄 설명이 반복되는 영역이라 마침표가 시각 노이즈로 가장 두드러진다.

```python
# OK
Args:
    smry_id: 대화 ID
    role: "user" | "assistant" | "system"
    content: 메시지 본문
    msg_id: 메시지 고유 ID. 미지정 시 UUID 자동 생성   # 두 절이면 절 구분에만 마침표

Returns:
    저장된 메시지의 chtb_tlk_id

Raises:
    IntegrityError: 재시도 한도 초과 시 마지막 충돌 전파
```

```python
# 금지 (불필요한 마침표)
Args:
    smry_id: 대화 ID.
    role: "user" | "assistant" | "system".
    content: 메시지 본문.
```

### 2-4. 동작 주체가 모호하면 명시
```python
# 모호 (무엇을 갱신하는지)
"""갱신"""

# 명확
"""환영 메시지 갱신"""
"""현재 대화의 메시지 목록 갱신"""
```

---

## 3. 모듈 docstring

파일 최상단의 docstring. 세 가지 형태 중 책임에 맞춰 선택.

### A. 한 줄형 — 책임이 자명한 단일 목적 모듈
```python
"""채팅 서비스 - 대화 및 메시지 DB CRUD"""
```

### B. 2~4줄 요약형 — 추가 맥락 필요
```python
"""대화 상태 관리 - ChatState.

메시지 전송, 대화 생성/전환/삭제, Bedrock 스트리밍 응답 처리 담당.
DB 연동으로 대화 이력 영속화 보장.
"""
```
다단락은 각 문장 끝에 마침표 (2-2 케이스 C).

### C. 섹션 헤더형 — 복잡한 흐름·API 엔드포인트
섹션 헤더는 4가지만 사용 (필요한 것만):
- `흐름:` — 처리 단계
- `구조:` — 컴포넌트 구성
- `응답:` — HTTP 응답 코드와 의미
- `제약:` — 외부 제한사항

```python
"""파일 업로드 엔드포인트.

흐름:
    1. JWT 쿠키 검증 → emp_no
    2. 대화당 개수/용량 한도 검증
    3. 스트리밍으로 임시파일 저장
    4. attachment_service.register_attachment() 호출
    5. 백그라운드로 파싱·임베딩 실행

응답:
    200: {"file_no", "name", "mime", "status": "processing"}
    400: 검증 실패
    401: 인증 실패
"""
```

---

## 4. 클래스 docstring

### 4-1. 한 줄 요약 (기본)
```python
class ChatState(rx.State):
    """채팅 관련 상태 관리"""

@dataclass(frozen=True)
class TitleConfig:
    """대화 제목 생성용 경량 모델 설정"""

class AttachmentInfo(BaseModel):
    """프론트엔드 표시용 첨부파일 정보"""
```

### 4-2. Attributes 섹션 (필드 의미가 자명하지 않을 때만)
```python
@dataclass
class ParsedDocument:
    """파싱된 문서 결과.

    Attributes:
        text: 전체 텍스트 (페이지 구분 없음)
        page_count: 페이지/시트/슬라이드 수
        mime: 판별된 MIME 타입
        metadata: 파서가 채우는 부가 정보
    """
```

### 4-3. SQLAlchemy ORM 모델
한 줄 요약 + 테이블명 함께 노출.
필드 설명은 클래스 docstring 이 아닌 `mapped_column(comment=...)` 에 작성.

```python
class Employee(Base):
    """사원마스터 (DB 테이블: emp_m)"""

    __tablename__ = "emp_m"

    emp_no: Mapped[str] = mapped_column(
        "EMP_NO", String(15), primary_key=True, comment="사원번호",
    )
```

---

## 5. 함수 docstring

### 5-1. 한 줄형 — 다음 조건을 모두 만족할 때
- 파라미터 ≤ 2개
- 리턴이 단순 (단일 타입, None 의미 자명)
- 부작용이 자명하거나 없음

```python
def _refresh_greeting(self) -> None:
    """환영 메시지를 랜덤으로 갱신"""

def image_format(filename: str) -> str | None:
    """파일명 확장자에서 Bedrock Converse image format 판별. 미지원 시 None"""
```

### 5-2. Google-style 섹션형 — 다음 중 하나라도 해당
- 파라미터 ≥ 3개
- 리턴이 복잡한 dict/tuple/Union (각 키·요소 의미 설명 필요)
- 예외를 명시적으로 raise

섹션 순서: 본문 → `Args:` → `Returns:` → `Raises:` → `Yields:`

```python
def append_message(
    smry_id: str,
    role: str,
    content: str,
    emp_no: str,
    model_name: str = "",
    msg_id: str | None = None,
) -> str:
    """메시지 DB 저장 (seq 자동 발급).

    동일 트랜잭션 내에서 MAX(chtb_tlk_seq) + 1 계산과 INSERT 수행.
    UNIQUE(smry_id, seq) 충돌 시 제한된 횟수만큼 재시도.

    Args:
        smry_id: 대화 ID
        role: "user" | "assistant" | "system"
        content: 메시지 본문
        emp_no: 사용자 사번
        model_name: 사용한 LLM 모델명. 빈 문자열이면 미기록
        msg_id: 메시지 고유 ID. 미지정 시 UUID 자동 생성

    Returns:
        저장된 메시지의 chtb_tlk_id

    Raises:
        IntegrityError: 재시도 한도 초과 시 마지막 충돌 전파
    """
```

### 5-3. Yields (제너레이터)
스트리밍 함수는 `Yields:` 섹션 필수.

```python
def stream_one_turn(...) -> Generator[tuple[str, Any], None, None]:
    """단일 Converse 호출.

    Yields:
        ("thinking", text)              - reasoning delta
        ("text", text)                  - 응답 텍스트 delta
        ("tool_use", {id, name, input}) - 완성된 tool use 요청
        ("stop_reason", reason)         - "end_turn" | "tool_use" | ...
        ("usage", dict)                 - 토큰 사용량
    """
```

### 5-4. 함수명이 자명하면 docstring 생략 가능
**자명한 함수 — 생략 가능**

다음을 모두 만족하면 docstring 없이 함수명만으로 의도가 전달된다고 본다.

- 함수명이 동사구 또는 명사구로 역할을 명확히 표현 (`_file_size_mb`, `_ensure_aware`, `_get_jwt_secret`)
- 파라미터·리턴 타입이 단순하고 타입 힌트만으로 의미 추론 가능
- 부작용·예외·외부 의존성이 없거나 자명함
- 본문이 한두 줄로 짧음

```python
# OK — 함수명 + 타입 힌트로 충분
def _file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


# OK — 동일 패턴
def _normalize_emp_no(value: str) -> str:
    return value.strip()[:15]
```

**작성이 필요한 경우**

함수명만으로는 다음 중 하나가 모호하면 docstring 을 작성한다.

- 동작에 비명시적 제약이 있음 (재시도, lazy 초기화, 캐시 사용 등)
- 리턴이 None 일 수 있는데 그 의미가 코드 흐름에 영향
- 부작용이 있음 (DB write, 외부 API 호출, 환경변수 검증)
- 같은 도메인의 비슷한 이름과 구분이 필요함

```python
# OK — 비명시적 제약(lazy + 환경변수 검증)
def _get_jwt_secret() -> str:
    """JWT 서명 키 조회.

    import 시점이 아닌 첫 호출 시점에 검증.
    환경변수 미설정 시 RuntimeError.
    """
    ...


# OK — 리턴 None 의미
def validate_session_token(token: str) -> dict | None:
    """세션 토큰 검증. 유효하면 사용자 정보 dict, 아니면 None"""
    ...
```

판단이 애매하면 **작성 쪽으로** 기운다. 다만 작성할 때는 코드를 그대로 옮긴 docstring(예: "파일 크기를 MB로 반환한다") 은 함수명 반복일 뿐이므로 그런 docstring 은 차라리 없는 게 낫다.

### 5-5. computed var (`@rx.var`)
주어를 명시. 단순히 "여부" 만 쓰지 않는다.

```python
# OK
@rx.var
def can_send(self) -> bool:
    """현재 입력 상태가 전송 가능한지 여부.

    처리 중인 첨부파일이 있으면 전송 차단.
    """

# 모호
@rx.var
def can_send(self) -> bool:
    """전송 가능 여부"""
```

---

## 6. 인라인 주석 (`#`)

### 6-1. WHY 만 남긴다
이유·제약·전제·외부 호환성을 설명할 때만 작성. 마침표 규칙은 docstring 과 동일 (한 줄이면 생략, 두 절 이상이면 절 구분에만).

```python
# OK (WHY: 외부 호환)
# Bedrock Converse 가 정규화되지 않은 벡터를 반환하므로 명시적 L2 정규화 후 내적 검색
faiss.normalize_L2(normalized)

# OK (WHY: 비명시적 제약 + 보충 설명)
# DB 저장 시점에 system 메시지는 제외. UI 에는 표시되지 않음
if role == "system":
    return
```

### 6-2. WHAT 주석 제거
코드를 그대로 한국어로 옮긴 주석은 삭제.

```python
# 금지
total += len(chunk)  # 누적 합산

# 금지
ext = Path(filename).suffix.lower()  # 확장자를 소문자로 변환
```

### 6-3. 섹션 박스 주석
논리 블록 구분용. 길이 통일은 강제하지 않지만 표기 통일.

```python
# OK
# ── Bedrock Titan 임베딩 호출 ──

# 금지 (혼합 표기)
# === 임베딩 호출 ===
# --- 임베딩 호출 ---
```

### 6-4. 번호 매김 주석
순차 흐름 설명에서만. 단순 나열에는 사용하지 않는다.

```python
# OK
# 1. 사용자 메시지 추가 및 상태 초기화
# 2. Bedrock 스트리밍 호출
# 3. 최종 AI 메시지 저장

# 금지 (단순 나열)
# 1. text 필드
text: str
# 2. timestamp 필드
timestamp: float
```

### 6-5. TODO / FIXME / NOTE 마커
`# TODO: <영문 또는 한국어>` 형태. 이유와 함께 작성.

```python
# OK
# TODO: 파일 파서 모드별 어댑터 분리 검토 (현재 if/elif 분기 과다)

# 금지 (이유 없음)
# TODO: 리팩토링 필요
```

---

## 7. 따옴표·포매팅

### 7-1. Docstring 따옴표
항상 `"""..."""` (triple double-quote) 사용. `'''` 금지.

### 7-2. 코드 식별자 표기
docstring 본문 안의 식별자는 백틱 없이 그대로 표기. (1-3 참조)
가이드 문서·README 같은 마크다운 파일의 백틱 사용은 별개로, 그쪽은 마크다운 렌더링 효과를 위해 사용해도 무방.

### 7-3. 화살표 사용
순서·매핑 표현에 `→` (단방향), `↔` (양방향) 사용. `->` `<->` 는 코드 영역에서만.

```python
"""JWT 쿠키 검증 → emp_no 추출 → DB 조회"""
```

예외: 값과 설명을 정렬해 나열하는 블록(Yields, 응답 코드 등)에서는 설명 구분자로 `-` 를 사용한다 (5-3 참조). 이때는 "단계 전이"가 아니라 "값 ─ 설명" 대응이므로 `→` 를 쓰지 않는다.

```python
"""
Yields:
    ("text", text)      - 응답 텍스트 delta
    ("usage", dict)     - 토큰 사용량
"""
```

---

## 8. 적용 예시 (Before / After)

### 예시 1: 한 줄 함수
```python
# Before
def _refresh_greeting(self) -> None:
    """환영 메시지를 랜덤으로 갱신한다."""

# After
def _refresh_greeting(self) -> None:
    """환영 메시지를 랜덤으로 갱신"""
```

### 예시 2: 영어 혼용 제거
```python
# Before
"""Database engine and session management.

엔진/세션팩토리는 `get_session()` 첫 호출 시점에 생성한다.
"""

# After
"""DB 엔진·세션 관리.

엔진과 세션팩토리는 get_session() 첫 호출 시점에 생성.
import 시점에 환경변수 강제 검증 시 단위 테스트·CLI 가 깨지므로 lazy 초기화.
"""
```

### 예시 3: 코드 반복 제거 + Args 추가
```python
# Before
def _get_client():
    """S3 클라이언트를 생성한다 (싱글턴). boto3 가 환경변수에서 자격증명 자동 로드."""
    region = os.environ.get("S3_REGION", os.environ.get("AWS_REGION", "ap-northeast-2"))
    return boto3.client("s3", region_name=region)

# After
def _get_client():
    """S3 클라이언트 싱글턴.

    S3_REGION 미설정 시 AWS_REGION, 그것도 없으면 ap-northeast-2 폴백.
    """
    region = os.environ.get("S3_REGION", os.environ.get("AWS_REGION", "ap-northeast-2"))
    return boto3.client("s3", region_name=region)
```

### 예시 4: 복잡 리턴에 Returns 섹션 추가
```python
# Before
def list_conversations(emp_no: str) -> list[dict]:
    """사원의 대화 목록 조회 (최근 30개, 메시지 제외)."""

# After
def list_conversations(emp_no: str) -> list[dict]:
    """사원의 대화 목록 조회 (최근 30개, 메시지 제외).

    Returns:
        각 dict 키:
            id: 대화 ID
            title: 대화 제목
            model_name: 사용 모델명
            created_at: 생성 시각 (Unix epoch float)
    """
```

### 예시 5: WHAT 주석 제거
```python
# Before
def _stream_to_tempfile(upload, max_bytes):
    tmp_dir = Path(tempfile.gettempdir()) / "wellbot_upload"
    tmp_dir.mkdir(parents=True, exist_ok=True)  # 디렉터리 생성

    total = 0  # 누적 바이트 수
    with open(tmp_path, "wb") as out:
        while True:
            chunk = upload.file.read(_STREAM_CHUNK)  # 청크 읽기
            if not chunk:
                break  # 끝
            total += len(chunk)  # 누적

# After
def _stream_to_tempfile(upload, max_bytes):
    tmp_dir = Path(tempfile.gettempdir()) / "wellbot_upload"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    with open(tmp_path, "wb") as out:
        while True:
            chunk = upload.file.read(_STREAM_CHUNK)
            if not chunk:
                break
            total += len(chunk)
```

---

## 9. 점검 체크리스트

코드 리뷰·신규 함수 작성 시 다음을 확인.

- [ ] docstring 이 한국어인가 (영어 혼용 없음)
- [ ] 어미가 명사형인가 (`~한다`/`~합니다`/`~함` 없음)
- [ ] 마침표가 시각 노이즈가 되지 않는가 — 단일 한 줄 docstring 과 Args/Returns 짧은 한 줄 항목에 마침표 없음
- [ ] 함수명만으로 의도가 명확한 경우 docstring 을 생략했는가 — 또는 의도·제약·반환 의미가 모호한 경우 docstring 을 작성했는가
- [ ] 파라미터 ≥ 3개 또는 리턴이 복잡한데 Args/Returns 섹션이 빠지지 않았는가
- [ ] WHAT 주석 (코드 반복) 이 남아있지 않은가
- [ ] 섹션 박스 주석 표기가 `# ── ... ──` 인가
- [ ] docstring 본문 안의 코드 식별자에 불필요한 백틱이 붙어있지 않은가
- [ ] 영문·식별자 토큰과 한국어 조사 사이를 한 칸 띄웠는가 (`DB 에`, `get_session() 의`)
