# report_maker 마이그레이션 — 최종 설계 문서

**작성**: 2026-07-16 · **상태**: 확정 대기(승인 시 구현 착수)
**목표**: 외부 랜딩 사이트(`wellbot/services/report_maker/legacy/`)의 "보고서 문구 작성 지원 에이전트"를, `report_checker`와 동일 패턴으로 wellbot 내부 AI 서비스(`/ai-services/report-generator`)로 편입.

## 확정된 결정
1. **legacy 폴더 유지** — `report_maker/legacy/`는 그대로 두고(추후 삭제), 신규 코드는 `report_maker/` 하위에 독립 서비스로 신설.
2. **범위** — 전체 기능 이식(스타일 학습 + 다단계 게이트 생성 + 편집 루프 + 대화 이력).
3. **인프라** — wellbot 네이티브 기반 + **AgentCore 유지**(스타일 프로파일).
4. **`chat_models.py` 재사용** — UI 상태의 대화/메시지 pydantic 모델 재사용.
5. **백지에서 리팩터 구조로 재작성** — legacy `chat_state.py`(2,500줄 CR)를 참조만 하고, 새 구조로 다시 씀.
6. **정리 깊이** — 적극 리팩터링(프롬프트→코드, LLM 호출 통합, 스트리밍, 구조화 질문).

---

## 1. 타깃 디렉토리 구조 (report_checker 선례 미러링)

```
wellbot/services/report_maker/
  legacy/              # (유지, 참조 전용, 추후 삭제)
  __init__.py
  config.py            # report_maker.yaml 로드. 하드코딩 계정ID/리소스 → env/yaml
  report_maker.yaml    # 모델ID·S3 prefix·한도·AgentCore memory_id 등
  prompts/             # legacy config.py(1135줄) 프롬프트 상수 분리
    __init__.py
    outline_rules.py   #   OUTLINE_GENERATION_RULES
    structure_rules.py #   REPORT_STRUCTURES / STRUCTURE_HEADING_RULES / PAGE_STRUCTURE_GUIDE
    table_rules.py     #   TABLE_READING_RULES + 표 지침
  models.py            # dataclass: OutlineRequest, TopicAnalysis, StructureProposal,
                        #            StyleProfile, GateQuestions, FlowState, ReportMessage
  bedrock.py           # wellbot 기존 Bedrock 클라이언트 재사용 + 단일 invoke/stream 헬퍼
  analysis.py          # analyze_topic(주제→구조화 JSON)
  structure.py         # propose_structure + 게이트/근거검증(page_info·deepdive·grounding·remaining)
  build.py             # build_outline / edit_outline (스트리밍)
  style.py             # 문서 파싱(pptx/pdf/이미지 OCR) + analyze_style + build_style_desc
  memory.py            # 스타일 프로파일: AgentCore(/writing·/preference) + agnt_mmry_use_n 등록 + S3 fallback
  storage.py           # S3 = app storage_service 뒤로 격리, emp_no 스코프. 파일 전용
  db.py                # MySQL(get_session): 대화·메시지·템플릿 CRUD
  parsing.py           # util.py 이식: parse_page_count, 표 정규화, strip_code_fences, to_safe_id

wellbot/state/report_maker_state.py    # flow_stage 상태머신 재작성. 신원=AuthState emp_no. 스트리밍
wellbot/state/report_maker_scripts.py  # 스크롤/파일선택 클라이언트 스크립트
wellbot/pages/report_maker.py          # chat_layout 재사용. 사이드바·인사·입력·아웃라인 렌더
wellbot/api/report_maker_api.py        # 업로드(주제 첨부 + 스타일 문서). 인증=세션쿠키→emp_no
```

**등록**: `pages/__init__.py` export → `wellbot.py` `add_page(route="/ai-services/report-generator", on_load=AuthState.check_auth)` → `api/app.py` 라우터 include.
**카탈로그**: `config/ai_services.yaml`의 `report-generator` 카드를 외부 URL → 내부 라우트(`external:false`). `REPORT_GENERATOR_URL` 상수/치환은 내부 라우트 안정화 후 제거.

## 2. 데이터 모델 / 영속 매핑 (기존 스키마 재사용)

| legacy 저장 | wellbot 네이티브 (재사용) |
|---|---|
| 사번 텍스트 입력(인증 없음) | AuthState 세션 → `emp_no` (서버 검증). **C1 IDOR 해결** |
| S3 conversations/*.json | `chtb_smry_d`(ChatSummary, 대화 헤더) + `chtb_msg_d`(ChatMessage, `AGNT_ID='report-maker'` 태깅) |
| S3 template_meta.json (보고서 유형) | `agnt_mmry_use_n`(AgentMemory) 행: (agnt_id, emp_no, path=AgentCore 네임스페이스, dscr=표시명) |
| AgentCore /writing·/preference | **AgentCore 유지** + `agnt_mmry_use_n`으로 경로/동기화 추적 |
| combined_style.json | S3 fallback(emp_no 스코프) |
| S3 입력 파일 | `{S3_KEY_PREFIX}/report_maker/{emp_no}/{template}/...` (storage_service) |
| flow_state(진행 스냅샷) | **저장 안 함(확정)** — 라이브 세션(Reflex State)에만 유지 |

- **flow_state 비영속(확정)**: "몇 장?"·구조 확인 등 생성 도중 중간 상태는 저장하지 않는다. 대화 재진입 시 마지막 아웃라인 메시지로 `self.outline`만 복원 → 편집 계속 가능. 트레이드오프: 봇 질문에 답하는 *도중* 새로고침하면 그 단계는 이어지지 않음(드문 엣지). 신규 테이블 없음.
- **에이전트 태그(확정)**: 코드 상수 `AGNT_ID = "report-maker"`로 메시지 태깅(메인 채팅과 분리). `AGNT_ID`는 태그 문자열이며 `agnt_m` 매칭 행이 없어도 fallback 동작(report_checker 동일). 표시명이 필요하면 관리자 화면에서 등록 — 별도 시드/마이그레이션 불필요.
- **`chat_models.py` 재사용**: 사이드바 대화목록·기본 메시지는 `Conversation`/`Message` 재사용. 아웃라인 전용 표시 필드(is_outline·iteration·is_flow·style_saved)는 `models.py`의 `ReportMessage`(Message 확장)로 처리.

## 3. 신원 · 인증

- 모든 진입에 `on_load=AuthState.check_auth`. 상태·API 어디서도 클라이언트 문자열을 신원으로 신뢰하지 않음.
- API 업로드/다운로드는 `auth_service.validate_session_token(wellbot_auth)` → `emp_no` 도출(report_checker 동일).
- 모든 S3 key·DB row·AgentCore actor_id는 서버가 도출한 `emp_no`로 스코프. actor_id = `f"{emp_no}_{to_safe_id(template)}"`.

## 4. 서비스 로직 리팩터 (적극)

- **프롬프트→코드**: 프롬프트 문자열은 `prompts/`로 분리. 결정론 로직(표 정규화·페이지 수 파싱·TBD 판정·기호 계층)은 `parsing.py` 순수 함수로.
- **단일 Bedrock 헬퍼**: `bedrock.py`에 `invoke_json()` / `stream_text()` 하나로 통합(legacy 5+ 중복 호출부 제거). 통일 try/except + 재시도.
- **구조화 질문 프로토콜**: 게이트/근거검증 질문을 자연어 헤더 매칭 대신 JSON(`{questions:[...], sufficient:bool}`)으로 주고받아 M8 취약성 제거.
- **스트리밍**: `build_outline`/`edit_outline`을 토큰 스트리밍으로 → "생성 중" 대기 체감 개선, max_tokens 초과 시 명시적 에러.
- **예외/UI**: 모든 `_handle_*`가 실패 시 `is_streaming=False` + 사용자 메시지. 트레이스백은 서버 로깅만(M1).

## 5. 상태머신 (report_maker_state) — 백지 재작성

legacy `flow_stage` 흐름을 동일 UX로 재현하되 구조 정리:
```
입력 → (의도분류) → _start_flow
  analyze_topic → await_page_count
  → [심층·정보부족] await_struct_info(구조화 질문)
  → propose_structure + grounding → await_clarify
  → 1회 되묻기 await_outline_info → run_build(스트리밍)
  → 편집 루프(edit_outline, 누적 지시)
스타일 학습(별도): 업로드 → 파싱 → analyze_style → AgentCore 저장 → user_mode=report_based
```
- 신원·템플릿은 `AuthState`/DB에서. flow_state 필드는 `models.FlowState` dataclass로 묶어 관리(legacy의 산발 15개 state var 정리).
- 대화 저장은 턴 종료 시 `db.save_conversation`(chtb_smry_d/chtb_msg_d) + flow_state 스냅샷.

## 6. API 엔드포인트 (report_checker 미러)

- `POST /api/report_maker/upload` — 주제 첨부/스타일 문서 업로드. 세션 인증 → emp_no. 확장자·크기 검증, `basename`+uuid, 처리 후 삭제(H1/H2). S3 저장 후 참조 반환.
- 필요 시 `POST /api/report_maker/export` — 아웃라인 마크다운/파일 다운로드(스트리밍, 한글 파일명 RFC 5987).

## 7. 접는 리뷰 결함 (이식 중 동시 수정)

C1 인증/IDOR · H1/H2 업로드 경로조작 · H3 예외 통일 · H4 actor_id · M1 트레이스백 · M4 하이라이트(dict==str) · M5/M8 프롬프트·질문 구조화 · L3 CR 줄바꿈(LF + `.gitattributes`) · print→logging.

## 8. 빌드 순서(태스크) & 테스트

- **P0** 스캐폴드: 디렉토리 + `report_maker.yaml` + `prompts/` 분리 + `models.py` + `AGNT_ID` 상수. (신규 테이블·시드 없음)
- **P1** 순수 로직: parsing/analysis/structure/build/style + 단일 Bedrock 헬퍼 + 구조화 JSON. (단위 테스트: 파싱·스키마·page_count)
- **P2** 영속화: db.py(chtb_smry_d/chtb_msg_d/agnt_mmry_use_n) + memory.py(AgentCore) + storage.py — 전부 emp_no 스코프. (통합 테스트: emp_no 격리)
- **P3** API: 업로드 + 세션 인증.
- **P4** 상태머신: report_maker_state, AuthState 결합, 스트리밍.
- **P5** UI: page + 컴포넌트(chat_layout·사이드바·입력) wellbot 스타일.
- **P6** 배선: 등록 + ai_services.yaml 전환 + E2E 스모크(생성 1회 + 스타일 학습 1회).
- **P7** 정리: 외부 URL 상수 제거, `legacy/` 삭제 여부 확인, 문서화.

## 9. 착수 전 확정 항목 — 모두 확정됨

1. **flow_state 저장** → **저장 안 함**(라이브 세션만, 신규 테이블 없음).
2. **보고서 유형(템플릿) 저장** → **`agnt_mmry_use_n` 재사용**(표시명=dscr, AgentCore 경로와 1:1).
3. **아웃라인 산출물** → **현행 유지**(마크다운 표시 + 복사). pptx/pdf 내보내기 범위 제외.
4. **에이전트 태그** → 코드 상수 `AGNT_ID="report-maker"`. 시드/마이그레이션 불필요(관리자 화면 등록은 선택).

→ 미해결 항목 없음. **문서 확정 — 구현 착수 가능.**
```
