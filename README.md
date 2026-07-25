# Wellbot

AWS Bedrock 기반의 모듈형 사내 AI 플랫폼입니다.  
Reflex UI와 FastAPI를 결합해 **멀티모델 채팅**, **개인·팀·공용 Knowledge Base(RAG)**, **보고서 draft·오류 탐지**까지 한 앱에서 제공합니다.

| 영역 | 설명 |
|------|------|
| 채팅 | Bedrock Converse 스트리밍, 첨부·비전, 프롬프트 프리셋 |
| Knowledge Base | 개인 / 팀 / 공용 KB (Bedrock KB + S3) |
| AI 업무 서비스 | 보고서 draft 지원, 보고서 오류(오탈자·일관성) 탐지 |
| 관리 | 사원·부서·토큰 사용량 모니터링 |

**스택:** Python ≥ 3.11 · Reflex · FastAPI · MySQL · AWS Bedrock / S3 · (선택) Upstage Document Parse · Bedrock AgentCore

---

## Architecture

```
wellbot/
├── config/                 # YAML·MD 설정 (모델, 프롬프트, KB, AI 서비스 카탈로그)
├── docs/                   # DDL, nginx 샘플, 설계·감사 문서
├── scripts/                # KB·마이그레이션·운영 유틸
├── tests/                  # pytest
└── wellbot/                # 메인 패키지
    ├── wellbot.py          # Reflex 앱 엔트리포인트
    ├── api/                # FastAPI (업로드·KB·report API) — api_transformer 마운트
    ├── pages/              # 라우트별 페이지
    ├── components/         # UI 컴포넌트
    ├── state/              # Reflex State
    ├── services/           # 비즈니스 로직
    │   ├── chat/           # 대화·도구 실행
    │   ├── knowledgebase/  # 개인·팀 KB
    │   ├── report_maker/   # 보고서 draft
    │   ├── report_checker/ # 보고서 오류 탐지
    │   ├── files/          # 첨부·파싱·스토리지
    │   ├── ai/bedrock/     # Bedrock 클라이언트
    │   ├── auth/           # 인증
    │   └── admin/          # 관리·모니터링
    └── models/             # SQLAlchemy ORM
```

- **UI·세션 상태:** Reflex (`pages` / `components` / `state`)
- **대용량 multipart·전용 HTTP:** FastAPI (`wellbot/api`, `rx.App(api_transformer=...)`)
- **LLM·스토리지·DB:** `services/*` + `config/` + `.env`

---

## Prerequisites

- Python **3.11+** ([`.python-version`](.python-version)), [`uv`](https://github.com/astral-sh/uv)
- MySQL (스키마: [`docs/ddl.sql`](docs/ddl.sql))
- AWS 자격증명 — Bedrock, S3, (KB 사용 시) Lambda / IAM Role / S3 Vectors
- (선택) Upstage API — `FILE_PARSER_MODE`가 `upstage` / `hybrid`일 때
- (선택) `REPORT_MAKER_MEMORY_ID` — AgentCore 스타일 메모리 (미설정 시 S3 폴백)

---

## Quick Start

```bash
# 1) 의존성
uv sync

# 2) 환경변수
cp .env.example .env
# DB_URL, AWS_REGION, S3_BUCKET_NAME, JWT_SECRET 등 필수값 입력

# 3) DB 스키마 적용
# MySQL에 docs/ddl.sql 실행

# 4) 개발 서버
reflex run
```

기본 접속: 브라우저에서 Reflex가 안내하는 로컬 URL (프론트 `:3000`, 백엔드 `:8000` 근처).

회원가입(`/register`) 후 로그인하거나, 관리자 페이지는 `.env`의 `ADMIN_PASSWORD`로 접근합니다.

### 주요 환경변수

상세 설명·전체 목록은 [`.env.example`](.env.example)을 참고하세요.

| 변수 | 용도 |
|------|------|
| `DB_URL` | MySQL SQLAlchemy URL (`mysql+pymysql://...`) |
| `AWS_REGION` | Bedrock·S3 기본 리전 |
| `APP_ENV` | `dev` / `prd` — KB·S3 경로 네임스페이스 분리 |
| `JWT_SECRET` | 세션 JWT 서명 키 (운영에서는 반드시 변경) |
| `ADMIN_PASSWORD` | `/admin` 접속 비밀번호 |
| `S3_BUCKET_NAME` | 첨부·KB 파일 버킷 |
| `S3_KEY_PREFIX` | S3 키 prefix (선택) |
| `KB_*` | 공용/개인 KB 인프라 (Intermediate·Vector 버킷, Lambda, Role, KB ID) |
| `UPSTAGE_API_KEY` | Document Parse (선택) |
| `REPORT_MAKER_MEMORY_ID` | report_maker AgentCore 메모리 (선택) |
| `LOG_ENV` / `LOG_LEVEL` | 로깅 프리셋·레벨 |

---

## Configuration map

| 관심사 | 위치 |
|--------|------|
| 환경변수 | `.env` ← [`.env.example`](.env.example) |
| LLM 모델 목록 | [`config/models.yaml`](config/models.yaml) |
| 채팅 프롬프트 | [`config/prompts.yaml`](config/prompts.yaml), [`config/prompts/`](config/prompts/) |
| KB 동작 옵션 | [`config/knowBase.yaml`](config/knowBase.yaml) + `KB_*` env |
| AI 서비스 카드 | [`config/ai_services.yaml`](config/ai_services.yaml) |
| report_maker | [`wellbot/services/report_maker/report_maker.yaml`](wellbot/services/report_maker/report_maker.yaml) |
| report_checker | [`wellbot/services/report_checker/report_checker.yaml`](wellbot/services/report_checker/report_checker.yaml) |
| 운영 상수 (업로드 한도 등) | [`wellbot/constants.py`](wellbot/constants.py) |

새 AI 업무 서비스를 추가할 때: `config/ai_services.yaml`에 카드 등록 → `pages`·라우트·필요 시 `api`/`services` 구현.

---

## Routes

| 경로 | 설명 |
|------|------|
| `/` | 메인 채팅 |
| `/login`, `/register` | 로그인·회원가입 |
| `/admin` | 관리·모니터링 |
| `/ai-services` | AI 업무 특화 서비스 목록 |
| `/ai-services/report-generator` | 보고서 draft 지원 |
| `/ai-services/report-generator/style` | 작성 가이드(스타일) 편집 |
| `/ai-services/report-checker` | 보고서 오류 탐지 (PDF) |

---

## Features (요약)

### 채팅
- `config/models.yaml`의 Bedrock 모델 선택, thinking·비전·문서 첨부
- 대화 목록·검색·제목 자동 생성
- 첨부 파일 파싱 (`local` / `upstage` / `hybrid` — `constants.FILE_PARSER_MODE`)

### Knowledge Base
- **개인·팀 KB:** 사용자/부서 단위 Bedrock KB 생성·인제스트
- **공용 KB:** `KB_ID` / `knowBase.yaml`의 shared 설정
- 업로드·다운로드 API: `wellbot/api/kb_upload.py`, `kb_download.py`
- 문서 수 상한 등: `constants.KB_MAX_DOCS`

### 보고서 draft (`report_maker`)
- 문체·템플릿 학습 후 아웃라인·초안 지원
- AgentCore 메모리 또는 S3 스타일 프로파일 폴백
- 설정·한도: `report_maker.yaml`

### 보고서 오류 탐지 (`report_checker`)
- PDF 오탈자·표기·일관성 등 파이프라인 검사
- 청크·재시도·업로드 한도: `report_checker.yaml`

---

## Development

```bash
# 단위·모듈 테스트
uv run pytest

# 특정 영역만
uv run pytest tests/chat
uv run pytest tests/report_maker
```

운영·마이그레이션 유틸은 [`scripts/`](scripts/)에 있습니다 (예: `shared_kb_manager.py`, `cleanup_personal_kb.py`, `transform_lambda.py`).  
사용법은 각 스크립트 docstring을 참고하세요.

코드 주석·docstring 규칙은 [`docs/refactor-structure/style-guide.md`](docs/refactor-structure/style-guide.md)를 따릅니다.

---

## Deployment notes

- nginx 리버스 프록시 샘플: [`docs/nginx-reflex.conf`](docs/nginx-reflex.conf)  
  - UI → `:3000`, WebSocket·API → `:8000`  
  - 긴 LLM 스트리밍을 위해 `proxy_read_timeout` 확대, `client_max_body_size` 참고
- `APP_ENV=dev`이면 KB·S3 경로에 `-dev` 네임스페이스를 두어 운영과 충돌을 피합니다. 운영은 `prd`(또는 접미사 없는 설정)를 사용합니다.
- 시크릿(`.env`, 자격증명)은 커밋하지 마세요.

---

## Docs index

| 문서 | 내용 |
|------|------|
| [`docs/ddl.sql`](docs/ddl.sql) | DB 스키마 |
| [`docs/nginx-reflex.conf`](docs/nginx-reflex.conf) | nginx 프록시 샘플 |

---

## License / 내부 사용

사내 PoC·업무용 프로젝트입니다. 배포·계정·AWS 리소스 정책은 팀 가이드를 따르세요.
