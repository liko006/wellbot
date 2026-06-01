# 프로젝트 구조 리팩토링 변경 계획

> 작성일: 2026-05-29
> 대상 브랜치: `refactor/project-structure`
> 진단 문서: [diagnosis.md](./diagnosis.md)

본 문서는 [diagnosis.md](./diagnosis.md) 에서 도출한 개선 방향을 실제 변경 단위로 분해한 실행 계획이다. 각 단계는 **독립적으로 PR 분리 가능**하도록 설계되어 있으며, 단계 간 의존성은 "선행 조건" 항목에 명시한다.

---

## 단계 개요

| # | 작업 | 영향 범위 | 위험도 | 선행 조건 |
| - | --- | --- | --- | --- |
| 1 | `chat_state.py` 분리 & 데이터 모델 추출 | state, components 다수 | 高 | — |
| 2 | `services/` 도메인 그룹화 & `bedrock_client` 분리 | services 전체, state | 中 | 1 |
| 3 | 설정·경로 재배치 (`constants.py` ↔ YAML, `paths.py` 신설) | services, state | 中 | — |
| 4 | 네이밍 정리 (모델 별칭, 페이지 함수명, `services/config.py` 개명, State re-export) | 전역 | 低 | 2, 3 |
| 5 | 기타 정리 (프롬프트 ASCII 화, `.gitignore`, `ruff` 도입) | 빌드/운영 | 低 | — |

---

## 1단계 — `chat_state.py` 분리 & 데이터 모델 추출

### 목적
1,261줄 단일 State 의 책임을 분리하고, 외부에서 import 되는 데이터 클래스를 별도 모듈로 빼 순환 import 위험을 제거한다.

### 변경안

**1-A. 데이터 모델 추출 (선행 작업)**
- 신설: `wellbot/state/chat_models.py`
- 이동 대상: `Message`, `Conversation`, `AttachmentInfo`, `ModelInfo`, `PromptInfo` 등 `rx.Base` 상속 데이터 클래스
- import 경로 변경:
  - [components/search_modal.py:8](../../wellbot/components/search_modal.py#L8)
  - [components/sidebar/conversation_list.py:8](../../wellbot/components/sidebar/conversation_list.py#L8)
  - [components/chat/message_bubble.py:10](../../wellbot/components/chat/message_bubble.py#L10)
  - [components/chat/gnb.py:8](../../wellbot/components/chat/gnb.py#L8)
  - [components/chat/attachment_chip.py:10](../../wellbot/components/chat/attachment_chip.py#L10)
  - [components/chat/input_bar.py:12](../../wellbot/components/chat/input_bar.py#L12)
  - [components/chat/message_area.py:11](../../wellbot/components/chat/message_area.py#L11)

**1-B. State 책임 분리 — 재진단 (2026-05-29)**

> 1-A 완료 후 ChatState 내부를 다시 살펴본 결과, **mixin 다중상속 안 (案) 은 위험 대비 효과가 낮다**. 다음과 같이 접근법을 재정비한다.

#### 현재 ChatState 책임 인벤토리

| 책임 영역 | var (state field) | computed (@rx.var) | event handler |
| --- | --- | --- | --- |
| 대화 목록·전환 | `conversations`, `current_conversation_id` | `current_messages`, `has_messages`, `current_title`, `sorted_conversations` | `on_load`, `create_new_conversation`, `switch_conversation`, `delete_conversation` |
| 입력·전송·스트리밍 | `current_input`, `is_loading`, `is_thinking`, `streaming_content`, `_cancel_requested`, `_emp_no` | `can_send`, `has_streaming` | `set_input`, `send_message`, `stop_generation` |
| 모델/프롬프트/에이전트 모드 | `selected_model`, `thinking_enabled`, `selected_prompt`, `show_style_panel`, `selected_agent_mode` | `model_names`, `model_list`, `trigger_label`, `model_supports_thinking`, `prompt_list`, `current_system_prompt`, `agent_mode_list`, `current_agent_mode_*` | `set_model`, `toggle_thinking`, `toggle_style_panel`, `select_prompt`, `set_agent_mode` |
| 검색 | `search_query` | `is_searching`, `has_search_results` | `set_search_query`, `clear_search_query` |
| 환영 메시지 | `greeting_text` | — | (`_refresh_greeting` 내부 호출) |
| 첨부 업로드/다운로드 | `pending_attachments`, `attachment_error`, `conversation_attachments`, `_pending_msg_id` | `accepted_file_extensions`, `model_supports_vision`, `has_pending_attachments`, `has_conversation_attachments`, `conversation_attachment_count`, `has_processing_attachments` | `set_attachment_error`, `remove_pending_attachment`, `download_attachment`, `trigger_upload`, `poll_attachments` |

전체 21개 var, 21개 computed, 16개 event handler. 책임 간 결합도 높음:
- `send_message` 가 conversation / 모델 / 프롬프트 / 첨부 / 스트리밍 var 를 모두 읽고 쓴다.
- `_get_current_index`, `_ensure_conversation`, `_update_conversation` 같은 헬퍼는 거의 모든 책임이 공유.

#### Mixin 다중상속 안의 문제

- **Reflex State 의 var 는 클래스 정의 시점에 메타클래스가 등록**한다. 다중상속으로 합치면 MRO 순서에 따라 var 가 어느 mixin 에서 선언됐는지 추적이 어려워지고, 디버깅 시 "이 var 가 어느 파일에 있나" 가 비명시적이 된다.
- **이벤트 핸들러가 다른 책임의 var 를 수정하는 코드 (`send_message` 내부의 `self.is_loading = ...`, `self.pending_attachments = []` 등)** 가 다수. mixin 분리 후에도 cross-mixin 접근이 그대로 남아 응집도 이득이 사라진다.
- Reflex 가 향후 State 메타클래스/동작을 바꾸면 다중상속 패턴이 깨질 수 있다 (현재 0.8.28).

#### 권장 접근법: 헬퍼 모듈 추출 (클래스 단일 유지)

ChatState 클래스는 **단일 클래스로 유지**하되, 책임별로 **순수 헬퍼 모듈** 을 추출한다. State 메서드는 헬퍼에 위임만 한다.

```
wellbot/state/
  chat_state.py            # ChatState 클래스 (단일) - var/event handler 본체
  chat_models.py           # 데이터 모델 (1-A 완료)
  chat_helpers/
    __init__.py
    conversations.py       # 대화 빌더, persist 헬퍼 (DB I/O 위임)
    attachments.py         # 첨부 sync/이미지 블록 변환 등 순수 함수
    system_prompt.py       # _augment_system_with_attachments 추출
    upload_script.py       # trigger_upload 의 JS 스크립트 생성 함수
    download_script.py     # download_attachment 의 JS 스크립트 생성 함수
```

핵심:
- **순수 함수만 추출** — `self` 를 인자로 받거나 받지 않는다. State var 를 읽기만 하고 새 값을 리턴해 호출부에서 `self.x = result` 로 대입. 클래스 분할 없음.
- **JS 스크립트 빌더**(`trigger_upload`, `download_attachment` 의 거대한 f-string) 를 별도 모듈로 빼면 chat_state.py 가 즉시 ~200줄 가벼워진다.
- **`_augment_system_with_attachments`, `_collect_image_blocks`** 도 State 의존성이 약하므로 헬퍼로 이관.
- 위험·복잡도가 낮고, Reflex 동작과 무관하게 동작.

#### 예상 효과
- chat_state.py 1,261줄 → 약 **700~800줄** 수준으로 축소 (JS 빌더 + 첨부 헬퍼 + 시스템 프롬프트 빌더 추출분)
- mixin 다중상속의 var 추적 비용 없음
- 추후 더 분리가 필요해지면 sub-state 도입 (`rx.State` 의 children state 패턴) 을 별도로 검토

#### 작업 순서 (1-B 재정의)
1. `state/chat_helpers/` 패키지 신설
2. JS 스크립트 빌더 추출 (`trigger_upload`, `download_attachment` → `upload_script.py`, `download_script.py`)
3. `_augment_system_with_attachments` → `system_prompt.py`
4. `_collect_image_blocks` → `attachments.py`
5. 첨부 sync 로직 (`_sync_attachments_from_db` 의 row → AttachmentInfo 변환 등) → `attachments.py`
6. 각 단계마다 `python -m py_compile` + `reflex run` 수동 검증

#### 검증
- 채팅 전송 / 대화 전환 / 첨부 업로드 / 다운로드 / 검색 5개 시나리오 수동 테스트
- 추출된 헬퍼는 인자/리턴 타입을 명시해 향후 단위 테스트가 가능하도록 함

#### 위험
- 헬퍼가 `self._emp_no` 같은 internal var 를 필요로 하는 경우, 함수 시그니처에 명시적으로 인자로 받아야 한다. (의존성을 숨기지 말 것)
- 큰 f-string JS 코드의 따옴표·중괄호 이스케이프가 깨지기 쉬움 — 추출 전후로 동일성 확인 필수

---

## 2단계 — `services/` 도메인 그룹화 & `bedrock_client` 분리

### 목적
14개 평탄 모듈을 도메인 패키지로 묶고, 607줄 `bedrock_client.py` 의 책임을 분리한다.

### 변경안

**2-A. 디렉터리 그룹화**
```
wellbot/services/
  auth/        ← auth_service.py
  chat/        ← chat_service.py, response_filter.py, tool_executor.py
  ai/          ← bedrock/, embedding_service.py
  files/       ← attachment_service.py, file_parser.py, chunker.py, storage_service.py
  admin/       ← admin_service.py
  core/        ← database.py, settings.py (구 config.py, 4단계에서 개명)
```

- 각 패키지 `__init__.py` 에서 공개 API 재노출 → 호출부는 `from wellbot.services.chat import chat_service` 형태로 단순화
- 기존 호출부 ([state/chat_state.py:31](../../wellbot/state/chat_state.py#L31), [api/upload.py:45](../../wellbot/api/upload.py#L45) 등) 의 import 경로 일괄 변경

**2-B. `bedrock_client.py` 분리**
```
wellbot/services/ai/bedrock/
  __init__.py        # 공개 API (converse_stream, generate_title 등)
  converse.py        # Converse API 호출 래퍼
  tool_loop.py       # tool-use 루프 (TOOL_USE_MAX_ITERATIONS 등)
  title.py           # 제목 생성 (TITLE_MODEL_ID, TITLE_SYSTEM_PROMPT)
  image.py           # 이미지 블록 가공 (IMAGE_MAX_SIZE_MB)
```

### 작업 순서
1. 패키지 디렉터리 생성 + `git mv` 로 파일 이동 (히스토리 보존)
2. 각 패키지 `__init__.py` 에 re-export 추가
3. 호출부 import 일괄 치환 (`rg -l "from wellbot.services\." | xargs sed -i ...`)
4. `bedrock_client.py` 내부 분리 (단독 PR)

### 검증
- `python -c "import wellbot.wellbot"` 으로 import 그래프 검증
- 첨부 업로드, RAG 검색, tool 호출 시나리오 수동 테스트

### 위험
- `git mv` 누락 시 히스토리 단절 — 반드시 `git status` 로 rename 인식 확인
- `bedrock_client` 내부 함수가 서로 참조하는 경우 순환 import → 공용 헬퍼는 `bedrock/_common.py` 로 추출

---

## 3단계 — 설정·경로 재배치

### 목적
운영 가변값과 불변 상수를 분리하고, 반복되는 경로 계산을 단일 모듈로 모은다.

### 변경안

**3-A. `wellbot/paths.py` 신설**
```python
# wellbot/paths.py
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
PROMPTS_DIR = CONFIG_DIR / "prompts"
ENV_FILE = PROJECT_ROOT / ".env"
```
- 적용 대상: [services/config.py:106-108](../../wellbot/services/config.py#L106-L108), [services/config.py:213](../../wellbot/services/config.py#L213) 등 `Path(__file__).resolve().parent.parent.parent` 패턴 전부

**3-B. `constants.py` 분리**

운영 가변값 → YAML 또는 환경변수로 이관:
| 현재 위치 | 이관 대상 |
| --- | --- |
| `TITLE_MODEL_ID`, `TITLE_MAX_TOKENS`, `TITLE_TEMPERATURE`, `TITLE_SYSTEM_PROMPT` | `config/models.yaml` (title 섹션) |
| `EMBEDDING_MODEL_ID`, `EMBEDDING_DIMENSION` | `config/models.yaml` (embedding 섹션) |
| `FILE_PARSER_MODE`, `FILE_PARSER_FALLBACK` | `.env` |
| `SEARCH_TOP_K`, `TOOL_USE_*` | `config/agents.yaml` 또는 별도 `config/retrieval.yaml` |

`constants.py` 에는 다음만 남긴다:
- 타임존 (`KST`)
- 토큰/세션 길이 상수
- 파일 확장자 frozenset
- UI 임계값 (`SCROLL_THRESHOLD`, `BTN_THRESHOLD`)

**3-C. 모듈 import 시 사이드이펙트 일괄 제거**

엔트리포인트(`wellbot/wellbot.py`) 가 실행되기 전에 환경변수가 로드되지 않은 상태로 service 모듈을 import 하면 `RuntimeError` 가 즉시 발생하는 구조. 1단계·2단계 리팩토링 중 import 검증 시에도 이 문제가 반복 노출되었다. 다음 3개 사이드이펙트를 한 단위로 묶어 처리한다.

1. **`load_dotenv()` 모듈 레벨 실행**
   - 위치: [services/core/config.py:15](../../wellbot/services/core/config.py#L15)
   - 변경: `init_env()` 같은 명시적 함수로 감싸고, [wellbot/wellbot.py](../../wellbot/wellbot.py) 에서 다른 import 전에 1회 호출

2. **`DB_URL` 환경변수 강제 검증**
   - 위치: [services/core/database.py:15](../../wellbot/services/core/database.py#L15)
   - 현 동작: 모듈 import 시점에 `DB_URL` 없으면 `RuntimeError`
   - 변경 방향: 모듈 로드 시점이 아니라 **첫 세션 생성 시점**(`get_session` 등)으로 검증을 지연. 또는 lazy engine 패턴 (`_engine = None` + getter) 도입
   - 이점: 단위 테스트·CLI 스크립트가 DB 없이도 모듈을 import 가능

3. **`JWT_SECRET` 환경변수 강제 검증**
   - 위치: [services/auth/auth_service.py](../../wellbot/services/auth/auth_service.py) (모듈 레벨)
   - 동일 패턴 — 토큰 발급/검증 함수 진입 시점에 검증으로 지연

**원칙**: 모듈 import 는 부수효과 없이 항상 성공해야 한다. 외부 자원(DB, 비밀키, 파일시스템) 검증은 자원이 실제로 필요한 함수 호출 시점에서.

### 3-C 검증
- `DB_URL` / `JWT_SECRET` 둘 다 미설정 상태에서 `python -c "import wellbot.wellbot"` 가 RuntimeError 없이 성공해야 함
- `pytest` 도입 시 fixture 가 환경변수 주입 전에 모듈을 import 해도 깨지지 않아야 함

### 작업 순서
1. `paths.py` 신설 + 하드코딩 경로 치환 (단독 PR, 저위험)
2. `load_dotenv` 명시 호출로 변경
3. `constants.py` → YAML/env 이관 (값별로 PR 분리 가능)

### 검증
- YAML 이관값: `get_config().models[0].model_id` 등으로 로드 검증
- `pytest` 가 도입되면 환경변수 fixture 가 테스트 단위로 깨끗하게 격리되는지 확인

### 위험
- YAML 스키마 변경 시 기존 prod `.env` 와 충돌 — 이관 전 prod 설정 백업 필수

---

## 4단계 — 네이밍 정리

### 목적
혼용·중복 네이밍을 해소해 호출부 가독성을 높인다. (진단 문서 2.5절 대응)

### 변경안

**4-A. 모델 도메인 별칭 추가**
```python
# wellbot/models/__init__.py
Agent = AgntM
Employee = EmpM
Dept = DeptM
ChatMessage = ChtbMsgD
ChatSummary = ChtbSmryD
ChatMessageAttachment = ChtbMsgAtchFileD
Attachment = AtchFileM
AuthToken = CrtfToknN
AgentMemory = AgntMmryUseN
```
- 신규 코드에서는 별칭 사용 권장. 기존 SI 약어는 유지하되 점진적 마이그레이션.

**4-B. 페이지 함수명 통일**
- `pages/admin.py::admin` → `admin_page`
- `pages/index.py::index` → `index_page`
- `pages/login.py::login` → `login_page`
- `pages/register.py::register` → `register_page`
- 동시에 `pages/__init__.py` 에 re-export 추가:
  ```python
  from .admin import admin_page
  from .index import index_page
  from .login import login_page
  from .register import register_page
  ```
- 엔트리포인트 [wellbot/wellbot.py](../../wellbot/wellbot.py) 의 import 4줄을 1줄로 축약

**4-C. `services/config.py` → `services/core/settings.py`**
- 호출부 `from wellbot.services.config import get_config` → `from wellbot.services.core.settings import get_config`
- 2단계의 `services/core/` 디렉터리 신설과 함께 수행
- `AppConfig`, `ModelConfig`, `PromptTemplate`, `AgentMode` 도 함께 이동

**4-D. `state/__init__.py` 에 `AdminState` 추가**
```python
from .admin_state import AdminState
from .auth_state import AuthState
from .chat_state import ChatState
from .ui_state import UIState

__all__ = ["AdminState", "AuthState", "ChatState", "UIState"]
```

**4-E. `agent` 도메인 명확화 (옵션)**
- DB `AgntM` 은 `Agent` 로, 채팅의 `agent_modes` 는 `assistant_mode` 또는 `chat_mode` 로 개명 검토
- 영향 범위가 크므로 별도 PR + 결정 필요

### 작업 순서
1. State re-export (4-D) — 최소 변경, 즉시 가능
2. 페이지 함수명 + `pages/__init__.py` (4-B)
3. 모델 별칭 (4-A)
4. `services/config.py` 개명 (4-C) — 2단계와 함께
5. agent 도메인 분리 (4-E) — 별도 결정

### 검증
- `ruff` 의 unused-import / undefined-name 룰로 누락 탐지
- `reflex run` 수동 확인

### 위험
- 페이지 함수명 변경 시 `app.add_page(...)` 의 라우트는 그대로 유지해야 함 — `route="/"` 등 인자만 확인

---

## 5단계 — 기타 정리

### 5-A. 프롬프트 파일명 ASCII 화
- `config/prompts/구조적.md` → `structural.md`
- `config/prompts/균형적.md` → `balanced.md`
- `config/prompts/미적용.md` → `none.md`
- `config/prompts/일반.md` → `general.md`
- `config/prompts/정확성.md` → `accuracy.md`
- [config/prompts.yaml](../../config/prompts.yaml) 의 `name` 필드와 `default` 값을 새 슬러그로 갱신
- 한국어 표시명은 `description` 필드로 유지
- [services/config.py:141](../../wellbot/services/config.py#L141) `by_name[f.stem]` 매핑 검증

### 5-B. `.gitignore` 점검
- `.states/`, `.web/`, `__pycache__/`, `.venv/` 가 포함되어 있는지 확인
- 누락 시 추가 + `git rm --cached -r <dir>` 로 추적 해제

### 5-C. `ruff` 도입
```toml
# pyproject.toml
[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "TID", "UP"]
ignore = ["E501"]  # line-length 는 별도 설정

[tool.ruff.lint.isort]
known-first-party = ["wellbot"]
```
- CI 에 `ruff check .` 추가 (CI 가 없다면 pre-commit hook)

### 5-D. `scripts/` 패키지화 (옵션)
- `scripts/verify_attachment_index.py` 를 `wellbot/scripts/verify_attachment_index.py` 로 이동
- 실행: `python -m wellbot.scripts.verify_attachment_index`
- PYTHONPATH 의존성 제거

### 검증
- `ruff check .` 통과
- `reflex run` 정상 기동
- 5-A 적용 후 프롬프트 선택 UI 에서 한국어 표시명이 그대로 노출되는지 확인

---

## 진행 체크리스트

- [x] 1-A 데이터 모델 추출 (`state/chat_models.py`) ✅ 2026-05-29
- [x] 1-B ChatState 헬퍼 모듈 추출 (`state/chat_helpers/`) ✅ 2026-05-29 — 1,261줄 → 925줄 (-336)
- [x] 2-A `services/` 도메인 패키지 그룹화 ✅ 2026-05-29 — auth/chat/ai/files/admin/core 6개 그룹, 호출부 import 일괄 갱신 후 shim 전량 제거 (평탄도 완전 해소)
- [x] 2-B `bedrock_client.py` 분리 ✅ 2026-05-29 — ai/bedrock/{client,converse,tool_loop,title,image}.py 5모듈, 원본은 삭제
- [x] 3-A `wellbot/paths.py` 신설 ✅ 2026-05-29 — `Path(__file__).resolve().parent.parent.parent` 패턴 6곳 모두 단일 모듈로 통합
- [x] 3-B `constants.py` → YAML 이관 ✅ 2026-05-29 — TITLE_* / EMBEDDING_* 5개 상수를 `config/models.yaml` 의 title/embedding 섹션으로 이전 (튜닝 파라미터는 constants 유지)
- [x] 3-C 모듈 import 시 사이드이펙트 제거 ✅ 2026-05-29 — `wellbot/env.py::init_env()` 신설 + DB_URL / JWT_SECRET 검증을 lazy 화. 환경변수 없이 entrypoint import 통과
- [x] 4-A ORM 모델 전면 개명 + 약어 alias ✅ 2026-05-29 — `EmpM` → `Employee` 등 9개 모델 도메인명으로 개명, 약어는 `EmpM = Employee` alias 로 호환 유지
- [x] 4-B 페이지 함수명 통일 + `pages/__init__.py` ✅ 2026-05-29 — `admin/index/login/register` → `*_page`, 엔트리포인트 import 9줄 → 4줄
- [x] 4-C `services/core/config.py` → `settings.py` ✅ 2026-05-29 — 디렉터리·모듈·`rxconfig` 3중 의미 해소
- [x] 4-D `state/__init__.py` 에 `AdminState` 추가 ✅ 2026-05-29 — 4개 State 모두 re-export
- [x] 4-E 채팅 `agent_modes` → `chat_modes` 전면 개명 ✅ 2026-05-29 — YAML/dataclass/state var/UI 컴포넌트 일괄 변경. DB의 `Agent` 도메인과 분리
- [ ] 5-A 프롬프트 파일명 ASCII 화
- [ ] 5-B `.gitignore` 점검
- [ ] 5-C `ruff` 도입
- [ ] 5-D (옵션) `scripts/` 패키지화
- [ ] 6 docstring·주석 스타일 정리 — [style-guide.md](./style-guide.md) 기준으로 전체 코드 점검
