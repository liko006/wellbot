# 프로젝트 구조 진단 및 개선 방향

> 작성일: 2026-05-29
> 대상 브랜치: `refactor/project-structure`

## 1. 현재 폴더/파일 구조

```
wellbot/
├── .env.example, .gitignore, .python-version
├── README.md
├── pyproject.toml, uv.lock, rxconfig.py
├── config/                      # YAML/MD 설정
│   ├── agents.yaml, greetings.yaml, models.yaml, prompts.yaml, notice.md
│   └── prompts/                 # 한글 파일명 프롬프트 (.md)
├── docs/
│   ├── ddl.sql
│   └── nginx-reflex.conf
├── scripts/
│   └── verify_attachment_index.py
├── tests/
└── wellbot/                     # 메인 패키지
    ├── wellbot.py               # 엔트리포인트
    ├── constants.py             # 전역 상수
    ├── styles.py
    ├── api/                     # FastAPI sub-app (upload, download)
    ├── components/              # Reflex UI
    │   ├── admin/, chat/, sidebar/
    │   ├── icons.py, layout.py, search_modal.py
    ├── models/                  # SQLAlchemy ORM (테이블당 1파일)
    ├── pages/                   # index/login/register/admin
    ├── services/                # 비즈니스 로직 (14 파일)
    └── state/                   # Reflex State
```

### 라인 카운트 핫스팟

| 파일 | 라인 수 |
| --- | ---: |
| `wellbot/state/chat_state.py` | 1,261 |
| `wellbot/services/bedrock_client.py` | 607 |
| `wellbot/services/attachment_service.py` | 575 |
| `wellbot/components/sidebar/sidebar.py` | 544 |
| `wellbot/services/file_parser.py` | 537 |
| `wellbot/services/embedding_service.py` | 506 |
| `wellbot/components/chat/input_bar.py` | 452 |

---

## 2. 진단 및 개선 방향

### 2.1 거대한 단일 파일 분리 (최우선)

한 파일에 다중 책임이 누적되어 가독성·테스트 가능성이 떨어진다.

- **[wellbot/state/chat_state.py](../../wellbot/state/chat_state.py) (1,261줄)**
  메시지 송수신·스트리밍·대화 CRUD·첨부 연동·검색·제목 생성이 한 State 클래스에 모여 있다.
  책임별 mixin 또는 sub-state 로 분리 권장:
  - `chat_state/messaging.py` — 전송·스트리밍
  - `chat_state/conversation.py` — 목록·제목
  - `chat_state/attachment.py` — 업로드 연동
  - `chat_state/search.py` — 검색

- **[wellbot/services/bedrock_client.py](../../wellbot/services/bedrock_client.py) (607줄)**
  Converse 호출, tool-use 루프, 제목 생성, 이미지 처리 등이 섞여 있다.
  → `bedrock/converse.py`, `bedrock/tool_loop.py`, `bedrock/title.py` 로 분리.

- **[wellbot/services/attachment_service.py](../../wellbot/services/attachment_service.py) (575줄) / [file_parser.py](../../wellbot/services/file_parser.py) (537줄)**
  파서 모드별(`local` / `upstage` / `hybrid`) 어댑터 분리 검토. 현재 `FILE_PARSER_MODE` 분기가 한 파일에 집중되어 있을 가능성이 높다.

- **[wellbot/components/sidebar/sidebar.py](../../wellbot/components/sidebar/sidebar.py) (544줄)**
  헤더 / 검색 / 대화목록 / 유저 메뉴 등 섹션별 함수 분리.

### 2.2 설정·환경 분리

- `wellbot/constants.py` 에 **운영 파라미터**(`TITLE_MODEL_ID`, `EMBEDDING_MODEL_ID`, `FILE_PARSER_MODE`, top_k 등)와 **불변 상수**(`KST`, 확장자 집합)가 섞여 있다.
  → 운영 가변값은 [config/models.yaml](../../config/models.yaml) 또는 `.env` 로 이관, `constants.py` 는 진짜 상수만 유지.

- [config/prompts/](../../config/prompts/) 의 **한글 파일명** (`구조적.md`, `균형적.md`, `미적용.md`, `일반.md`, `정확성.md`) 은 OS·CI·도커 빌드 환경에 따라 인코딩 문제가 발생할 수 있다.
  → ASCII 슬러그(`structural.md`, `balanced.md`, …) 로 변경하고, 한국어 표시명은 `prompts.yaml` 의 메타데이터에서 관리.

### 2.3 패키지·네이밍

- 엔트리포인트 `wellbot/wellbot.py` 는 Reflex 관례(`app_name == 패키지명`)에 맞으므로 **현행 유지**.
  단, import 블록이 길어 가독성이 떨어진다. → [wellbot/state/__init__.py](../../wellbot/state/__init__.py) 등에서 State 클래스·페이지를 re-export 하여 엔트리포인트 import 를 축약.
  > 참고: `wellbot/app.py` 로 이름을 옮기는 대안은 `wellbot/api/app.py` (FastAPI sub-app `api_app`) 와 모듈명·객체명이 유사해져 혼란을 키우므로 채택하지 않는다.

- `models/` 의 SI 표준 약어 접미사(`AgntM`, `EmpM`, `ChtbMsgD`, `CrtfToknN` …) 가 코드 호출부까지 그대로 노출된다.
  → DB 객체명은 유지하되, 도메인 별칭을 추가해 호출부 가독성 확보.
  ```python
  # wellbot/models/__init__.py
  Agent = AgntM
  Employee = EmpM
  ChatMessage = ChtbMsgD
  ```

- [wellbot/services/](../../wellbot/services/) 가 14개 파일로 평탄(flat) 하다. 도메인별 그룹화 제안:
  ```
  services/
    auth/      (auth_service.py)
    chat/      (chat_service.py, response_filter.py, tool_executor.py)
    ai/        (bedrock_client.py, embedding_service.py)
    files/     (attachment_service.py, file_parser.py, chunker.py, storage_service.py)
    admin/     (admin_service.py)
    core/      (database.py, config.py)
  ```

### 2.4 그 외

- `.states/`, `.web/`, `__pycache__/` 가 `.gitignore` 에 포함되어 있는지 점검.
- [rxconfig.py](../../rxconfig.py) (189 bytes) — 환경(dev/prod) 분기가 필요해질 때 `config/` 와 통합 고려.
- `pyproject.toml` 에 lint/format 설정 부재. 현 규모에서는 `ruff` 도입 권장 (포맷·린트 일원화).
- [wellbot/state/__init__.py](../../wellbot/state/__init__.py) 는 이미 `ChatState/AuthState/UIState` 를 re-export 하지만, **`AdminState` 가 누락**되어 있어 `wellbot.py` 가 `from wellbot.state.admin_state import AdminState` 를 직접 호출한다. `AdminState` 도 동일하게 노출해 일관성 확보.
- `pages/` 에는 `__init__.py` re-export 가 없어 엔트리포인트가 페이지를 한 줄씩 import 한다. `pages/__init__.py` 에서 모아 노출하면 `wellbot.py` 가 3~4줄로 축약된다.

### 2.5 혼용·중복 네이밍 리스크 (추가 확인)

같은 단어가 서로 다른 책임의 객체를 가리키며 import 시 혼란을 유발한다.

- **`app` / `api_app`**
  - `wellbot/wellbot.py::app` — Reflex `rx.App`
  - `wellbot/api/app.py::api_app` — FastAPI sub-app
  → 현행 유지가 안전. (앞 절에서 다룬 결정)

- **`config` 라는 이름의 다중 의미**
  - 최상위 `config/` 디렉터리 — YAML/MD 설정 파일
  - `wellbot/services/config.py` — `AppConfig` 로더 (`get_config()`)
  - `rxconfig.py` — Reflex 빌드 설정 (`rx.Config`)
  세 곳 모두 "설정"이지만 역할이 다르다. `services/config.py` 를 `services/app_config.py` 또는 `services/settings.py` 로 개명하면 호출부에서 `from wellbot.services.config import get_config` 같은 모호한 import 가 줄어든다.

- **`admin` 의 3중 사용**
  - `wellbot/pages/admin.py::admin` — 페이지 함수
  - `wellbot/components/admin/` — 어드민 UI 컴포넌트 패키지
  - `wellbot/state/admin_state.py::AdminState` — 어드민 State
  엔트리포인트에서 `from wellbot.pages.admin import admin` (모듈명과 함수명이 동일) 패턴이 반복된다. 페이지 함수명을 `admin_page`, `index_page` 등으로 통일하면 import 가독성이 개선된다. (Reflex `add_page` 는 함수 자체를 받으므로 변경 부담은 낮다.)

- **`Base`**
  - `wellbot/models/base.py::Base` — SQLAlchemy `DeclarativeBase`
  - Reflex 의 `rx.Base` (Pydantic 기반) — `chat_state.py` 안의 `Message`, `Conversation` 등 클래스가 상속할 가능성
  같은 파일에 둘이 import 되면 충돌하므로, ORM 쪽은 `from wellbot.models.base import Base as ORMBase` 같은 별칭 컨벤션을 문서화해 두는 게 안전.

- **`Conversation` / `Message` / `AttachmentInfo`**
  - `chat_state.py` 내부 클래스가 `from wellbot.state.chat_state import ChatState, Conversation` 식으로 여러 파일에서 직접 import 되고 있다 ([search_modal.py:8](../../wellbot/components/search_modal.py#L8), [conversation_list.py:8](../../wellbot/components/sidebar/conversation_list.py#L8), [message_bubble.py:10](../../wellbot/components/chat/message_bubble.py#L10)).
  - chat_state 분리 시 이들 데이터 클래스는 **`wellbot/state/chat_models.py`** (또는 `state/types.py`) 같은 별도 모듈로 빼야 순환 import 를 막을 수 있다.

- **DB 모델 약어 vs 도메인명**
  - `AgntM` (모델) ↔ `agent_modes` (config) ↔ `agent_tab` (컴포넌트) — "agent" 라는 단어가 ORM/설정/UI에서 모두 등장하지만 가리키는 도메인이 다르다 (DB 에이전트 마스터 vs 채팅 에이전트 모드). 한쪽을 `assistant_mode` 등으로 개명하거나, 코드 주석/타입에 의미를 명시할 것.

### 2.6 추가로 확인된 사항

- **모듈 레벨 사이드이펙트** — [services/config.py:15](../../wellbot/services/config.py#L15) 의 `load_dotenv(...)` 가 import 시점에 실행된다. 테스트나 다른 엔트리포인트에서 환경변수 주입 순서가 꼬일 수 있으므로, 명시적 `init_env()` 함수로 분리하거나 엔트리포인트 한 곳에서만 호출하도록 정리.
- **하드코딩된 상대 경로** — [services/config.py:106-108](../../wellbot/services/config.py#L106-L108), [auth_service](../../wellbot/services/auth_service.py) 등에서 `Path(__file__).resolve().parent.parent.parent / "config"` 패턴이 반복된다. `wellbot/paths.py` 같은 단일 모듈에 `PROJECT_ROOT`, `CONFIG_DIR` 을 정의하고 재사용 권장.
- **`__pycache__` / `.web` / `.states` 가 git 추적 대상** — `git status` 는 clean 이지만, 트리에 노출된 것으로 보아 `.gitignore` 점검 필요(특히 `.states/`).
- **`pages/` 와 `components/` 경계 모호** — [pages/admin.py](../../wellbot/pages/admin.py) 가 `components/admin/*_tab.py` 를 직접 조합한다. 향후 페이지가 늘면 `components/admin/__init__.py` 에서 `admin_panel()` 같은 상위 컴포넌트 하나만 노출하도록 모으는 방향이 좋다.
- **`scripts/` 와 패키지 간 의존성** — `scripts/verify_attachment_index.py` 는 패키지 코드를 import 할 가능성이 큰데, 실행 시 PYTHONPATH 설정이 명시돼 있지 않다. `python -m wellbot.scripts.verify_attachment_index` 형태로 패키지화하면 안전.
- **`__init__.py` 가 모두 빈 파일인 경우 다수** — `components/__init__.py`, `components/chat/__init__.py`, `components/sidebar/__init__.py`, `pages/__init__.py`, `services/__init__.py` 등이 비어 있다. 직접 import 경로가 길어지는 원인. 패키지 진입점에서 공개 API 를 정리하는 차원에서 re-export 추가 권장.

---

## 3. 우선순위 로드맵

1. **`chat_state.py` 분리** + **`state/chat_models.py` 추출** — 유지보수성 즉시 개선, 후속 작업의 전제 조건.
2. **`services/` 도메인 그룹화** + **`bedrock_client.py` 분리** + **`services/config.py` → `settings.py` 개명** — 비즈니스 레이어 정리 및 네이밍 혼용 해소.
3. **`constants.py` ↔ `config/*.yaml` 재배치** + **`wellbot/paths.py` 신설** — 운영 가변값/불변 상수/경로 상수 분리.
4. **`ruff` 도입**, **모델 도메인 별칭 추가**, **페이지 함수명 `*_page` 통일**, **`state/__init__.py` 에 `AdminState` 추가** — 코드 스타일 및 호출부 가독성 일원화.
5. **프롬프트 파일명 ASCII 화** + **`.gitignore` 점검** — 운영 환경 호환성 확보.
