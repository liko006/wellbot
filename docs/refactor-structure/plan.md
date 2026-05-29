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

**1-B. State 책임 분리**

`ChatState` 를 mixin 4종으로 쪼개 다중상속으로 합친다 (Reflex State 는 다중상속을 지원하지만 동일 var 충돌 주의):

```
wellbot/state/chat/
  __init__.py            # ChatState 조립 (다중상속)
  messaging.py           # 전송·스트리밍·중단
  conversation.py        # 목록·전환·제목 생성·삭제
  attachment.py          # 첨부 업로드/제거 연동
  search.py              # 대화 검색·하이라이트
```

- 공통 var/이벤트는 베이스 mixin 에 정의
- 기존 import (`from wellbot.state.chat_state import ChatState`) 는 호환을 위해 `wellbot/state/chat_state.py` 를 얇은 re-export shim 으로 남긴다 → 2단계 완료 후 제거

### 작업 순서
1. `chat_models.py` 추출 + 호출부 import 일괄 수정 (단독 PR)
2. mixin 분리 (단독 PR)
3. shim 제거 + 호출부 import 정리 (4단계와 함께)

### 검증
- `reflex run` 으로 앱 기동 확인
- 채팅 전송 / 대화 전환 / 첨부 업로드 / 검색 4개 시나리오 수동 테스트
- mixin 분리 후 var 이름 충돌 grep: `rg "self\." wellbot/state/chat/` 으로 동일 var 중복 탐지

### 위험
- Reflex State 다중상속 시 동일 var 재정의 → 런타임 에러. 분리 전 var 인벤토리 작성 필요
- shim 단계에서 IDE 자동 import 가 옛 경로를 다시 만들 수 있음 → CI 에 `ruff` 의 `TID` 룰로 차단

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

**3-C. `load_dotenv` 사이드이펙트 제거**
- [services/config.py:15](../../wellbot/services/config.py#L15) 의 모듈 레벨 `load_dotenv` 를 `init_env()` 로 감싸 [wellbot/wellbot.py](../../wellbot/wellbot.py) 엔트리포인트에서 1회 호출

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

- [ ] 1-A 데이터 모델 추출 (`state/chat_models.py`)
- [ ] 1-B `ChatState` mixin 분리
- [ ] 2-A `services/` 도메인 패키지 그룹화
- [ ] 2-B `bedrock_client.py` 분리
- [ ] 3-A `wellbot/paths.py` 신설
- [ ] 3-B `constants.py` → YAML/env 이관
- [ ] 3-C `load_dotenv` 명시 호출
- [ ] 4-A 모델 도메인 별칭
- [ ] 4-B 페이지 함수명 통일 + `pages/__init__.py`
- [ ] 4-C `services/config.py` → `settings.py`
- [ ] 4-D `state/__init__.py` 에 `AdminState` 추가
- [ ] 4-E (옵션) `agent` 도메인 분리
- [ ] 5-A 프롬프트 파일명 ASCII 화
- [ ] 5-B `.gitignore` 점검
- [ ] 5-C `ruff` 도입
- [ ] 5-D (옵션) `scripts/` 패키지화
