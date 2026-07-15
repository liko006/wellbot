# Task 3 설계 — 공용(shared) KB 권위 가중 재랭킹

> 상태: 재랭킹(3.1~3.4)+프롬프트(3.5) **구현 완료** + **§8 공용 목록 N단계 트리화도 구현 완료**(브랜치 `feat/kb-rerank-shared-docs`, 전부 미커밋). §8은 UI 리팩터라 dev 실물 렌더 확인 권장.
> 관련 메모리: planned-authority-weighted-rag, kb-compliance-prompt-placement, planned-shared-kb-admin-ui

## 1. 목적 / 배경

공용 KB 검색 결과를 **문서별 권위 티어(authority tier)** 에 따라 정렬에서 우대한다.
높은 티어(0=최상) 문서의 청크가 상위에 오면 LLM 이 그 내용을 더 인용·활용할 확률이 올라간다.

**설계 전환(중요)**: 초기엔 "폴더(대분류/소분류) 권위"였으나, 실제 계층 정리 결과 **우선순위가 도메인
경계를 가로지르고**(A도메인 1번 > B도메인 2번, 같은 도메인 안에서도 문서마다 상이) 폴더-가중으로
표현 불가함이 드러남. 폴더는 **도메인(표시·목록용)** 으로만 두고, **권위는 문서별 전역 티어(속성)** 로
분리한다. 두 축(도메인/티어)이 직교하므로 폴더 트리에 함께 담지 않고 티어를 config 속성으로 관리.

**두 층으로 구성**: (1) **재랭킹**(3.1~3.4) — 점수를 바꾸지 않고 정렬 순서만 우대하는 **소프트** 층
(문서 티어 → 배수 곱셈). (2) **프롬프트 보강**(3.5) — 재랭킹만으로는 "상위 문서를 반드시 쓰게" 강제
못 하므로, shared 전용으로 "권위 문서 기준 답변 + 충돌 시 병기 + 규정 헤지"를 지시해 **override 의도를
실제로 전달**하는 층.

키워드로 질의를 분류하는 방식(범용성 훼손)은 배제하고, **floor 게이팅 + 약한 곱셈**으로
"일반 질문에 의미만 유사해 약하게 걸린 상위티어 문서가 오발동 우선되는" 케이스를 차단한다.
티어는 **전역**(0순위가 도메인 무관하게 1순위를 이김), 배정은 **문서 단위**(폴더 기본값 없음).

## 2. 현재 상태 (코드 검증 완료)

- `kb_retriever._merge_results`: `score` 내림차순 정렬 → `KB_MIN_SCORE`(0.4) 미만 제외 → 1-index `rank` 부여.
  이 `merged` 가 (a) `source_docs` 의 `ranks` 와 (b) `_format_context` 의 `[N]` 순서를 **둘 다** 만든다.
- `source_uri` 형태: 공용은 `…/shared/{대분류}/(raw|originals)/{소분류}?/{파일}`. team/personal 은 `teams/`·`users/`.
  - 변환본(pptx/xlsx/pdf)은 `_map_to_original_uri` 로 `/raw/`→`/originals/` 매핑됨 → `originals` 도 처리 대상.
- `get_kb_config()` 는 모듈 전역 캐시(`_kb_config`) — 1회 로드. 추가 I/O 없음.
- 상수: `KB_MIN_SCORE=0.4`, `KB_SEARCH_TOP_K=10`.

## 3. 설계 / 로직

### 3.1 설정 (config/knowBase.yaml)

```yaml
shared_kb:
  # (1) 티어 사다리: 티어 번호(0=최우선) → 정렬 배수. 전역·단조감소. 작고 거의 불변(튜닝값).
  authority_tiers:
    0: 1.5
    1: 1.35
    2: 1.2
    3: 1.1
  # (2) 문서별 속성(문서-major): "도메인/[서브/]파일명" → {tier, dept, ...}. raw/originals 마커는 키에 안 씀.
  #     한 문서=한 entry(속성 추가 시 그 줄에 키만). tier=권위(사다리 참조), dept=담당부서(컨텍스트 (담당:X) 노출). (→추후 DB 행)
  docs:
    "발주/발주규정.pdf":      { tier: 0, dept: "구매팀" }
    "인사/취업규칙.pdf":      { tier: 1, dept: "인사팀" }
    "발주/2024/구매지침.pdf": { tier: 2 }              # dept 없음(부서 미표시)
```

- `docs` 가 **비어있거나 없으면 재랭킹 전혀 안 함** → 현행과 100% 동일 (투명 옵트인).
- **문서-major**: 한 문서 = 한 entry, 속성은 그 안에 중첩(`{tier, dept, ...}`) → **속성 추가 시 문서당 키 하나만**(속성-major 병렬맵의 "속성×문서 줄 폭증" 회피). DB 테이블(doc=행, 속성=열)과 1:1 → 이관 시 각 entry가 한 행.
- `tier` 미지정/미매칭 → 배수 1.0(중립). 티어 값이 `authority_tiers` 에 없어도 1.0.
- `dept` 는 **tier 와 독립**(정렬 무관, 표시 전용): 컨텍스트에 `(담당: X)` 노출 → 규정 답변(3.5) 시 그 부서로 최종확인 안내. 미지정이면 부서 미표시(일반 "담당 부서" 폴백). tier 없이 dept만, dept 없이 tier만도 가능.
- **floor 는 yaml 에 두지 않는다.** `KB_MIN_SCORE`(0.4)와 같은 성격(모델 score 분포 의존, 배포 무관)의 형제 임계값이므로 `KB_MIN_SCORE` 처럼 **constants.py 상수 `KB_AUTHORITY_FLOOR = 0.55` 단일 소스**로 둔다.
- `authority_tiers`(사다리)는 per-doc 이 아니라 **티어→배수 룩업표**라 `docs` 와 분리 유지(작고 거의 불변, config). `docs`(문서 배정, 50→150건)가 **DB 이관 후보**.

### 3.2 가중 규칙

1. **대상**: `source == "shared"` 결과만. team/personal 은 항상 weight = 1.0.
   - **shared 전용 3중 보장**: (a) 설정이 `shared_kb:` 섹션 안에만 존재, (b) 코드 게이트 `source != "shared"` → 1.0, (c) 개인/팀 KB 는 `users/{emp}/…`·`teams/{team}/…` 경로라 `shared_base()` 마커 미포함 → 문서 키 산출 불가. 하드 보장은 (b).
2. **문서 키 산출(`_shared_doc_key`)**: `source_uri` 에서 `shared_base()` 마커 뒤를 취해 `raw`/`originals` 세그먼트만 제거 → `"도메인/[서브/]파일명"`. 이게 `docs` 조회 키.
   - **[F1] env-suffix**: dev 는 경로가 `/shared-dev/` 라 `"/shared/"` 하드코딩 split 은 매칭 실패. `kb_utils.shared_base()` import 해 `f"/{shared_base()}/"` 로 split → dev/prd 모두 정상. (순환 없음: kb_utils 는 kb_retriever 미참조)
   - **[F2] percent-encoding**: Bedrock `source_uri` 는 URL 인코딩됨(`%20`→공백 치환 코드에서 확인). 한글이 `%EC…` 로 올 수 있어 각 세그먼트를 `urllib.parse.unquote` 후 매칭(인코딩 안 돼 있어도 no-op).
3. **속성 조회**: `attrs = docs.get(문서키)` → `tier = attrs.get("tier")`(없으면 배수 1.0) → `weight = authority_tiers.get(tier)`(없으면 1.0, `_tier_weight` 가 int/str 키 모두 매칭). `dept = attrs.get("dept")`(표시용). tier·dept 상호 독립(하나만 있어도 됨).
   - 문서마다 자기 속성으로 독립 결정 (출처 간 비교 아님). **폴더는 무관**(도메인=표시용).
4. **floor 게이팅**: `raw score >= KB_AUTHORITY_FLOOR`(constants.py, 0.55) 일 때만 배수 적용. 미만은 원본 score 로 정렬.
   - floor 가 `min_score`(0.4)보다 **높아야** 의미 — 0.4 면 필터 통과 전 문서가 가중돼 게이트 무의미.
   - 0.55 시작 → DEBUG 로그로 분포 본 뒤 상수 보정(재배포). `KB_MIN_SCORE` 튜닝과 동일.
5. **정렬키**: `score_eff = score * weight` (게이트 통과 시) `else score`. 소프트(A안): 티어 배수는 순서 확률만 올림 — raw score 차가 크면 하위 티어가 상위로 갈 수 있음(프롬프트 3.5 가 "상단 기준"으로 보강).
   - `item["score"]` (원본) 은 **그대로 유지** — UI 표시 score·`min_score` 필터·`[N]` 인용 매칭은 전부 원본 기준.
6. **rank / context**: 가중 순서로 `rank` 부여 → LLM 이 보는 `[N]` 순서와 `source_docs.ranks` 에 반영(= 의도한 소프트 효과).
   - **[F3] membership 영향**: `merged = filtered[:top_k]` 로 자르므로 가중은 순서뿐 아니라 **top_k 진입 여부**도 바꿈. 권위 우대 취지상 의도된 동작(기존에도 KB 간 raw score 경쟁으로 membership 결정). "순서만"이 아니라 "무엇이 보이는지"도 바뀜을 인지.

### 3.3 의사 코드 (kb_retriever)

```python
from urllib.parse import unquote
from wellbot.services.knowledgebase.kb_utils import shared_base   # [F1]

# source_uri → "도메인/[서브/]파일명" (raw|originals 제거, unquote). shared 아니면 None
def _shared_doc_key(source_uri: str):
    marker = f"/{shared_base()}/"           # [F1] dev=/shared-dev/, prd=/shared/
    if marker not in source_uri:
        return None
    parts = [unquote(p) for p in source_uri.split(marker, 1)[-1].split("/") if p]  # [F2]
    for i, p in enumerate(parts):
        if p in ("raw", "originals"):
            parts = parts[:i] + parts[i + 1:]
            break
    return "/".join(parts) or None

def _tier_weight(tier, tiers: dict) -> float:   # yaml int/str 키 모두 허용
    if tier in tiers:       return float(tiers[tier])
    if str(tier) in tiers:  return float(tiers[str(tier)])
    return 1.0

# 문서별 tier 배수. 미배정/미설정/비-shared 이면 1.0
def _authority_weight(source: str, source_uri: str, tiers: dict, docs: dict) -> float:
    if source != "shared" or not docs:
        return 1.0
    key = _shared_doc_key(source_uri)
    if key is None:
        return 1.0
    attrs = docs.get(key)
    if not attrs:
        return 1.0
    tier = attrs.get("tier")
    if tier is None:
        return 1.0
    return _tier_weight(tier, tiers)

# _merge_results: filter → (배수 계산 + 로깅) → eff score 정렬 → top_k → rank
filtered = [r for r in all_results if r.get("score", 0.0) >= min_score]
cfg = get_kb_config().get("shared_kb", {})
tiers = cfg.get("authority_tiers") or {}
docs = cfg.get("docs") or {}          # 문서-major: "도메인/파일명" → {tier, dept, ...}
floor = KB_AUTHORITY_FLOOR   # constants.py 단일 소스 (yaml override 없음)

# [F4] decorate-then-sort: sort key 부작용 제거, shared 결과 로깅, tie-break 명시적
scored = []
for r in filtered:
    src, uri = r.get("source", ""), r.get("source_uri", "")
    w = _authority_weight(src, uri, tiers, docs)
    eff = r["score"] * w if (w != 1.0 and r["score"] >= floor) else r["score"]
    if src == "shared":
        attrs = docs.get(_shared_doc_key(uri))
        dept = attrs.get("dept") if attrs else None   # 표시 전용(정렬 무관)
        if dept:
            r["dept"] = dept                          # _format_context 가 (담당: X) 로 노출
        log.debug("rerank src=%s uri=%s raw=%.4f w=%.2f eff=%.4f", src, uri, r["score"], w, eff)
    scored.append((eff, r))
# _format_context: f"[{rank}] [{label}] {title}{' (담당:'+dept+')' if dept else ''}\n{content}"
# stable sort → eff 동점 시 filtered 순서(shared→team→personal, 각 score desc) 유지
scored.sort(key=lambda t: t[0], reverse=True)
merged = [r for _, r in scored[:top_k]]
for idx, item in enumerate(merged, 1):
    item["rank"] = idx
```

### 3.4 컨텍스트에서 score 줄 제거 (`_format_context`)

현재 `_format_context` 는 각 결과를 `[N] [라벨] 제목 / 내용 / (score: X)` 로 포맷해 **raw score 를 LLM 에게 그대로 노출**한다. 리랭킹은 **순서**를 eff 로 바꾸지만 표시 score 는 원본이라, 부스트된 권위 문서가 `[1] (score:0.70)`, 강등된 문서가 `[2] (score:0.72)` 처럼 나와 **순서와 점수가 모순** → LLM 이 순서 신호를 부분 상쇄할 수 있다(override 의도 약화).
```python
# 변경 전: f"[{rank}] [{label}] {title}\n{content}\n(score: {score})"
# 변경 후: f"[{rank}] [{label}] {title}\n{content}"
```
- raw cosine score 는 LLM 에 해석가치가 낮고 이제 순서와 충돌 → **줄 제거**. 유도는 **위치(rank)** 로만.
- 리랭킹과 무관한 전 쿼리의 컨텍스트 포맷도 바뀌지만(부작용 최소), score 노출은 원래도 불필요했음.

### 3.5 프롬프트 보강 — 규정 답변 + 문서 충돌 처리 (shared 전용, `augment_system_with_kb`)

리랭킹은 순서(확률)만 바꿔 "상위 문서를 반드시 쓰게" 강제하지 못하므로, override 의 실질은 **프롬프트 층**에서 만든다. `augment_system_with_kb(base_prompt, kb_modes)` 는 이미 `kb_modes` 를 받고 인용 `[N]` 규칙을 안내하므로, **`"shared" in kb_modes` 일 때만** 아래 블록을 기존 블록 뒤에 append 한다.
- shared 전용 근거: 개인/팀 KB 엔 규정 문서 없다는 가정 + **권위 정렬이 shared 결과에만** 적용되므로 "상위=권위" 표현이 shared 에서만 정확(개인/팀은 상위=최고점).
- `[N]` 규칙은 기존 블록에서 이미 정의됨 → 보조 문서 표기는 그 위에 얹힘.

**확정 문구 (초안 — 실사용 충돌 양상 관찰 후 튜닝. 샘플 확보 어려워 지금 확정):**
```python
# augment_system_with_kb: 기존 block append 후
result = f"{base_prompt}\n\n{block}"
if "shared" in kb_modes:
    shared_block = textwrap.dedent("""\
        **규정·사규 관련 답변 (해당 시)**
        - 질문이 사내 규정·사규·정책과 관련되면, 답변의 근거가 된 조항을 함께 제시하고 그 조항에 기반해 답하세요.
        - 특정 행위·상황이 규정에 부합하는지/위배되는지 묻는 경우엔, 근거 조항에 비추어 부합/위배 여부까지 판단해 답하세요.
        - 규정은 개정·예외가 있을 수 있으니, 판단이 필요하거나 중요한 사안은 담당 부서에 최종 확인하도록 안내하세요. 근거 문서에 담당 부서가 표시(담당: ...)돼 있으면 그 부서명으로 안내하세요.

        **문서 간 내용이 다를 때**
        - 검색 결과는 관련도·권위 순으로 정렬돼 **위에 있을수록 우선**입니다. 여러 문서가 상충하면 **상단(먼저 제시된) 문서를 기준**으로 답하세요.
        - 하위 문서에 의미 있게 다른 내용이 있을 때만 함께 안내하세요(충돌이 실제 있고 유의미할 때만, 억지 병기 금지). 그 문서는 **파일명으로 지칭**하세요.""")
    result = f"{result}\n\n{shared_block}"
return result
```
- **① 규정 답변**: 자기-게이팅(LLM 이 규정성 질의일 때만 적용). 트리거는 **규정 관련 전반**(정보성·단순확인 포함) → 근거 조항 제시 + 조항 기반 답변; **부합/위배 질의는 그 안의 특수 케이스**로 판단까지. 개정·예외 가능성에 담당부서 최종확인 안내 — **부서는 문서별**(`docs[key].dept`)로 컨텍스트 `(담당: X)` 에서 읽어 지목, 미지정이면 일반 "담당 부서". (메모리: kb-compliance-prompt-placement)
- **② 충돌 시 권위 우선 + 병기**: 상단(먼저 제시된=권위 반영) 문서 기준, 하위에 유의미하게 다른 내용이면 **파일명으로 지칭**해 병기(예: `다만 「취업규칙」에는…`). 파일명 근거: LLM 이 보는 `title`(_format_context)은 `metadata_title or 파일명` 폴백이라 실무상 대부분 파일명 표시([kb_retriever.py:115](../../wellbot/services/knowledgebase/kb_retriever.py)). 프롬프트엔 "제목 지칭"만 두고, 보조 문서 `[N]` 부착·칩 표시는 **같은 프롬프트의 기존 인용 규칙이 이미 커버**(재진술 안 함, 중복 제거). `[N]` 이 화면서 제거된다는 구현 디테일도 LLM 에 노출 불요. 설계 근거: LLM 은 context 에서 순서·제목은 보지만 score 는 못 봄(3.4)이라 `[N]` 지시어 대신 순서/제목으로 지칭해야 문장이 안 깨짐(chat_state.py:2071 이 `[N]` strip). rerank 는 하위 문서를 버리지 않고 순서만 바꿔 하위 내용이 이미 context 에 있으므로 "제시 방식" 지시로 충분(강제보다 준수율↑, 빠뜨려도 권위 문서 기반 graceful fallback).
- **안전성**: `docs` 비어(리랭킹 무동작)도 "상단=최고점" 으로 자연 degrade. shared 아니면 블록 자체가 안 붙어 개인/팀 무영향.

## 4. 변경 파일 / 단계

| 파일 | 변경 |
|---|---|
| `config/knowBase.yaml` | `shared_kb` 에 `authority_tiers`(티어→배수)·`docs`(문서-major: 문서→`{tier, dept}`) 추가(예시 주석). 기본은 비움(무동작). floor 는 yaml 에 안 둠. |
| `wellbot/constants.py` | `KB_AUTHORITY_FLOOR: float = 0.55` 추가 (`KB_MIN_SCORE` 옆, 단일 소스). |
| `wellbot/services/knowledgebase/kb_retriever.py` | `_shared_doc_key()`·`_tier_weight()`·`_authority_weight(문서→티어→배수)` 추가 + `_merge_results` decorate-then-sort 로 교체(`authority_tiers`/`docs` 조회, shared 결과에 `dept` 부착) + DEBUG 로그 + **`_format_context` score 줄 제거 + `(담당: X)` 노출([3.4])**. import 추가: `shared_base`(kb_utils, [F1]), `unquote`(urllib.parse, [F2]). |
| `wellbot/state/chat_helpers/system_prompt.py` | `augment_system_with_kb` 에 **shared 전용 블록 append**(규정 헤지 + 문서 충돌 처리, [3.5]). 시그니처 무변경(`kb_modes` 이미 받음). |

그 외(`tool_executor`, `chat_state`, 호출부, 시그니처) **무변경**.

## 5. 리스크 / 롤백

- **경로 키 취약성**: `docs` 키가 `"도메인/파일명"` 이라 파일 **rename/재업로드 시 배정이 끊김**(재지정 필요). 폴더(도메인) 이동도 동일. 근본 해소는 DB 안정 doc_id 이관(§3.1). 시범기간엔 수기 관리로 감수.
- **문서 단위 관리 부담**: 50→150건을 문서별로 티어 배정 → 수기 yaml 은 실수 여지. Task 1 admin UI(문서 행 티어 드롭다운)로 완화. DEBUG 로그(`w=1.00` 이면 미배정/미매칭)로 누락 가시화.
- **과전도**: 큰 배수로 순서가 과하게 뒤집힘 → floor(0.55) + 완만한 티어 사다리(1.1~1.6)로 완화, 로그로 튜닝.
- **config 캐시**: `get_kb_config()` 가 1회 캐시라 런타임 yaml 수정은 **재시작 필요**(기존 `_shared_kb_id` 등과 동일). Task 1 UI 는 캐시 갱신 헬퍼로 즉시 반영.
- **롤백**: `docs` 를 비우면 즉시 현행 동작으로 복귀 (코드 롤백 불필요).

## 6. 검증

- **DEBUG 로그** (per-doc: source, uri, raw score, weight, weighted score) — 현재 score 를 안 찍으므로 튜닝 위해 필수.
  - **[F1/F2] 첫 실행 확인**: 로그의 `uri=` 로 (a) `shared_base()` 세그먼트가 실제 경로와 일치하는지, (b) 한글 파일명이 percent-encoding 돼 있는지 확인. **티어 배정한 문서인데 `w=1.00` 만 찍히면** 문서키 미매칭 신호(base 불일치·unquote 누락·`docs` 키 오타).
- 대표 질의 N개로 **재랭킹 on/off 비교**: 상위 출처·`[N]` 인용 변화.
- 기존 grounding 로그(`retrieved/cited`) 관찰.
- **프롬프트(3.5) 동작 확인** (shared 활성 시): ⓐ 규정 부합/위배 질의 → 근거 조항 제시 + 담당부서 최종확인 안내 ⓑ 상충 문서 상황 → 상단 문서 기준 답변 + 유의미할 때만 병기(파일명 지칭). shared 아닌 턴엔 블록 미부착 확인.
- **Acceptance**: ① 상위 티어 문서가 동점·근소 차에서 상위로(전역: 0순위가 도메인 무관하게 우대) ② 일반 질문에선 floor 로 약한-매칭 상위티어 문서가 오발동 우선되지 않음 ③ `docs` 비우면 현행과 동일(리랭킹) + shared 프롬프트 블록만 추가.

## 7. 제외 / 별도 작업

- **다중 kb_search 인용 rank 전역화**(한 턴 내 여러 호출의 rank 1..N 겹침 → 출처 칩 과다귀속·페이지 오표시)는 본 작업과 무관한 인용 인프라 이슈로 **제외**. 별도 예정 작업으로 메모리에만 등록(planned-kb-citation-global-rank).
- (규정 헤지 + 문서 충돌 처리 프롬프트는 **본 작업 범위로 편입** → 3.5 참조. 초안 확정, 운영 관찰 후 튜닝.)

## 8. 공용 문서 목록 N단계 트리화 (본 브랜치 동반 구현 — **구현 완료**)

> 상태: **구현 완료**(chat_models·chat_state·kb_panels 수정, py_compile + 평탄화/가시성 단위검증 통과). **미커밋**. UI 리팩터라 dev 실물 렌더 확인 권장(§8.5). 표시층 작업이라 rerank 코드와 무관·별도 커밋 가능.

### 8.1 배경 / 목적
현재 공용 KB 목록은 **2단계 고정**(대분류→소분류→파일, [chat_state.py:1086-1094](../../wellbot/state/chat_state.py) 파싱 + [kb_panels.py](../../wellbot/components/chat/kb_panels.py) 중첩 foreach + `chat_models.KbSharedFolder/Subfolder`). 저장·리랭킹은 이미 임의 깊이 지원(리랭킹 키=`_shared_doc_key` 전체 경로)이나 **목록만 3단계+를 파일명에 흡수**해 지저분. 깊이 추가마다 재작업이 필요 없도록 **N단계 일반화**한다.

### 8.2 방식 — 평탄-행(flat rows) (핵심)
**Reflex `rx.foreach` 는 임의 깊이 재귀 불가**(컴파일 타임 컴포넌트 확정). 지금도 재귀가 아닌 2단계 중첩 foreach. → 트리를 **`depth` 를 가진 평탄 행 리스트**로 변환해 **단일 foreach + `padding_left = depth × 단위`** 로 렌더 → 깊이 무관, 한 번 구현으로 영구.
- **펼침/접힘 인프라 재사용**: `expanded_kb_folders` 가 **이미 경로 키 집합**(`대분류`, `대분류/소분류` `.contains()`) → N단계 그대로. `toggle_kb_folder(path)` 유지.
- **가시성**: computed var — 어떤 행은 그 **조상 경로가 모두 `expanded_kb_folders` 에 있을 때만** 표시.

### 8.3 수정 범위 (3곳, 표시층 국소)
| 파일 | 현재(2단계) | N단계(평탄 행) |
|---|---|---|
| `state/chat_models.py` | `KbSharedFolder→Subfolder→File` 중첩 | 평탄 `KbTreeRow{depth, path, is_folder, name, uploaded_at, expires_at}` |
| `state/chat_state.py`(`load_kb_docs` 공용 분기) | `folder_map[top][sub]` 2단계 | 전체 경로 split → 중첩 dict 트리 → **DFS 평탄화(depth 부여)** + 가시성 computed var |
| `components/chat/kb_panels.py` | 중첩 foreach 3함수 | **단일 foreach + depth 들여쓰기**(폴더 행=토글박스, 파일 행=파일 UI). 토글 로직 재사용 |

### 8.4 유의점
- **선택/삭제는 이번 범위 아님**: 공용(shared) 탭은 **읽기전용**(체크박스·삭제 푸터는 개인/팀 탭에만) → 지금은 선택/삭제 키 변경 불필요. 단 **Task 1 admin UI 처럼 공용 트리에 삭제가 붙으면** `file_name` 아닌 **전체 `path` 기준**으로 해야 N단계 동명 파일 충돌 방지(그때 처리). 개인/팀은 폴더 없는 flat 뷰라 무관.
- **Task 1 수혜**: admin UI 좌측 트리가 "kb_panels 트리 재사용"(admin-ui §7) → N단계 평탄-행 컴포넌트를 그대로 재사용해 **admin 트리도 자동 N단계**.
- **무관**: 리랭킹·업로드·다운로드·`_shared_doc_key`·`docs` yaml 키 전부 무변경(순수 표시층). 개인/팀 flat 뷰도 무변경.

### 8.5 검증 (구현 후)
- **완료**: py_compile 3파일 + 평탄화/가시성 알고리즘 단위검증(2·3·4단계 혼재, 완전중복 dedup, 미펼침→대분류만·부분펼침·전펼침 가시성).
- **dev 실물 확인 필요**: 트리 렌더(들여쓰기 depth×1.25em·펼침/접힘 토글), 기존 2단계 데이터 회귀, 개인/팀 flat 뷰 무변경.
