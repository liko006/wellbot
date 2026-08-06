# Wellbot 코딩 컨벤션

## 1. 목적과 적용 범위

이 문서는 Wellbot의 신규 개발과 유지보수에 적용할 코드 작성 기준이다. Python 3.11,
Reflex, FastAPI, SQLAlchemy와 AWS 연동 코드가 주요 대상이며 테스트, 설정 파일과
브라우저 스크립트에도 관련 규칙을 적용한다.

규칙의 강도는 다음과 같다.

- **필수(MUST)**: 보안, 정확성, 장애 예방 또는 일관성을 위해 반드시 지킨다.
- **권장(SHOULD)**: 특별한 이유가 없으면 지킨다. 예외는 PR에 이유를 남긴다.
- **검토 기준**: 위반 자체가 오류는 아니지만 모듈 분리나 설계 검토를 시작하는 기준이다.

기존 코드 전체를 한 번에 포맷하거나 이름을 변경하지 않는다. 신규 파일은 모든 규칙을
적용하고, 기존 파일은 수정한 범위부터 적용하는 **점진적 적용 원칙**을 따른다. 대규모
기계적 포맷 변경은 기능 변경 PR과 분리한다.

## 2. 기준과 Wellbot 예외

기본 Python 스타일은 PEP 8, docstring은 PEP 257, 타입 표기는 PEP 484 계열 관례를
따른다. 자동 포맷과 import 정리는 Ruff를 기준 도구로 채택하는 것을 목표로 한다.

다음은 Wellbot의 의도적인 예외다.

- DB의 기존 테이블·컬럼명은 사내 물리 모델을 유지한다. ORM의 Python 속성은
  `snake_case`, 실제 DB 이름은 `mapped_column("UPPER_CASE_NAME", ...)`로 명시한다.
- Reflex State의 공개 class attribute는 프레임워크 상태 필드이므로 일반 클래스의
  mutable class attribute 금지 규칙에서 제외한다.
- 사용자·운영자 대상 메시지와 도메인 설명은 한국어를 사용할 수 있다. 식별자와 로그
  event 이름은 영어를 사용한다.
- 하위 호환 alias는 제거 일정 또는 사용 이유를 주석으로 남긴 경우 허용한다.

## 3. 현재 코드 기준선과 차이

2026-08-06 정적 조사 결과다. 수치는 품질 점수가 아니라 자동화 도입과 리팩터링 범위를
가늠하기 위한 기준선이다.

| 항목 | 현재 상태 | 일반적인 관례와의 차이 | 적용 방향 |
|---|---:|---|---|
| Python 파일 | 152개 | 해당 없음 | 동일 기준 적용 |
| 함수·메서드 | 약 1,037개 | 해당 없음 | 신규·수정 함수부터 적용 |
| 반환 타입 표기 | 약 772개 | 공개 함수는 대부분 표기하는 것이 일반적 | 점진적으로 100%에 접근 |
| `except Exception` | 131곳 | 예외 범위가 넓고 장애 분류가 어려움 | 경계 계층 외에는 구체 예외 사용 |
| 100자 초과 줄 | 305줄 | formatter 기준 부재 | 최대 100자로 통일 |
| `print()` | 0곳 | 좋은 상태 | logger 사용 유지 |
| bare `except:` | 0곳 | 좋은 상태 | 금지 유지 |
| wildcard import | 0곳 | 좋은 상태 | 금지 유지 |
| 모듈 logger | 51곳 | 서비스 코드에서 잘 적용됨 | 변경 파일에 일관되게 적용 |
| formatter/linter | 없음 | 스타일을 사람의 기억에 의존 | Ruff 단계적 도입 |
| type checker | 없음 | 타입 표기의 실제 검증이 없음 | mypy 점진적 도입 |
| pre-commit/CI style gate | 없음 | PR마다 편차가 누적될 수 있음 | 도구 안정화 후 강제 |

현재 잘 지켜지는 부분은 다음과 같다.

- 모듈 docstring과 함수 타입 표기가 상당수 존재한다.
- `logging.getLogger(__name__)`와 중앙 로깅 설정을 사용한다.
- `print`, bare except, wildcard import를 사용하지 않는다.
- DB 세션을 context manager로 감싸 commit/rollback/close를 일관되게 처리한다.
- Python 3.11의 `X | None`, 내장 generic 타입을 사용한다.
- 파일은 UTF-8이며 한글 docstring과 주석도 정상적으로 저장되어 있다.

주요 부족 사항은 다음과 같다.

- Ruff, formatter, type checker가 없어 형식과 타입 규칙이 자동 검증되지 않는다.
- 서비스 반환값에 `dict`, State 필드에 `list[dict]`가 많아 키 구조를 타입으로 확인하기
  어렵다.
- `ChatState`, `ReportMakerState`처럼 상태·오케스트레이션·도메인 로직이 한 파일에
  집중된 모듈이 있다.
- broad exception이 많아 인증 실패, 입력 오류, 외부 서비스 장애와 내부 버그가 같은
  방식으로 처리될 수 있다.
- 일부 State event의 매개변수와 반환 타입이 생략되어 있다.
- import가 절대 경로와 상대 경로로 혼용되며 줄 길이 기준도 명시되어 있지 않았다.

## 4. 파일과 인코딩

### 필수

- 모든 텍스트 파일은 UTF-8로 저장한다. BOM은 추가하지 않는다.
- 줄 끝은 LF를 기준으로 한다. Windows 개발 환경에서도 Git이 저장소에는 LF로
  정규화하도록 한다.
- 파일 마지막에는 빈 줄 하나를 둔다.
- Python 파일명과 패키지명은 소문자 `snake_case`를 사용한다.
- 한 파일에는 하나의 주된 책임만 둔다.

### 파일 배치

| 코드 종류 | 위치 |
|---|---|
| Reflex 화면 구성 | `wellbot/pages`, `wellbot/components` |
| 사용자 화면 상태와 이벤트 | `wellbot/state` |
| 비즈니스 use case와 외부 연동 | `wellbot/services/<domain>` |
| FastAPI HTTP 경계 | `wellbot/api` |
| SQLAlchemy ORM | `wellbot/models` |
| 공통 설정·실행기·DB 기반 기능 | `wellbot/services/core` |
| 재사용 가능한 브라우저 script builder | 기능별 `*_scripts.py` 또는 전용 모듈 |
| 테스트 | 소스 구조를 반영한 `tests/` 하위 경로 |

Page와 Component에서 DB, S3, Bedrock을 직접 호출하지 않는다. State도 복잡한 도메인
로직을 직접 구현하지 않고 service/use case를 호출한다.

## 5. 포맷

### 필수

- 들여쓰기는 공백 4칸을 사용한다. Tab을 사용하지 않는다.
- 한 줄 최대 길이는 **100자**로 한다.
- 한 줄에 문장 하나만 작성한다.
- 여러 줄 함수 호출, collection과 import에는 trailing comma를 사용한다.
- 연산자 주변, 콤마 뒤와 타입 표기는 PEP 8 공백 규칙을 따른다.
- 수동 정렬을 위해 의미 없는 여러 공백을 넣지 않는다.

PEP 8 기본 79자보다 100자를 선택한 이유는 긴 도메인 식별자와 타입 표기가 많은 현재
코드에서 가독성과 변경량의 균형을 맞추기 위해서다. 단, 주석과 docstring은 가능한
88자 안쪽에서 자연스럽게 줄을 나눈다.

문자열 quote는 의미상 차이가 없다면 formatter 결과를 따른다. quote 형태만 바꾸는
리뷰 의견은 남기지 않는다.

## 6. 이름

| 대상 | 규칙 | 예시 |
|---|---|---|
| 모듈·변수·함수·메서드 | `snake_case` | `create_session_token` |
| 클래스·예외 | `PascalCase` | `ReportCheckerState`, `AuthenticationError` |
| 상수 | `UPPER_SNAKE_CASE` | `FILE_MAX_SIZE_MB` |
| 내부 구현 | 앞에 `_` | `_ensure_engine` |
| 타입 alias | `PascalCase` | `MessagePayload` |
| boolean | `is_`, `has_`, `can_`, `should_` | `is_running`, `has_result` |
| 컬렉션 | 복수 명사 | `messages`, `attachment_ids` |
| ID | 도메인이 드러나는 이름 | `conversation_id`, `job_id`, `emp_no` |

### 필수

- `data`, `info`, `item`, `result`, `tmp` 같은 포괄적인 이름은 작은 지역 범위가 아니면
  사용하지 않는다.
- 단위가 있는 값은 이름에 단위를 포함한다. 예: `timeout_sec`, `size_bytes`,
  `max_tokens`.
- 함수 이름은 동사로 시작하고, 값 또는 상태를 나타내는 property와 변수는 명사를
  사용한다.
- 약어는 프로젝트에 이미 정착한 `kb`, `s3`, `llm`, `emp_no`, `dept_cd`만 허용한다.
  새 약어는 문서 없이 만들지 않는다.
- 외부 API의 이름이 내부 convention과 다르면 경계에서 변환한다.

## 7. Import

Import 순서는 다음 세 그룹이며 그룹 사이에 빈 줄 하나를 둔다.

1. Python 표준 라이브러리
2. 외부 패키지
3. `wellbot` 내부 패키지

### 필수

- wildcard import를 사용하지 않는다.
- application code에서는 `from wellbot...` 절대 import를 기본으로 한다.
- 이름 충돌을 피할 필요가 없다면 불필요한 alias를 만들지 않는다.
- 무거운 import나 순환 참조를 피하기 위한 지연 import에는 이유를 주석으로 남긴다.
- import만으로 DB, AWS client 또는 환경 검증 같은 외부 부작용을 발생시키지 않는다.

### 허용 예외

- ORM 모델의 `.base`, package `__init__.py` 재노출처럼 같은 작은 package 내부 관계가
  명확한 경우 상대 import를 허용한다.
- 타입 검사에만 필요한 순환 참조는 `TYPE_CHECKING`을 사용한다.

## 8. 타입

### 필수

- 신규·수정하는 공개 함수, service 함수, FastAPI helper, State event에는 매개변수와
  반환 타입을 모두 작성한다.
- 내부 helper도 반환 타입을 작성한다.
- Python 3.11 표기를 사용한다: `list[str]`, `dict[str, object]`, `str | None`.
- `Any`는 외부 라이브러리 또는 JSON 경계에서 불가피한 경우에만 사용하고 경계에서
  구체 타입으로 변환한다.
- `# type: ignore`에는 오류 코드와 사유를 작성한다.
- 반환 구조가 정해진 `dict`에는 `TypedDict`, dataclass 또는 Pydantic model을 사용한다.
- 문자열 상태값은 가능한 `Literal` 또는 `Enum`으로 제한한다.

```python
from typing import Literal, TypedDict


JobStatus = Literal["idle", "processing", "done", "error"]


class ConversationRow(TypedDict):
    id: str
    title: str
    model_name: str
    created_at: float
```

`dict[str, object]`는 구조가 동적으로 변하는 진짜 범용 payload에만 사용한다. 함수
호출자와 반환자가 특정 키에 합의한다면 명시적인 타입을 정의한다.

## 9. 함수와 클래스 설계

### 필수

- 함수는 하나의 추상화 수준과 하나의 주된 책임을 가진다.
- boolean 인자가 여러 개 생기면 option object, Enum 또는 별도 함수를 검토한다.
- 함수 안에서 환경변수를 여러 번 읽지 않는다. 설정 계층에서 검증·변환해 주입한다.
- 외부 시스템 호출과 순수 데이터 변환을 분리한다.
- 숨은 전역 상태를 새로 추가하지 않는다. 필요한 경우 생명주기와 동시성 범위를
  명시한다.

### 검토 기준

- 함수가 50줄을 넘으면 helper/use case 분리를 검토한다.
- 모듈이 500줄을 넘으면 책임별 분리를 검토한다.
- State가 800줄을 넘으면 새 기능을 추가하기 전에 service와 sub-state 분리를 우선
  검토한다.
- 매개변수가 5개를 넘으면 dataclass/Pydantic command object를 검토한다.
- 중첩이 3단계를 넘으면 guard clause나 helper 분리를 검토한다.

이 수치는 기계적 실패 조건이 아니다. 다만 기준을 넘긴 코드에 기능을 계속 추가할 때는
PR에 분리하지 않은 이유를 적는다.

## 10. Docstring과 주석

### 필수

- 모든 모듈, 공개 클래스, 공개 service 함수에는 docstring을 작성한다.
- docstring은 코드가 무엇을 하는지보다 계약, 권한, 부작용, 예외와 반환 의미를
  설명한다.
- 매개변수 의미가 타입과 이름만으로 충분하지 않으면 `Args`, 반환 계약은 `Returns`,
  호출자가 처리해야 하는 예외는 `Raises`를 작성한다.
- 주석은 현재 코드의 동작을 번역하지 말고 선택 이유와 제약을 설명한다.
- 보안 검증, idempotency, transaction 경계와 concurrency 가정은 주석 또는 docstring에
  반드시 남긴다.
- 코드와 맞지 않는 주석은 기능 결함으로 취급하고 같은 PR에서 수정한다.

```python
def reserve_budget(command: BudgetReservation) -> Reservation:
    """모델 호출 전에 부서 예산을 원자적으로 예약한다.

    동일한 ``idempotency_key`` 요청에는 기존 예약을 반환한다.

    Raises:
        BudgetExceededError: 잔여 부서 예산이 예상 사용량보다 작은 경우.
    """
```

한국어와 영어를 한 문서 안에서 임의로 섞지 않는다. 사용자 도메인 설명은 한국어,
라이브러리 고유 용어와 식별자는 원래 영어를 유지한다.

## 11. 예외와 오류 응답

### 필수

- `except:`를 사용하지 않는다.
- 내부 로직에서는 처리 가능한 구체 예외만 잡는다.
- `except Exception`은 API, worker, background task 같은 최상위 경계에서 로그와 상태를
  남긴 뒤 실패를 격리하기 위한 경우에만 사용한다.
- 예외를 잡고 조용히 무시하지 않는다. 무시가 의도라면 이유와 관측 수단을 둔다.
- rollback 또는 임시 파일 정리는 `finally`, context manager 또는 명시적 cleanup으로
  보장한다.
- 사용자에게 Python 예외 문자열이나 AWS/DB 상세 오류를 그대로 반환하지 않는다.
- 사용자 오류는 안정적인 error code와 안전한 메시지로 변환한다.

도메인 예외는 기능 package에 정의한다.

```python
class AttachmentError(Exception):
    """첨부파일 처리 실패의 기반 예외."""


class AttachmentNotFoundError(AttachmentError):
    pass


class AttachmentAccessDeniedError(AttachmentError):
    pass
```

FastAPI endpoint는 도메인 예외를 HTTP 상태로 변환하고, service가 직접
`HTTPException`에 의존하지 않도록 한다. 입력 형식 검증 같은 순수 HTTP 경계 오류는
endpoint에서 `HTTPException`을 사용할 수 있다.

## 12. 로깅

현재 중앙 로깅 구조를 유지한다.

### 필수

- 모듈 logger는 `log = logging.getLogger(__name__)`를 사용한다.
- `print()`를 사용하지 않는다.
- 요청 시작점에서 가능한 `request_id`, `emp_no`, `conversation_id`, `message_id`,
  `job_id`를 context에 연결한다.
- 예외 stack이 필요한 경계에서는 `log.exception(...)`을 사용한다.
- 동일 실패를 여러 계층에서 stack trace로 중복 기록하지 않는다.
- 로그 메시지는 검색 가능한 짧은 영어 event phrase를 사용하고 값은 인자로 전달한다.
- password, JWT, cookie, secret, 전체 문서 본문, prompt 원문과 개인정보를 기록하지
  않는다.
- 토큰과 비용 로그에는 기능, 모델, 사용자/부서 식별자, latency와 request ID를
  구조화 필드로 기록한다.

```python
log.info(
    "attachment processing completed",
    extra={"file_no": file_no, "elapsed_ms": elapsed_ms},
)
```

문자열 보간을 미리 수행하는 f-string보다 logger placeholder 또는 `extra`를 사용한다.
로그 시스템에서 추출해야 하는 정보는 메시지 문자열에만 묻어두지 않는다.

## 13. 비동기와 동시성

### 필수

- `async def` 안에서 동기 DB, boto3, 파일 I/O 또는 CPU 파싱을 직접 실행하지 않는다.
- 동기 호출은 `asyncio.to_thread`, 공용 I/O executor 또는 전용 worker로 격리한다.
- CPU 작업은 ProcessPool 또는 외부 worker로 보낸다.
- timeout, 취소, retry와 동시성 상한을 외부 호출마다 정의한다.
- `asyncio.Queue`는 생산량이 제한된다는 증명이 없으면 `maxsize`를 지정한다.
- 프로세스 메모리 lock과 registry는 단일 프로세스에서만 유효함을 명시한다.
- retry 대상 작업은 idempotent해야 하며 idempotency key를 사용한다.
- 요청마다 새 ThreadPool/ProcessPool을 생성하지 않는다.

FastAPI endpoint가 전부 동기 I/O라면 `def`로 선언해 FastAPI thread pool에 맡긴다.
`async def`를 선택했다면 전체 호출 사슬이 비동기이거나 블로킹 구간이 명시적으로
격리되어야 한다.

## 14. FastAPI 경계

### 필수

- request/response payload는 Pydantic model로 정의한다.
- 인증과 소유권 검증은 클라이언트가 제공한 사용자 정보가 아니라 서버 인증
  context를 기준으로 한다.
- 데이터 변경 endpoint에는 인증·인가·CSRF/Origin 또는 bearer 정책을 명확히 한다.
- 파일 확장자만 신뢰하지 않고 크기, MIME, magic byte와 parser 한도를 검증한다.
- HTTP status와 도메인 error code를 일관되게 사용한다.
- 내부 예외 상세는 로그에 남기고 사용자 응답에는 노출하지 않는다.
- endpoint는 HTTP 변환과 use case 호출만 담당하고 DB/S3 절차를 직접 길게 구현하지
  않는다.
- rate limit, body limit와 idempotency 필요 여부를 endpoint별로 검토한다.

인가 검증은 UI에서 버튼을 숨기는 것과 별개로 데이터 변경 함수의 실행 시점에 다시
수행한다.

## 15. Reflex Page, Component와 State

### Page와 Component

- Component는 표시와 사용자 입력 전달에 집중한다.
- DB, AWS, 환경변수와 파일 시스템에 접근하지 않는다.
- 반복 UI는 작은 순수 component 함수로 분리한다.
- browser script 문자열은 page/state 안에서 이어 붙이지 않고 전용 builder에 둔다.
- 동적 값을 JavaScript/HTML에 넣을 때 escape 경계를 명확히 한다.

### State

- 공개 상태 필드에는 타입을 작성한다.
- `_` prefix 상태는 서버 전용이라는 의미로만 사용한다.
- `status: str`과 여러 boolean 조합보다 Enum/Literal 기반 상태 전이를 사용한다.
- event는 입력 검증 → 권한 확인 → use case 호출 → UI 상태 반영 순서로 짧게 유지한다.
- DB, S3, LLM orchestration이 길어지면 service/use case로 이동한다.
- background event는 실패, 취소와 재접속 후 복구 정책을 가진다.
- State lock을 가진 채로 느린 외부 작업을 기다리지 않는다.
- event 매개변수와 반환 타입도 생략하지 않는다.

## 16. DB와 SQLAlchemy

### 필수

- DB 접근은 `get_session()` context manager 또는 프로젝트가 정한 Unit of Work를
  사용한다.
- service 계층에서 transaction 범위를 명확히 하고 service 사이에서 숨은 commit을
  반복하지 않는다.
- ORM query에는 사용자 소유권과 부서 범위를 같은 query 조건으로 포함한다.
- `SELECT` 후 `INSERT/UPDATE`하는 제한 로직은 lock, unique constraint 또는 원자적
  update로 race를 방지한다.
- `datetime`의 timezone 기준을 명시한다. 애플리케이션에서는 aware datetime을
  사용하고 DB 경계에서 변환 정책을 통일한다.
- schema 변경은 사내 DDL 신청 절차를 따른다(개발자가 직접 DDL을 실행하지 않는다).
  변경이 확정되면 저장소 기준 스키마 문서인 `docs/ddl.sql`을 같은 PR에서 갱신한다.
  Alembic 같은 버전 migration 도구는 스키마 변경이 빈발해질 때 재평가한다.
- 신규 테이블은 DDL 신청 절차의 비용을 감안해 범용·확장 가능하게 설계한다.
  코드값 행 확장을 컬럼 추가보다 우선하고, 예견되는 컬럼은 초기 설계에 포함하며,
  DDL 변경은 배치로 묶어 신청한다.
- raw SQL을 사용하면 bind parameter를 사용한다.
- N+1 query와 무제한 `.all()`을 피하고 list API에는 limit 또는 pagination을 둔다.

ORM Python 속성은 읽기 쉬운 도메인 이름이 이상적이지만 기존 물리 모델 약어를
변경하면 영향이 크므로, 신규 모델부터 명확한 이름을 우선하고 기존 이름 변경은 별도
호환성 작업으로 진행한다.

## 17. AWS와 외부 서비스

### 필수

- boto3 client 생성, timeout, retry와 connection pool 설정을 공통 factory에서
  관리한다.
- region, bucket, KB ID와 model ID를 코드에 직접 넣지 않는다.
- 모든 외부 호출에 timeout을 둔다.
- 재시도는 throttle과 일시 오류 등 안전한 오류에만 적용하고 최대 횟수를 제한한다.
- S3 key는 신뢰할 수 있는 서버 값으로 구성하고 사용자 입력 path를 정규화한다.
- S3와 DB를 함께 변경하면 부분 실패 복구, outbox 또는 reconciliation을 설계한다.
- 외부 응답은 경계에서 검증한 뒤 내부 model로 변환한다.
- 모델 호출은 공통 비용·모델 정책 계층을 우회하지 않는다.

## 18. 설정과 secret

### 필수

- 환경변수는 설정 모듈에서 한 번 읽고 타입 변환과 범위 검증을 수행한다.
- import 시점에 환경변수 검증이나 외부 연결을 강제하지 않는다.
- 필수 설정이 잘못되면 실제 요청 시점이 아니라 애플리케이션 startup validation에서
  안전하게 실패시킨다. 단, 단위 테스트 import 가능성은 유지한다.
- secret, password와 token을 repository, 기본 설정 또는 로그에 넣지 않는다.
- YAML/JSON 설정은 Pydantic 또는 명시적 schema로 검증한다.
- 설정 key는 `snake_case`, 환경변수는 `UPPER_SNAKE_CASE`를 사용한다.
- 모델과 prompt 설정 변경은 버전 또는 변경 이력을 남긴다.

## 19. 보안 규칙

### 필수

- 인증과 인가는 별개의 단계로 처리한다.
- 모든 객체 조회·수정·삭제에 사용자 또는 부서 소유권을 서버에서 검증한다.
- 관리자 mutation은 실행 시점마다 현재 역할을 확인한다.
- 사용자 입력은 출력 위치에 맞게 escape한다. HTML, JavaScript, S3 key와 SQL의 escape
  규칙을 혼용하지 않는다.
- 파일명은 표시용 원본명과 저장용 안전한 key를 분리한다.
- 보안 실패 메시지로 계정 존재 여부, 내부 경로 또는 외부 서비스 상세를 노출하지
  않는다.
- dependency 추가 전 유지보수 상태, 라이선스와 취약점 영향을 확인한다.
- 보안 검증을 완화하는 변경에는 반드시 회귀 테스트를 추가한다.

## 20. 테스트

### 무엇을 테스트하는가

전 기능 테스트가 목표가 아니다. 테스트는 **실패가 조용한 곳 × 실패 비용이 큰 곳**에
우선 투자한다. UI가 깨지면 사용자가 바로 발견하지만, 권한 우회·비용 초과·정합성
훼손은 조용히 진행되므로 테스트만이 잡는다. 기능 단위가 아니라 기능 안의 위험한
조각 단위로 판단한다.

우선순위:

1. **최상** — 권한·소유권·인가, 비용/한도 로직: 성공·거부·경계값을 모두 작성한다.
2. **높음** — 분기가 많은 순수 함수(파싱, 청킹, 토큰 추정, 카운터/만료 계산):
   mock 없이 작성할 수 있으므로 구현 시점에 함께 만든다.
3. **중간** — service use case: 로직 밀도에 비례해 판단한다.
4. **낮음/생략** — 얇은 State 글루(대신 그것이 호출하는 service를 테스트), Reflex
   UI 렌더링(QA 수동 스모크로 확인), 외부 서비스 실호출(경계에서 mock).

작성 트리거 — 다음에 해당하면 테스트를 쓴다:

- 버그를 수정할 때(그 버그를 잡았을 회귀 테스트를 먼저 추가 — 아래 필수 규칙).
- 보안·권한·비용 로직을 추가·변경할 때.
- 분기가 많은 순수 함수를 새로 만들 때.
- 리팩터링 직전 characterization test로 기존 동작을 고정할 때.

이 트리거에 해당하지 않는 얇은 CRUD와 UI 배선은 테스트를 생략할 수 있다. 생략은
결함이 아니라 우선순위 판단이다.

### 이름과 구조

- 테스트 파일: `test_<target>.py`
- 테스트 함수: `test_<condition>_<expected_result>()`
- 공통 fixture: 가장 가까운 범위의 `conftest.py`
- 하나의 테스트는 하나의 행동을 검증한다.

```python
def test_download_rejects_attachment_owned_by_another_user() -> None:
    ...
```

### 필수

- 버그 수정은 실패하는 회귀 테스트를 먼저 추가한다.
- 권한, 비용 한도, DB/S3 정합성과 idempotency는 성공·실패·경계값을 모두 테스트한다.
- 네트워크와 실제 AWS에 의존하는 테스트는 기본 단위 테스트와 분리한다.
- 시간, UUID와 외부 응답은 주입하거나 고정하여 결정적으로 만든다.
- private 구현보다 공개 계약과 관측 가능한 결과를 검증한다.
- mock 호출 횟수만 검증하지 말고 저장 결과, 응답과 부작용을 확인한다.
- background job은 재시작, 중복 전달, 취소와 retry를 테스트한다.

테스트 marker는 역할을 명확히 한다.

- 기본: 빠른 단위 테스트
- `integration`: DB 또는 격리된 외부 대체 서비스를 사용하는 테스트
- `slow`: 보고서 파싱, 부하 등 일반 PR에서 선택적으로 실행하는 테스트

실제 marker를 사용하기 전 `pyproject.toml`에 등록한다.

테스트 실행 환경: 로컬 개발 환경은 편집 전용이므로 테스트는 uv가 준비된 QA
서버에서 `uv run pytest`로 실행한다(CI 도입 후에는 GitHub Actions 병행). DB·AWS가
필요한 통합 검증은 사내망에서만 가능하다 — 환경 분리는 CONTRIBUTING 참조.

## 21. 설정 파일과 브라우저 코드

- YAML/JSON은 공백 2칸 들여쓰기를 사용하고 tab을 사용하지 않는다.
- YAML key와 내부 JSON key는 `snake_case`를 기본으로 한다.
- 외부 API 형식은 경계에서 그대로 사용하고 내부 model로 변환한다.
- JavaScript 식별자는 `camelCase`, class는 `PascalCase`, 상수는
  `UPPER_SNAKE_CASE`를 사용한다.
- Python에서 JavaScript 또는 HTML 문자열을 만들 때 동적 값은 JSON encoder 또는
  문맥에 맞는 escape 함수를 사용한다.
- 긴 script는 Python State 안에 두지 않고 전용 script builder 또는 정적 asset으로
  분리한다.

## 22. Git과 PR

### Commit

- 하나의 commit에는 하나의 논리적 변경을 담는다.
- 제목은 명령형으로 짧게 작성한다.
- 기능 변경과 전체 포맷 변경을 같은 commit에 섞지 않는다.
- 생성물, secret, 운영 데이터와 개인 설정을 commit하지 않는다.

권장 제목 예시:

```text
fix: enforce attachment ownership on download
feat: reserve department token budget before model call
refactor: extract chat streaming use case
perf: offload bcrypt verification to worker thread
chore: bump reflex 0.9.7 -> 0.9.8
docs: define Wellbot coding convention
```

### PR 필수 내용

- 문제와 변경 이유
- 주요 변경 사항과 영향받는 계층
- 보안·비용·동시성·데이터 정합성 영향
- DB·설정·AWS 변경과 배포 순서
- 검증 결과
- 롤백 방법
- 관련 architecture review ID 또는 ADR

## 23. 완료 정의

변경은 다음 조건을 만족해야 완료로 본다.

- [ ] 변경 코드의 책임과 계층이 적절하다.
- [ ] 신규·수정 함수의 타입과 필요한 docstring이 있다.
- [ ] 인증·인가와 사용자 소유권을 서버에서 검증한다.
- [ ] 외부 I/O에 timeout, 오류 변환과 필요한 동시성 제한이 있다.
- [ ] 로그에 secret·token·문서 원문·불필요한 개인정보가 없다.
- [ ] DB/S3 부분 실패와 retry의 정합성을 검토했다.
- [ ] 정상, 실패, 권한과 경계값 테스트를 추가했다.
- [ ] 설정·schema·운영 방식 변경을 문서화했다.
- [ ] formatter, linter, type checker와 테스트가 CI 기준을 통과한다.

마지막 항목은 도구가 저장소에 도입된 뒤 필수 gate로 전환한다. 도입 전에는 코드
리뷰에서 같은 기준을 수동으로 확인한다.

## 24. 자동화 도입안

문서를 만든 뒤 한 번에 strict 설정을 켜면 기존 코드 수정량이 너무 커질 수 있다.
다음 순서로 도입한다.

### 1단계: 형식과 명백한 오류

- Ruff formatter, `E`, `F`, `W`, `I` 규칙
- line length 100, target Python 3.11
- `.editorconfig`와 `.gitattributes`(`* text=auto eol=lf`)로 줄 끝 정규화
- 신규·수정 파일에 우선 적용

권장 Ruff 기준 예시:

```toml
[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "W", "I"]

[tool.ruff.format]
line-ending = "lf"
```

### 2단계: 현대화와 결함 예방

- Ruff `UP`, `B`, `SIM`, `ASYNC`를 항목별로 활성화
- broad exception과 불필요한 ignore 정리
- pre-commit에서 formatter와 linter 실행

규칙 묶음을 추가할 때는 현재 위반 수와 수정 위험을 먼저 확인하고, 기능 변경과 별도
PR로 반영한다.

### 3단계: 점진적 타입 검사

- mypy에 `check_untyped_defs = true` 적용
- 핵심 service와 API schema부터 검사 대상 지정
- SQLAlchemy와 Reflex의 framework 동작을 확인하며 strictness 확대
- 신규 모듈은 완전한 타입 표기를 요구

### 4단계: CI gate

1. format check
2. lint
3. type check
4. unit test
5. integration test 또는 별도 pipeline
6. dependency/security scan

도구 버전과 실행 명령은 실제 개발 환경에서 검증한 뒤 `pyproject.toml`과 이 문서에
고정한다. 현재 로컬 환경에서는 `uv` 또는 프로젝트 가상환경 Python이 준비되어 있지
않아 이 검토에서 도구 설치와 실행은 수행하지 않았다.

도구 실행 위치는 환경 분리 제약을 따른다(CONTRIBUTING 참조): Ruff·mypy·단위
테스트는 CI(GitHub Actions)와 QA에서 실행 가능하고, DB·AWS가 필요한 통합 검증은
사내망 QA에서 수동으로 수행한다. pre-commit은 로컬에 실행 환경이 없는 동안 강제
장치가 아니라 선택 사항으로 둔다.

## 25. 컨벤션 예외 절차

규칙을 지키는 것이 프레임워크 제약, 성능, 보안 또는 호환성을 해치는 경우 예외를
허용한다. 예외는 다음을 남긴다.

1. 적용하지 못한 규칙
2. 기술적 이유
3. 위험을 줄이는 대안
4. 제거 조건 또는 추적 이슈

```python
# Ruff 예외 사유: Reflex가 이 public class attribute를 상태 필드로 수집한다.
# 제거 조건: Reflex의 typed field API로 전환한 뒤 재검토.
```

단순히 기존 코드가 같은 방식이라는 이유만으로 예외를 만들지 않는다.
