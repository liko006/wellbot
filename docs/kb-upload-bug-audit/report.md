# KB 업로드/패널 UI 먹통·디싱크 — 잠재 버그 감사 (코드 변경 없음)

> 목적: 코드 리뷰만으로 안 보이는 **런타임/타이밍/상태동기화 버그** 후보를 위험도 랭킹으로 정리(인계용).
> 범위: `wellbot/state/chat_state.py`, `wellbot/state/chat_helpers/upload_script.py`, KB 업로드/삭제/패널 흐름.
> 작성 시점 기준 코드. 라인 번호는 변동될 수 있으니 함수명으로 교차 확인 권장.
> **실제 수행한 작업·이유는 짝 문서 [`task.md`](./task.md) 참조** (이 문서=후보 랭킹, task.md=실행 기록).

## 버그 부류 (왜 정적 리뷰로 안 잡히나)
- **A. 이벤트 채널 점유(먹통)**: `await`/`run_in_executor`를 하는 **비-background** 이벤트 핸들러는 실행 동안 그 클라이언트의 이벤트 처리를 점유 → 토글/패널/버튼이 죽은 듯 보임. 긴 침묵은 프록시 **websocket idle 타임아웃**으로 연결까지 끊어 새로고침 전까지 복구 불가.
- **B. 이중 상태(two-source-of-truth) 디싱크**: 패널/버튼은 **서버 state**(`pending_files`)로 그려지는데 실제 업로드는 **브라우저 JS 전역**(`window._kbSelectedFiles`)을 읽음 → 둘이 어긋나면 "버튼은 보이는데 No files selected".
- **C. sticky 상태**: `ingestion_status`/`kb_delete_status`가 종료 직전 죽으면 `processing`에 고착 → UI가 계속 잠김(새로고침/대화전환 전까지).

---

## 진행 상태 (이번 작업)
- **H1 — 적용됨**: `confirm_kb_delete` → `@rx.event(background=True)` + `async with self:` 래핑(동작 보존). M3 가드 포함.
- **M3 — 적용됨(삭제 경로)**: `confirm_kb_delete` `finally`에 `processing` 고착 가드 추가. (업로드 경로는 `on_upload_complete`가 이미 종료 상태를 항상 찍음.)
- **M1 — 일부 적용(방어적)**: `uploadKbFilesToApi` 필터에 NFC 정규화 추가(파일명 코드포인트 불일치 방지). 근본 타이밍/clear 원인은 기존 가드(빈 선택 시 정리+재선택 안내)로 완화 — 재현 데이터 확보 시 확정.
- **M2 — 보류**: 근거(취소 미감지 재현) 부족. 30초 타임아웃 단축 등은 동작 변경이라 보류.
- **M4 — 메모만(코드 변경 없음)**: 프록시 websocket idle 타임아웃은 **전역 인프라 설정**이라 인프라 담당과 별도 조율. (heartbeat 코드화는 후속.)
- **L2 — 제외**: 첨부파일(비-KB) 도메인이라 이번 범위 밖.
- 검증: `py_compile` 통과. **스모크 테스트 필요** — 문서 삭제(개인/팀) → 인덱스 정리 중 패널/토글 응답 + 완료 후 목록 새로고침(`yield ChatState.load_kb_docs`가 background에서 정상 트리거되는지).

---

## HIGH

### H1. `confirm_kb_delete` 가 비-background인데 ingestion poll 까지 수행 → 삭제 시 UI 먹통 + ws-drop
- **위치**: `chat_state.py` `confirm_kb_delete()` (def ~647), `run_in_executor`로 `poll_ingestion_status` 호출(~747).
- **부류**: A (그리고 C).
- **증상**: 문서 삭제 확정 후 인덱스 정리 ingestion이 도는 동안(수십 초~분) 토글/패널/다른 버튼 무반응. 길면 ws 끊겨 새로고침 필요.
- **근거**: 우리가 방금 고친 `on_upload_complete`와 **동일 구조**인데 이쪽은 아직 `@rx.event(background=True)`가 아님. `except Exception`만 있고 `finally` 없음 → 중간에 죽으면 `kb_delete_status`가 `processing`에 고착될 수 있음(C).
- **재현**: 문서 여러 개 선택 삭제 → 인덱스 정리 도는 동안 패널 토글/대화 전환 시도.
- **수정 방향(낮은 위험, 동작 보존)**: `on_upload_complete`와 동일하게 `@rx.event(background=True)` + 상태 접근을 `async with self:`로 래핑. 무거운 `run_in_executor`는 락 밖. (참고 구현: 이번에 수정한 `on_upload_complete`.)
- **회귀 위험**: 낮음~중간. 같은 패턴을 그대로 적용. 삭제 흐름 스모크 테스트 필요(개인/팀 삭제 → 인덱스 반영 → 목록 새로고침).

---

## MEDIUM

### M1. 이중 상태 디싱크: `pending_files`(서버) vs `window._kbSelectedFiles`(브라우저)
- **위치**: `chat_state.py` `open_file_picker`(~1002)·`add_pending_files_from_js`(~1029)·`confirm_upload_via_api`(~1091); `upload_script.py` `uploadKbFilesToApi`(~190, `allowedNames` 필터 ~194).
- **부류**: B.
- **증상**: 패널엔 파일이 보이는데 업로드 시 "No files selected"(현재는 가드로 재선택 안내). "업로드 3번 클릭 후 됨" 현상.
- **근거**: 버튼 노출=서버 `pending_files`, 업로드 대상=브라우저 `_kbSelectedFiles`. 둘의 동기화가 200ms 폴링 Promise + JS 전역에 의존해 레이스/잔여에 취약. **추가 의심**: 한글 파일명 **NFC/NFD 정규화 불일치**로 `allowedNames.indexOf(f.name)` 매칭 실패(맥OS 업로드 등) → 파일은 있는데 필터 결과 0.
- **현재 완화(적용됨)**: "No files selected" 시 `_kbSelectedFiles`/`_kbPendingMeta` 비우고(`upload_script.py`) 패널도 비워 재선택 유도 + 친화 메시지. → 교착은 풀리나 **근본(왜 어긋났나)은 미해결**.
- **수정 방향**: (1) 필터 비교 전 양쪽 파일명을 **NFC 정규화**(`name.normalize('NFC')`) → 이름 불일치 제거. (2) 더 근본적으로 `_kbPendingMeta` 즉시-resolve 지름길 제거 + 선택/제거/업로드 시 두 저장소 하드 싱크. (3) 확정 직전 JS에서 실제 매칭 파일 수를 재검증해 0이면 명시적 재선택 요청.
- **확정 방법**: 실패 직전 콘솔에서 `window._kbSelectedFiles.map(f=>f.name)` 와 패널 파일명을 비교(정규화 형태 차이 확인).
- **회귀 위험**: (1) NFC 정규화는 낮음. (2)는 picker 흐름을 건드려 중간 위험.

### M2. `open_file_picker`의 30초 폴링 Promise가 이벤트 큐를 막을 수 있음
- **위치**: `chat_state.py` `open_file_picker`(~1002-1024).
- **부류**: A (call_script Promise pending).
- **증상**: 파일 선택 다이얼로그를 띄운 뒤, 브라우저 `cancel` 이벤트가 안 잡히면 Promise가 최대 30초 pending → 그동안 UI가 멈춘 것처럼 보임.
- **근거**: 코드 주석이 이미 이 함정을 명시("Promise가 pending이면 다른 이벤트들이 큐에 쌓이고 UI가 멈춘 것처럼 보이는 문제"). `cancel` 이벤트는 브라우저별 신뢰도 차이.
- **수정 방향**: 타임아웃 단축(예 30s→8~10s), `window.focus` 복귀 감지 등 취소 휴리스틱 보강, 또는 picker를 call_script 콜백 의존에서 분리.
- **회귀 위험**: 낮음.

### M3. sticky `processing`/`uploading` 상태 — 자가 복구 없음
- **위치**: 리셋 지점 `_reset_kb_panels`(~535)·`close_panel`(~988)은 **`ready`/`error`만** `idle`로 되돌림(`processing`는 의도적으로 유지). `confirm_kb_delete`는 `finally` 없음.
- **부류**: C.
- **증상**: 핸들러가 종료 상태를 못 찍고 죽으면(ws drop, 서버 재시작, background task 취소) 상태가 `processing`에 고착 → 업로드/삭제 UI가 계속 잠김. 새로고침/대화전환 전까지 복구 안 됨.
- **수정 방향**: (1) 모든 처리 핸들러가 `finally`로 반드시 종료 상태를 찍게(H1 수정 시 포함). (2) `processing`도 일정 시간 후 자가 복구하거나 사용자 "취소/초기화" 버튼 제공. (3) on_load 시 비정상 `processing` 감지→idle.
- **회귀 위험**: 낮음(가드 추가 성격).

### M4. 긴 서버 작업 중 websocket idle 끊김
- **위치**: `on_upload_complete`(background이나 단일 `run_in_executor`로 분 단위 무-state-push), `confirm_kb_delete`(비-background, H1).
- **부류**: A(인프라+코드).
- **증상**: 변환+ingest/인덱스정리 동안 ws로 아무 트래픽이 없으면 Nginx/ALB **ws idle 타임아웃**에 연결이 끊겨 완료 후에도 이벤트 무반응 → 새로고침 필요.
- **수정 방향**: (1) 프록시(Nginx `proxy_read_timeout`/ALB idle)의 **websocket 타임아웃을 넉넉히**. (2) 긴 poll 중 주기적 진행 상태를 push(heartbeat) 해 연결 유지 + 사용자에 진행 표시.
- **회귀 위험**: (1) 설정 변경(낮음), (2) 코드(낮음).

---

## LOW

### L1. `load_kb_docs` 비-background S3 목록 조회
- **위치**: `chat_state.py` `load_kb_docs`(~764), `run_in_executor`(~795/872/875).
- 문서 수가 많거나 S3가 느리면 목록 로딩 동안 짧은 먹통. 보통 빠름. 커지면 background 전환.

### L2. paste/첨부 흐름 JS 전역(`_pastedFiles`) 잔여
- **위치**: `handle_paste_upload`(~1387), `trigger_upload`(~1360). 첨부는 **DB 폴링(`poll_attachments`, background)** 기반이라 콜백-Promise 점유가 없어 상대적으로 견고. 다만 에러 경로의 `_pastedFiles` 정리 일관성 점검 권장.

### L3. (검증 항목) 새 `on_upload_complete` background 정확성
- 모든 `self.xxx` 접근이 `async with self:` 안인지(리뷰상 OK), **call_script 콜백이 background 이벤트를 정상 트리거하는지 스모크 테스트**(파일 1개 업로드 완료까지).

### L4. `confirm_kb_delete` 종료 보장 미흡
- `finally` 없음, TimeoutError 특화 처리 없음(현재 `except Exception`이 포괄). H1 수정 시 함께 정리.

---

## 교차 관찰 (구조적)
업로드/picker가 **JS 전역 + 폴링 Promise + call_script 콜백**에 의존하는 게 A·B 부류의 공통 뿌리다. 단기 개별 수정(H1/M1~M4)으로 충분히 잡히지만, 중기적으로 **단일 출처화**(서버 state 중심) 또는 Reflex 네이티브 업로드로의 전환을 고려하면 이 부류가 통째로 사라진다. (큰 작업이라 별도 검토.)

## 권장 수정 순서 & 방법 (회귀 최소화)
1. **관측성 먼저(위험 0)**: 주요 이벤트 핸들러 진입/종료+소요시간 로그, ws connect/disconnect 로그, 의심 시 `_kbSelectedFiles` 콘솔 덤프. → 먹통이 ws-drop인지 큐 적체인지, 디싱크가 이름불일치인지 즉시 판별.
2. **dev에서 재현**(이제 KB 리소스가 dev/prd 격리라 안전): 대형/다중 파일·연타·picker 취소·처리 중 대화전환·네트워크 throttle.
3. **H1 먼저**(가장 명확·동작 보존), 이후 M1(NFC)→M3→M2→M4.
4. **원칙**: 동작 보존, 한 번에 하나, 재작성보다 가드, dev 우선, 변경마다 스모크 체크리스트(개인 업로드/팀 업로드/문서 삭제/처리 중 대화전환/다중 파일/picker 취소).
