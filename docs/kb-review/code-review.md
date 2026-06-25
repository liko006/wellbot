# KB 기능 코드 리뷰 (feat/knowledge-base)

## 메타
- **대상**: `git diff main...HEAD` 전체 (52개 파일, 약 7,000줄 추가)
- **브랜치**: `feat/knowledge-base`
- **일자**: 2026-06-25
- **모드**: recall 우선(실제 버그 누락 최소화) — 10개 독립 finder 앵글 → 중복 제거 → 1표 3상태 검증 → gap sweep → 종합
- **결과**: 원시 후보 61건 → 중복 제거 50건 → 검증 통과(non-REFUTED) 47건 → sweep 신규 4건 → 최종 **15건** (모두 CONFIRMED 또는 PLAUSIBLE)

## 심각도 분포
| 등급 | 건수 | 항목 |
|---|---|---|
| 🔴 보안 / 데이터 유출 | 1 | #1 |
| 🔴 데이터 정합성 / 손실 | 4 | #2 #3 #4 #5 |
| 🔴 처리 실패 / 행(hang) | 3 | #6 #7 #8 |
| 🟠 문서 한도(cap) 로직 | 3 | #9 #10 #11 |
| 🟠 클라이언트 / UX | 2 | #12 #13 |
| 🟠 AI 루프 / 어드민 스크립트 | 2 | #14 #15 |

## 근본 원인 테마
세 갈래의 공통 원인이 발견 항목의 절반 이상을 만든다.

1. **클라이언트 `self.dept_cd` 신뢰** (#2, #7) — 업로드는 서버 도출 `get_dept_cd(emp_no)`를 쓰는데 삭제·인덱싱은 클라이언트 상태 `self.dept_cd`를 사용 → 신뢰 경계 불일치. 서버 도출 값으로 일원화 필요.
2. **분할 파일 ↔ 논리 문서 매핑 불일치** (#4, #5, #10, #11) — `_partN`/멀티시트 xlsx의 논리 문서 추적이 삭제·카운트·재업로드 경로마다 제각각. 매핑을 `kb_utils` 한 곳으로 집약 필요.
3. **`emp_no` 기반 KB 도출과 S3 작업의 순서·원자성** (#3, #9) — KB 레코드 확인 전 S3 삭제, 락 없는 read-then-check 등.

---

## 상세 발견

### 🔴 보안 / 데이터 유출

#### 1. 교차 버킷 데이터 유출
- **위치**: `wellbot/api/kb_download.py:92`
- **내용**: `download_kb_file`이 사용자가 보낸 `s3_uri`에서 버킷을 그대로 신뢰하는데, `_check_access`는 **키 prefix만** 검증한다.
- **재현**: 인증된 사용자가 `{"s3_uri": "s3://다른-내부-버킷/shared/secret.pdf"}`를 `/api/download_kb`에 POST → 키가 `shared/`로 시작하므로 `_check_access` 통과 → IAM 역할이 읽을 수 있는 **임의 버킷**의 객체가 스트리밍되어 유출.
- **권장**: 파싱한 버킷을 KB 버킷(설정값)과 대조 검증. prefix뿐 아니라 버킷도 화이트리스트화.

### 🔴 데이터 정합성 / 손실

#### 2. 팀 삭제가 클라이언트 `dept_cd` 사용
- **위치**: `wellbot/state/chat_state.py:731`
- **내용**: 삭제는 `delete_files_from_team_kb(self.dept_cd, ...)`(클라이언트 상태)로 prefix를 만들지만, 재인덱싱은 `emp_no`로 도출한 실제 KB에서 실행.
- **재현**: 위·변조되거나 stale한 `dept_cd` → `teams/{다른_부서}/raw/`를 지우거나(IAM 허용 시 타 팀 파일 삭제) 아무것도 안 지움. 실제 KB에는 문서가 남아 poll은 COMPLETE, UI는 "삭제 완료"로 표시되지만 문서는 여전히 검색됨.

#### 3. 개인 삭제가 KB 레코드 확인 전에 S3 삭제
- **위치**: `wellbot/state/chat_state.py:749`
- **내용**: `delete_files_from_personal_kb`가 S3 객체를 먼저 지운 뒤 `get_user_kb()`가 `None`이면 `RuntimeError`.
- **재현**: S3에는 개인 KB 파일이 있으나 `AGNT_MMRY_USE_N` row가 없는 사용자(이전 인덱싱이 COMPLETE에 도달 못한 경우)가 삭제 → S3 삭제 후 예외 → 재인덱싱 미실행 → **원본은 사라졌는데 벡터는 검색 가능**.

#### 4. 삭제 시 `_partN` 분할 객체 미삭제
- **위치**: `wellbot/services/knowledgebase/kb_utils.py:709`
- **내용**: csv/xlsx가 `sales_part1.csv`, `sales_part2.csv`로 저장됐는데, 삭제는 존재하지 않는 `sales.csv` 키만 계산해 지움.
- **재현**: `delete_object`가 no-op → part 객체 잔존 → 다음 인덱싱에 재스캔. "삭제 성공" 후에도 문서가 검색되고, `count_kb_docs`가 parts를 한 문서로 묶으므로 doc 한도도 회수되지 않음.

#### 5. `cleanup_existing_parts` 정규식 과매칭
- **위치**: `wellbot/services/knowledgebase/kb_utils.py:153`
- **내용**: 비앵커 정규식 `^{stem}(?:_.+)?_part\d+$`가 이름 prefix가 같은 무관한 파일의 part까지 매칭.
- **재현**: 멀티시트 `report_summary.xlsx`(→ `report_summary_Sheet1_part1.xlsx`) 업로드 후 `report.xlsx` 재업로드 → `cleanup_existing_parts(stem="report")`의 정규식이 `report_summary_Sheet1_part1`을 매칭 → **무관한 `report_summary.xlsx`의 청크가 조용히 삭제**. 다음 인덱싱에서 해당 행 소실(데이터/인덱스 손실).
- **권장**: stem 경계를 명확히 anchoring.

### 🔴 처리 실패 / 행(hang)

#### 6. `_chunk_text` 무한 루프
- **위치**: `scripts/transform_lambda.py:263`
- **내용**: fixed 모드에서 `start += cs - ov`. `CHUNK_OVERLAP >= CHUNK_SIZE`면 step ≤ 0.
- **재현**: `CHUNKER_TYPE=fixed, CHUNK_SIZE=1000, CHUNK_OVERLAP=1000` 설정 시 `while start < len(text)`가 진행되지 않아 무한 루프(청크 메모리 무한 증가) → Lambda 타임아웃까지 행 → Bedrock 인덱싱 작업 FAILED. 기본 recursive 모드만 테스트하면 가려짐.

#### 7. 팀 인덱싱이 클라이언트 `dept_cd` 사용
- **위치**: `wellbot/state/chat_state.py:1156`
- **내용**: `on_upload_complete`의 팀 분기가 `get_or_create_team_kb`에 `self.dept_cd`(클라이언트)를 넘김. 업로드는 서버 도출 `get_dept_cd(emp_no)`로 수행됨.
- **재현**: 로그인 후 DB의 `pstn_dept_cd`가 변경되면, 업로드는 `teams/{server_dept}/raw/`에, 인덱싱은 `teams/{client_dept}/raw/` 데이터소스에 대해 실행 → 방금 올린 파일이 인덱싱 안 됨. 그럼에도 `ingestion_status='ready'`로 표시. (#2와 동일한 신뢰 경계 문제)

#### 8. 팀 인덱싱에 동시성 가드 누락
- **위치**: `wellbot/state/chat_state.py:1159`
- **내용**: `is_ingestion_in_progress` 없이 `team_start_ingestion` 직접 호출. (팀 DELETE 경로와 dead code `upload_and_ingest`는 가드를 사용)
- **재현**: 같은 `dept_cd` 두 명이 수 초 내 업로드 확정 → 동일 `(kb_id, data_source_id)`에 둘 다 `start_ingestion` → Bedrock은 데이터소스당 in-flight 1개만 허용하므로 두 번째가 `ConflictException` → 일반 오류 메시지("문서 처리 중 오류가 발생했습니다")로 떨어지고 문서 미인덱싱.

### 🟠 문서 한도(cap) 로직

#### 9. `enforce_kb_doc_limit` TOCTOU
- **위치**: `wellbot/services/knowledgebase/kb_utils.py:588`
- **내용**: 부서 락 없이 read-then-check(count → 비교 → put). 팀 업로드 경로에 상호배제 없음.
- **재현**: 한도 근처의 팀 KB에 같은 부서 두 명이 동시에 3개씩 업로드 → 둘 다 같은 기존 카운트를 읽고 `existing+new <= cap` 통과 → 둘 다 put → `KB_MAX_DOCS['team']` 초과.

#### 10. 멀티시트 xlsx를 여러 문서로 카운트
- **위치**: `wellbot/services/knowledgebase/kb_utils.py:565`
- **내용**: `count_kb_docs`가 `{stem}`이 아니라 `{stem}_{sheet}`로 묶음.
- **재현**: 3시트 워크북 `budget.xlsx`(local/pandas 경로)가 `budget_Sheet1_part1.xlsx` 등으로 저장 → `_PART_RE` group(1)이 시트별로 달라 논리 문서 3개로 집계 → 한도에 의도보다 훨씬 빨리 도달.

#### 11. 동일명 재업로드를 신규로 카운트
- **위치**: `wellbot/services/knowledgebase/kb_utils.py:588`
- **내용**: `count_kb_docs`가 기존 S3 키 기준으로만 dedup → 재업로드(덮어쓰기)를 신규 1건으로 더함.
- **재현**: 개인 KB가 한도(5개) 가득 + `report.pdf` 포함. `report.pdf` 갱신 재업로드 → `existing(5)+new(1)=6 > 5`로 `ValueError`("개인 지식베이스에는 최대 5개까지...") → 결과 문서 수는 그대로 5인데도 정당한 덮어쓰기가 거부됨.

### 🟠 클라이언트 / UX

#### 12. KB 업로드가 `backendBase` prefix 없는 하드코딩 경로
- **위치**: `wellbot/state/chat_helpers/upload_script.py:169`
- **내용**: 형제 스크립트(upload/download)는 split-port 로컬 개발용으로 `env.PING`에서 `backendBase`를 계산하는데, `uploadKbFilesToApi`만 `'/api/upload_kb_files'` 고정.
- **재현**: 프론트(:3000)/백엔드(:8000) 분리 로컬 환경에서 POST가 `http://localhost:3000/api/upload_kb_files`로 가지만 해당 라우트는 백엔드에만 등록 → 모든 개인/팀 KB 업로드가 404/연결 오류(로컬 split-port 한정).

#### 13. `openKbFilePicker` dedup 배열 미초기화
- **위치**: `wellbot/state/chat_helpers/upload_script.py:133`
- **내용**: `openKbFilePicker`가 `window._kbSelectedFiles`로 신규 선택을 dedup하지만 `remove_pending_file`는 이 배열을 비우지 않음(파이썬 상태만 변경).
- **재현**: `a.pdf` 선택 → X로 제거 → picker 재오픈 후 `a.pdf` 재선택 → `added=[]`(여전히 `_kbSelectedFiles`에 존재) → cancel 이벤트 미발생으로 picker promise가 30,000ms 타임아웃까지 행 후 `[]` 반환. 페이지 새로고침 전까지 복구 불가.

### 🟠 AI 루프 / 어드민 스크립트

#### 14. fallback 턴이 `toolChoice auto`로 tool_use 무시
- **위치**: `wellbot/services/ai/bedrock/tool_loop.py:313`
- **내용**: empty-result/max-iter fallback이 `tool_config`(toolChoice auto) + 프롬프트 안내("도구 호출 금지")에 의존.
- **재현**: `empty_streak` 한도 초과 후 `_emit_no_tool_fallback` 실행 시, 모델이 안내를 무시하고 텍스트 없이 `toolUse`만 응답 → fallback 루프가 tool_use를 버리고 `yielded_any_text=False` → 사용자에게 "첨부 파일에서 관련 내용을 찾지 못했습니다" 표시(모델은 계속 검색하려던 상황).

#### 15. 공유 KB 업로드가 첫 시트만 읽음
- **위치**: `scripts/shared_kb_manager.py:495`
- **내용**: `_split_and_upload_tabular`가 `pd.read_excel(file_path)`(기본 `sheet_name=0`)로 첫 시트만 로드 — 전 시트를 읽는 `kb_utils`와 비대칭.
- **재현**: 어드민이 `--action upload --file budget.xlsx --parser local`로 시트 `['2025','2026','notes']` 워크북 업로드 → `'2025'`만 분할·인덱싱, 나머지는 미인덱싱. 어드민은 전체가 검색 가능하다고 오인.

---

## 권장 조치 우선순위
1. **즉시(보안)**: #1 — 버킷 검증 추가.
2. **데이터 정합성 핵심**: #2·#7 신뢰 경계 일원화(서버 도출 `dept_cd`), #3 작업 순서(레코드 확인 후 S3 삭제), #4·#5·#10·#11 분할 파일↔논리 문서 매핑을 `kb_utils` 단일 지점으로 통합.
3. **안정성**: #6 chunk step 가드(`ov < cs` 검증), #8·#9 동시성 가드/락.
4. **UX·기타**: #12·#13 클라이언트 스크립트, #14 fallback toolChoice, #15 멀티시트 처리.

> 비고: 본 리뷰는 자동 다중 에이전트 분석 결과이며, 각 항목은 독립 검증 단계(CONFIRMED/PLAUSIBLE)를 통과함. 수정 전 해당 라인 재확인 권장.
