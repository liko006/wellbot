# KB 기능 병합 계획 (wellbot_legacy → wellbot)

## 배경
- **wellbot** = master(이 저장소, git 연결). 챗봇 뼈대·근본 리팩터 보유. **구조·시그니처·사용 패턴 보존**.
- **wellbot_legacy** = KB 작업 폴더(`../wellbot_legacy`). 이식 대상 기능 원본.
- 공통 조상 A는 git으로 연결돼 있지 않음 → **자동 3-way 머지 불가**. 내용 기반 병합.
- 양쪽 모두 현재 정상 동작. **이식 후 동일 동작 보존이 최우선.**

## 원칙 / 가드레일
1. wellbot이 바꾼 파일은 **wellbot 형태 유지 + KB 추가분만 체리픽**.
2. KB 도메인 파일은 legacy 내용을 이식하되 **wellbot의 import·함수명·API와 reconcile**(불가피 시 KB 호출부 최소 수정).
3. 파일마다 `py_compile`, 클러스터마다 영향 점검. 최종 스모크는 사용자가 앱 실행으로 확인.
4. **위험 파일만 컨펌**: chat_state, team_kb_manager, storage_service, config(knowledgebase), tool_executor, kb_upload → 병합안 보여주고 진행.
5. AWS 리소스 **동일** → knowBase.yaml·.env 실값 그대로 이식.
6. legacy의 `[TEMP page-verify]` 디버그 로그는 이식 시 제거.

## 필수 버그 이식
- **toolConfig 버그**: tool 3회+ 호출 시 `_emit_no_tool_fallback`에 `tool_config=None` 전달 → Bedrock "toolConfig must be defined" 오류. legacy 수정본(실제 tool_config 전달)을 **wellbot tool_loop.py에 이식**. (먼저 wellbot에 버그 존재 확인)

## 차이 파일 (25 .py + 4 설정) — leg+/new+ = legacy추가/wellbot추가 라인

### Tier A — wellbot 미변경(new+=0), legacy 통째 이식
| 파일 | leg+/new+ | 상태 |
|---|---|---|
| state/chat_models.py | 11/0 | ⬜ |
| components/chat/message_bubble.py | 12/0 | ⬜ |

### Tier A0 — legacy 추가 없음 → wellbot 유지(검증만)
| 파일 | leg+/new+ | 상태 |
|---|---|---|
| services/knowledgebase/personal_kb_manager.py | 0/2 | ⬜ |

### Tier B — KB 도메인, legacy 권위 + wellbot API reconcile
| 파일 | leg+/new+ | 상태 |
|---|---|---|
| services/knowledgebase/kb_utils.py | 159/22 | ⬜ |
| services/knowledgebase/kb_retriever.py | 43/18 | ⬜ |
| components/chat/kb_panels.py | 166/41 | ⬜ |
| api/kb_download.py | 39/10 | ⬜ |
| api/kb_upload.py | 101/56 | ⚠위험 ⬜ |
| scripts/shared_kb_manager.py | 370/98 | ⬜ |
| scripts/transform_lambda.py | 32/24 | ⬜ |
| scripts/cleanup_personal_kb.py | 16/16 | ⬜ |
| state/chat_helpers/upload_script.py | 28/8 | ⬜ |
| state/chat_helpers/download_script.py | 2/2 | ⬜ |

### Tier C — 공통/근본, wellbot 보존 + KB 체리픽
| 파일 | leg+/new+ | 상태 |
|---|---|---|
| state/chat_state.py | 115/50 | ⚠위험(최난도) ⬜ |
| services/chat/tool_executor.py | 38/23 | ⚠위험 ⬜ |
| services/ai/bedrock/tool_loop.py | 10/4 | ⬜ (toolConfig fix 포함) |
| state/chat_helpers/system_prompt.py | 3/3 | ⬜ |
| components/chat/input_bar.py | 8/4 | ⬜ |
| components/chat/file_icon.py | 4/4 | ⬜ |
| constants.py | 11/2 | ⬜ |
| services/knowledgebase/__init__.py | 3/3 | ⬜ |
| api/download.py | 8/2 | ⬜ (비-KB, 분리 흔적 확인) |

### Tier C★ — wellbot이 더 많이 바꿈(반드시 보존)
| 파일 | leg+/new+ | 상태 |
|---|---|---|
| services/files/storage_service.py | 13/36 | ⚠위험 ⬜ |
| services/knowledgebase/config.py | 7/16 | ⚠위험 ⬜ |
| services/knowledgebase/team_kb_manager.py | 37/56 | ⚠위험 ⬜ |

### 설정/의존성
| 파일 | 상태 |
|---|---|
| pyproject.toml (KB 의존성 병합) | ⬜ |
| config/knowBase.yaml (실값 이식) | ⬜ |
| .env.example (KB env) | ⬜ |
| docs/nginx-reflex.conf (선택) | ⬜ |

## 실행 Phase (의존성 역순)
- **P1 기반**: constants → pyproject(deps) → .env.example → knowBase.yaml → knowledgebase/__init__
- **P2 KB 서비스 코어**: config.py(★) → kb_utils → kb_retriever → personal/team_kb_manager(★team)
- **P3 저장/IO**: storage_service(★) → api(kb_upload★, kb_download, download) → scripts(shared_kb_manager, transform_lambda, cleanup)
- **P4 UI/헬퍼/툴**: system_prompt, upload/download_script, kb_panels, input_bar, file_icon, message_bubble, chat_models, tool_executor(★), tool_loop(+toolConfig fix)
- **P5**: chat_state.py (최후, 단독)
- **P6**: py_compile 전수 + 사용자 스모크

## P4 완료 요약
- `chat_models.py` ✅ — 2단계 KB 모델(KbSharedSubfolder + KbSharedFolder.subfolders) 이식. Message.source_docs는 wellbot이 이미 보유.
- `components/chat/message_bubble.py` ✅ — legacy 통째(인용칩 + PDF 페이지 표시). new+=0이라 안전.
- `components/chat/kb_panels.py` ✅ — legacy 통째(2단계 트리 + xlsx callout). wellbot 41줄은 1단계 baseline이라 대체. (스크롤바 실험은 롤백돼 없음)
- `services/chat/tool_executor.py`(★) ✅ — wellbot 설계 보존(kb_scope 스키마 제거 + _parse_top_k), KB 추가: source_docs pages 수집 + top_k 설명 f-string 픽스(top_k=10 실효).
- `services/ai/bedrock/tool_loop.py` ✅ — **toolConfig 버그 픽스 이식**(_emit_no_tool_fallback 에 tool_config 전달, None→실값). source_docs 이벤트는 wellbot 기보유.
- `system_prompt.py`·`download_script.py`·`file_icon.py`·`input_bar.py` — docstring/표현 차이뿐 또는 동일 → **wellbot 유지.**
- `upload_script.py` — wellbot은 `credentials:'include'`로 세션인증 일관. legacy의 dedup/allowedNames/504 UX는 chat_state 업로드 흐름과 강결합 → **P5에서 함께 판단.**

## P5·P6 완료 요약
- `state/chat_state.py`(★최난도) ✅ — wellbot 근본(kb_scope 주입·COMPLETE_WITH_ERRORS startswith·kb_flyout·세션인증 업로드 흐름) 보존. graft 6개:
  1. KbSharedSubfolder import  2. has_tabular_pending computed var  3. load_kb_docs 회사탭 1단계→2단계(대분류/소분류, raw/+originals/ 병합)  4. 출처 누적에 pages 합집합 병합  5. finally 에 _with_pages_display(인용칩 PDF 페이지)  6. confirm_upload_via_api 4-arg(allowedNames).
  검증: legacy 대비 LEG-only 36줄 전부 docstring 표현 + `== "COMPLETE"`(wellbot startswith 보존). 누락 0.
- `state/chat_helpers/upload_script.py` ✅ — wellbot 유지(credentials:'include' 세션인증·엔드포인트·에러처리) + **double-pick 버그 픽스만** 추가: change 핸들러 누적+dedup, uploadKbFilesToApi allowedNames 필터. → 두 번 선택 시 유실/제거파일 업로드 둘 다 해결.
- **P6**: 변경 12개 파일 전수 py_compile OK. 일관성 grep(subfolders·pages_display·has_tabular_pending 생산/소비, 옛 folder.files 잔여 0) 통과. **런타임 스모크는 사용자 앱 실행으로 확인 예정.**

## 최종 변경 파일 (실제 이식/수정)
1. constants.py — KB_MAX_DOCS, PDF_VIA_UPSTAGE, KB_SEARCH_TOP_K=10
2. services/knowledgebase/kb_utils.py — Upstage 변환·count/limit·PDF분기·delete xlsx/pdf (additive)
3. services/knowledgebase/kb_retriever.py — page 추출·다중 suffix 매핑·title 일반화
4. services/files/storage_service.py — list 제외 `_xlsx.md`/`_pdf.md` 추가(1줄)
5. scripts/shared_kb_manager.py — legacy 통째(hierarchy/rename/upstage/None-fix) + pptx_to_dict 위임
6. state/chat_models.py — 2단계 KB 모델(KbSharedSubfolder)
7. components/chat/message_bubble.py — 인용칩 + PDF 페이지 표시(legacy)
8. components/chat/kb_panels.py — 2단계 트리 + xlsx callout(legacy)
9. services/chat/tool_executor.py — pages 수집 + top_k f-string(wellbot kb_scope 제거·_parse_top_k 보존)
10. services/ai/bedrock/tool_loop.py — **toolConfig 버그 픽스**(tool_config 전달)
11. state/chat_helpers/upload_script.py — double-pick 픽스(누적+allowedNames)
12. state/chat_state.py — KB graft 6개(위)

## wellbot 유지(이식 0, wellbot이 동급↑ 또는 위임으로 커버)
config.py, personal/team_kb_manager, kb_upload(세션인증+위임), kb_download, download, transform_lambda, cleanup_personal_kb, system_prompt, download_script, file_icon, input_bar, pyproject.toml, .env.example, knowBase.yaml, __init__.py

## 진행 로그
- (작성 시작) 조사 완료, 계획 수립. 코드 수정 전.
- 전 Phase(P1~P6) 완료. 12개 파일 이식/수정, py_compile·일관성 검증 통과. 런타임 스모크 대기.

## 병합 후 추가 미세 조정 (같은 세션)
- `scripts/shared_kb_manager.py` — `--parser {auto,upstage,local}` 플래그 추가. auto=기존 게이트, upstage=강제 Upstage, local=pandas/pdfplumber. `_use_upstage(ext,parser)` 헬퍼 + upload_files/upload_and_ingest 인자 + CLI. 기본 auto라 하위호환.
- `components/chat/message_bubble.py` — 출처 칩을 `rx.tooltip(chip, content=doc["title"])`로 감싸 긴 파일명 hover 시 전체 표시 (액션 아이콘과 동일 패턴 재사용).
- `services/knowledgebase/kb_retriever.py` — page 추출부에 "page는 로컬 파싱 PDF에만 존재, Upstage 변환 PDF엔 없음" 주석.
- 정책 확인: 사용자 xlsx=pandas / 공용 CLI xlsx=Upstage / PDF=전 경로 Upstage (병합 후에도 유지). 출처 page 표시는 로컬 파싱 PDF에만 동작(유지 결정).
- 향후 작업(보류): Upstage 파싱 PDF에도 page 표시 — file_parser+transform_lambda 손봐야 함(난이도 중상). 메모리에 기록.
- **P1 완료**:
  - `constants.py` ✅ 이식 — KB_MAX_DOCS 블록, PDF_VIA_UPSTAGE 블록 추가, KB_SEARCH_TOP_K 5→10. (py_compile OK)
  - `pyproject.toml` — 라인 차이 0(cosmetic). wellbot 유지, 이식 불필요. KB deps 이미 보유.
  - `.env.example` — wellbot이 superset(KB_ID 포함). wellbot 유지.
  - `wellbot/services/knowledgebase/__init__.py` — docstring 표현 차이뿐, export 동일. wellbot 유지.
  - `config/knowBase.yaml` — 구조 동일, wellbot이 실제 folders 등록(규정→9QQTRS4JPN) 보유. wellbot 유지.
  - 결론: P1에서 실제 코드 이식은 constants.py 1개.
- **P2 진행**:
  - `services/knowledgebase/config.py` — wellbot이 상위 호환(footgun 수정 truthy-override + KB_ID 처리). legacy 고유 KB 키 없음 → **wellbot 유지, 이식 0.**
  - `services/knowledgebase/kb_utils.py` ✅ **병합 완료**(additive). wellbot 베이스(storage_service 클라이언트·pptx_to_dict·멀티시트 split 보존)에 legacy KB 기능 추가: Upstage 변환(xlsx/pdf gate, _convert_via_upstage, convert_xlsx/pdf_to_markdown), count_kb_docs/enforce_kb_doc_limit, upload PDF 분기+enforce 호출, delete .xlsx/.pdf 분기, import(tempfile/상수). 검증: legacy 대비 LEG-only 16줄 모두 비기능(리팩터 대체+docstring), 누락 0. py_compile OK.
  - `services/knowledgebase/kb_retriever.py` ✅ **병합 완료**(강화분 적용). _coerce_page 추가, _map_to_original_uri 다중 suffix(pptx/xlsx/pdf), title strip 일반화, 결과 dict에 page 추가. **[TEMP page-verify] 로그는 제외.** wellbot 근본 변경 없음. py_compile OK, 누락 0.
  - `services/knowledgebase/personal_kb_manager.py` — wellbot 고유 2줄은 개선(`startswith("COMPLETE")` 부분실패도 등록). legacy 추가 없음 → **wellbot 유지.**
  - `services/knowledgebase/team_kb_manager.py`(★) — wellbot이 부서별 threading 락(동시 생성 레이스 방지) 추가한 개선판. legacy KB 기능 추가 없음(LEG-only=락없는 baseline+docstring) → **wellbot 유지, 이식 0.**
  - **P2 완료**: 실제 이식 = kb_utils, kb_retriever 2개. config/personal/team은 wellbot이 동급 이상이라 유지.
- **P3 진행**:
  - `services/files/storage_service.py`(★) ✅ — wellbot 근본 리팩터 다수 보존(get_client 공개 래퍼, build_content_disposition_header, head_object/iter_download_stream/list_objects_with_meta 의 bucket 파라미터). legacy-only는 그 함수들의 구버전+제외 1줄. **이식 = list_objects_with_meta 제외 목록 `_pptx.json` → `(_pptx.json,_xlsx.md,_pdf.md)` 1줄만.** py_compile OK.
  - `api/kb_download.py`, `api/download.py` — wellbot이 storage_service 헬퍼로 리팩터한 버전(KB 로직 동일). legacy 추가 없음 → **wellbot 유지.**
  - `api/kb_upload.py`(★) — wellbot이 세션 인증(emp_no/dept_cd 서버 도출) + `upload_files_to_kb` 위임으로 리팩터. P2에서 그 함수에 PDF/enforce 병합했으므로 **위임으로 KB 기능 자동 적용** → **wellbot 유지(보안 리팩터 보너스).**
  - `scripts/shared_kb_manager.py` ✅ — **legacy 통째 가져옴**(hierarchy `_split_folder`/`rename_folder`/PDF upstage/folder None-fix 전부) + wellbot의 pptx_to_dict 위임 리팩터 재적용(import+convert_pptx_to_json). py_compile OK.
  - `scripts/transform_lambda.py`, `scripts/cleanup_personal_kb.py` — 차이가 전부 docstring/주석 표현뿐(코드 동일) → **wellbot 유지.**
  - **P3 완료**: 실제 변경 = storage_service(1줄), shared_kb_manager(legacy+pptx재적용). 나머지는 wellbot 유지(대부분 wellbot이 리팩터로 동급↑, 기능은 P2 kb_utils 위임으로 커버).

## 사후 정리 (post-merge cleanup — 코드 효율성 점검)
병합·인용페이지 커밋(cdc6eff + d8b78a3) 대상으로 3개 리뷰 에이전트(KB코어 / chat·state·tool / UI·CLI)를 돌려 중복·죽은코드·구조 미반영·비효율만 점검. 큰 오류 없음(서버 기능 점검 통과). 아래 수정은 **전부 동작 무변경**. commit/push는 사용자.

- `scripts/shared_kb_manager.py` — **wellbot DRY 구조 복원**(병합 시 legacy 통째 복사로 되돌아갔던 부분). `ROWS_PER_SPLIT·TABULAR_EXTS·CONVERTIBLE_EXTS·MAX_FILE_SIZES·MAX_FILE_SIZE_DEFAULT·SUPPORTED_EXTENSIONS`를 kb_utils에서 import(로컬 재정의 삭제), `collect_files_from_dir` 인라인 set→`SUPPORTED_EXTENSIONS`(값 동일). `_originals_prefix`엔 "kb_utils.get_originals_prefix와 통합 금지(그쪽은 raw/ 이후를 잘라 소분류 유실; 1단계 개인/팀용)" 주석.
- `services/knowledgebase/kb_utils.py` — `_stash_original()` 헬퍼 추출로 pptx/pdf 분기의 originals 저장+URI기록 중복 제거. xlsx 비대칭(개인/팀 pandas vs 공용 CLI Upstage)이 **의도된 정책**임을 주석화. `_PART_RE`↔`cleanup_existing_parts` 정규식 결합 주석.
- `services/chat/tool_executor.py` + `state/chat_state.py` — 인용페이지(d8b78a3)에서 추가됐던 `pages`(list)가 `rank_pages`(dict)와 완전 중복(`pages == sorted(set(rank_pages.values()))` 항상 성립)이라 **`pages` 제거, rank_pages에서 파생**. 그룹핑/병합/표시 3곳에서 pages 빌드·머지·in-loop sort 삭제(~15줄). `_with_pages_display`는 `cited_ranks & rank_pages.keys()`로 일원화. 표시 페이지 집합 이전과 동일.
- 점검 결과 깨끗: kb_panels(스크롤바 잔재 0), kb_retriever, storage_service, tool_loop, message_bubble, chat_models, constants(추가 상수 4개 전부 사용), upload_script, chat_state의 load_kb_docs·kb_scope 주입·_parse_top_k 재사용.
- (범위 밖) shared_kb_manager의 `_validate_file_size`/`_cleanup_existing_parts`/`_split_and_upload_tabular`는 kb_utils와 병렬 중복이나 병합 이전부터 존재 — 미수정.
- 검증: 4개 파일 py_compile OK, 제거한 `pages` 키를 읽는 곳 없음(grep 확인).
