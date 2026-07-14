# KB 업로드 안정화 작업 기록 (task)

> 이 문서 = **수행한 작업과 그 이유**(실행 기록). 같은 폴더의 `report.md` = 잠재 버그 위험도 랭킹(후보 목록). 둘은 짝.
> 범위: 개인/팀 KB 업로드·삭제 파이프라인 + 관측성. (dev/prd 리소스 네임스페이스 분리는 별개 작업이라 제외.)

---

## 작업 1 — 업로드 파이프라인을 2축으로 분리 (504 해결 + 다듬기)

### 1-1. 문제
개인/팀 KB에 PDF 여러 개를 한 번에 올리면 **504**로 실패. 원인은 업로드 HTTP 엔드포인트
(`/api/upload_kb_files`)가 **요청 안에서 PDF Upstage 변환을 동기로 ×N회** 수행 → Nginx/ALB
프록시 타임아웃(≈60s) 초과. 백엔드는 계속 돌아 변환본이 S3엔 남지만 브라우저는 504를 받아
**ingestion이 트리거되지 않고 S3 고아**가 남음.

### 1-2. 왜 이 방향인가
HTTP 타임아웃을 키우는 건 임시방편. 근본은 "무거운 변환이 HTTP 요청 경로 안에 있는 것".
변환·색인을 요청 밖(이미 ingestion이 도는 websocket 이벤트)으로 옮기면 파일 수·Upstage 지연·
프록시 설정과 **완전히 무관**해진다.

### 1-3. 변경 (두 축)
- **축 1 (HTTP, 빠름)**: 엔드포인트는 **원본 바이트를 `staging/`(=`raw/`의 형제, 색인 제외 위치)에만
  적재하고 즉시 반환**. 변환·색인 안 함. — `wellbot/api/kb_upload.py` (`stage_raw_files` 호출).
- **축 2 (백그라운드)**: `staging/` 원본을 내려받아 **변환 → `raw/`/`originals/` 적재 → ingestion →
  poll**. — `ChatState.on_upload_complete`(websocket 이벤트).

신규/재사용 (DRY):
- `wellbot/services/knowledgebase/kb_utils.py`: `get_staging_prefix`, `stage_raw_files`,
  `process_staged_files`, `_delete_keys_quietly` 추가. 검증 로직은 `_validate_upload_files`로
  **추출해 `upload_files_to_kb`와 공유**(중복 제거). **변환 로직은 `upload_files_to_kb`를 그대로 재사용**.
- **누적 문서 상한은 staging 적재 *전* 엔드포인트에서 선검증**(초과 시 S3에 안 올리고 즉시 거부) +
  `upload_files_to_kb` 락 하 최종 재검증(동시 업로드 race 방어).

### 1-4. 왜: 고아 0 보장
- 변환 성공 → staging 삭제 / 변환 실패 → `with_rollback`이 raw·originals 정리 + staging 정리 /
  ingestion 실패 → 기존 롤백. 어느 경로든 잔여 파일 없음.

### 1-5. PDF 변환 병렬화
- **왜**: 변환은 직렬이라 PDF N개 = Upstage 호출 N번 합산 지연.
- **무엇**: `_convert_pdfs_parallel`(`ThreadPoolExecutor`, `_PDF_CONVERT_MAX_WORKERS=3`)로 락·S3 쓰기
  밖에서 PDF만 병렬 변환. `upload_files_to_kb`은 사전계산 결과(`pdf_md`) 소비. 변환 실패분은 원본
  PDF 색인(Lambda parse_pdf)으로 폴백 보존. pptx/tabular는 로컬이라 직렬 유지.
- **왜 PDF만**: 개인/팀 경로의 유일한 네트워크 hotspot이 PDF Upstage. (ingestion은 DS당 단일 job이라
  병렬 불가·불필요 — Bedrock이 동시 job 거부.)

### 1-6. 축2 오케스트레이션 서비스 추출
- **왜**: 변환+KB확보+ingest+poll+롤백이 `on_upload_complete`에 길게 인라인 → 가독성↓, 재사용↓.
- **무엇**: `wellbot/services/knowledgebase/kb_ingest_service.py` 신설 — `ingest_staged(scope, emp_no,
  names) -> IngestOutcome`(scope=personal/team 파라미터화)가 축2를 담당. `on_upload_complete`는
  **결과를 UI 상태로 매핑만** 하는 얇은 핸들러로 축소 + `_mark_kb_exists` 헬퍼로 team/personal
  중복 제거. → 향후 공용 KB(admin) fire-and-forget 업로드가 이 서비스를 재사용 가능.

---

## 작업 2 — UI 먹통/디싱크 잠재 버그 수정 (감사 기반)

`report.md`의 위험 랭킹 중 KB 전용·동작 보존 항목을 적용. (L2=첨부 도메인 제외, M2=근거부족 보류,
M4=인프라 메모.)

- **on_upload_complete → `@rx.event(background=True)`** (+ `async with self:` 래핑):
  **왜** — 비-background 핸들러가 긴 변환·ingest 동안 이벤트 채널을 점유해 토글/패널이 먹통이 되고,
  긴 침묵으로 websocket이 idle-drop되어 새로고침 전까지 복구 불가. background로 분리해 처리 중에도
  UI 응답 유지. (`wellbot/state/chat_state.py`)
- **H1: `confirm_kb_delete` → background** (+ M3 가드): 문서 삭제도 인덱스 정리 ingestion poll을
  비-background로 돌려 **동일 먹통 부류**였음. background 전환 + `finally`에 `processing` 고착 가드
  (정상 종료 시 no-op, 비정상 종료 시 error로 떨궈 UI 잠김 방지).
- **M1: 업로드 디싱크 가드**: 패널(`pending_files`, 서버)과 브라우저 선택(`window._kbSelectedFiles`)이
  어긋나 "버튼은 보이는데 No files selected"가 발생. **무엇** — (a) `uploadKbFilesToApi` 필터에 파일명
  **NFC 정규화**(macOS NFD 등 코드포인트 불일치 방지), (b) 빈 선택 시 stale JS 선택 정리 + "다시
  선택" 안내 메시지(관리자 문의 대신). (`upload_script.py`, `chat_state._user_friendly_error`)

> 근본(왜 어긋났나)은 JS전역+폴링Promise+콜백 의존 구조라는 게 공통 뿌리(`report.md` 교차관찰).
> 이번엔 사용자 영향(먹통·교착·오해 메시지)을 안전하게 제거하는 가드 위주로 처리.

---

## 작업 3 — 로그 강화 (관측성)

### 3-1. 왜
실패 시 화면엔 "관리자에게 문의" 같은 일반 메시지를 보여주되, 디버깅하려면 **백엔드에 정밀 사유**가
남아야 한다. 확인 결과 가장 큰 사각지대는 **브라우저에서만 발생하는 실패(웹/Reflex/websocket/JS)** —
서버 로그가 원천적으로 못 보는 영역. (Bedrock/Upstage 실패는 이미 잘 로깅됨.)

### 3-2. 확인 (기존 로깅은 양호)
- **Bedrock ingestion 실패**: `poll_ingestion_status`가 `failureReasons` 추출 → `log.warning` + 상태에
  `"FAILED: 사유"`, `ingest_staged`가 `log.error("KB ingestion 실패: %s", status)`. **세부 사유까지 구조화
  로그에 적재됨.**
- **Upstage**: `UpstageParser.parse`가 호출/완료(+지연ms)/HTTP오류/호출실패를 로깅, `timeout=300s`.
- **로깅 인프라**: `wellbot` 네임스페이스 로거 → dev=콘솔(`LOG_TO_FILE` 기본 false) / prod=`wellbot.log`
  (JSON, Rotating). `ContextFilter`가 emp_no/conversation_id/message_id/request_id 주입. `LOG_LEVEL=INFO`라
  warning/error 보임. uncaught는 `wellbot.uncaught` 안전망.

### 3-3. 변경 — 클라이언트 오류 비콘 (사각지대 메움)
- **무엇**: 브라우저의 `window.onerror` / `unhandledrejection`을 잡아 `/api/client_log`로 전송하면,
  백엔드가 구조화 로그로 남김.
  - `wellbot/api/client_log.py` (신규): `POST /api/client_log` — `log.warning("client error [kind] msg",
    extra={client_kind/detail/url})`. **실패 비콘 전용(성공 로그 없음).**
  - `wellbot/state/chat_helpers/upload_script.py`: `CLIENT_LOG_SCRIPT` — 핸들러 설치 + 비콘 전송
    (동일 메시지 5초 throttle, 비콘 자체 오류는 삼켜 재귀 방지).
  - `wellbot/api/app.py`: 라우터 등록. `wellbot/pages/index.py`: 스크립트 등록.
- **왜 이렇게(재사용)**: 기존 `_bind_log_context` **HTTP 미들웨어**가 모든 `api_app` 요청에 `request_id`를
  자동 부여("하이재킹") → 비콘 엔드포인트도 그 위에 얹으면 **request_id 자동 + 쿠키로 emp_no 보강 +
  동일 로거/파일/포맷**으로 흘러감. 추가 로깅 인프라 0. 백엔드 base는 기존 `_wellbotBackendBase` 재사용.
- **한계(솔직히)**: 순수 websocket idle-drop은 JS error를 안 던질 수 있어 직접 신호로는 부분적. 예외·
  rejection 위주로 대부분의 브라우저 실패를 포착. ws 전용 신호는 후속(소켓 훅/online·offline) 여지.

---

## 변경 파일 요약
| 파일 | 작업 |
|---|---|
| `wellbot/api/kb_upload.py` | 엔드포인트 staging 적재화 (1) |
| `wellbot/services/knowledgebase/kb_utils.py` | staging 헬퍼·검증 추출·PDF 병렬화 (1) |
| `wellbot/services/knowledgebase/kb_ingest_service.py` (신규) | 축2 오케스트레이션 (1) |
| `wellbot/state/chat_state.py` | on_upload_complete 슬림+background, confirm_kb_delete background+가드, _mark_kb_exists, friendly 메시지 (1·2) |
| `wellbot/state/chat_helpers/upload_script.py` | NFC 정규화, CLIENT_LOG_SCRIPT (2·3) |
| `wellbot/api/client_log.py` (신규) | 클라이언트 오류 수집 (3) |
| `wellbot/api/app.py` | client_log 라우터 등록 (3) |
| `wellbot/pages/index.py` | 비콘 스크립트 등록 (3) |

## 검증
- 전 파일 `py_compile` 통과.
- 스모크 테스트 권장: 다중 PDF 업로드(504 없음·병렬 변환), 문서 삭제 중 패널 응답(H1), 디싱크 시
  재선택 안내(M1), 콘솔에서 `throw`/`Promise.reject` → 서버 로그에 `client error` 적재(3).
- 미검증 포인트: background 이벤트에서 `yield ChatState.load_kb_docs`(삭제 후 목록 새로고침) 정상 동작.
