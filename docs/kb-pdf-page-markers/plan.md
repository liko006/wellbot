# Task 2 설계 — Upstage PDF 페이지 정보 유지 → 출처 page 표시

> 상태: **설계 확정 (구현 전)**. 브랜치는 이 문서 리뷰 후 생성.
> 관련 메모리: planned-upstage-pdf-page-markers

## 1. 목적 / 배경

PDF 를 Upstage Document Parse 로 KB 에 업로드하면 `_pdf.md`(aggregate markdown)로 내려가면서
**페이지 정보가 사라져** 출처에 page 가 표시되지 않는다. Upstage 응답의 `elements[i].page` 를 살려
md 에 페이지 마커를 심고, Lambda 가 이를 청크 `page` 메타로 태깅하도록 한다.

표시 측(retriever→tool_executor→chat_state→message_bubble)은 **이미 완성**되어 있고,
**생산 파이프라인(파싱→md→Lambda 청킹)만** 손보면 된다.

## 2. 현재 상태 (코드 검증 완료)

### 두 PDF 색인 경로
| 경로 | 조건 | page 현황 |
|---|---|---|
| pdfplumber (로컬) — Lambda `parse_pdf` | `PDF_VIA_UPSTAGE=False` 또는 Upstage 실패 폴백 | **이미 page 태깅됨**(transform_lambda.py:415,434) |
| Upstage DP — `_pdf.md` → Lambda `parse_md` | `PDF_VIA_UPSTAGE=True` (기본) | **page 없음** ← 본 작업 대상 |

### 검증된 사실
- Upstage 응답에 `response.json()['elements'][i]['page']` 존재, **int** (실제 샘플 확인).
- `UpstageParser.parse()` → `_call_api` 는 `content.markdown`(aggregate)만 `.text` 로 사용, `elements` 는 비었을 때 fallback 으로만 씀 ([file_parser.py:343-354](../../wellbot/services/files/file_parser.py)).
- `_parse_pdf_split` 은 분할 part 별 `_call_api` 호출 + `total_pages` 누적 → **글로벌 페이지 오프셋 데이터 이미 보유** ([file_parser.py:265-288](../../wellbot/services/files/file_parser.py)).
- **`UpstageParser.parse()` 는 KB 와 첨부가 공유** (첨부: attachment_service.py:212 `get_parser().parse()`, KB: kb_utils.py:346 `UpstageParser().parse()`).
- Lambda `parse_md` ([transform_lambda.py:342](../../scripts/transform_lambda.py)) 는 헤더로 섹션 분리 후 청킹. 청크 메타 `essential` 에 `page` 이미 포함(:122).

## 3. 설계 / 로직

### 3.1 격리 원칙 (B안)
첨부 파싱 동작은 **전혀 건드리지 않는다**. `ParsedDocument.text`(aggregate)는 현행 유지 →
첨부는 무영향. 페이지 데이터는 **별도 필드**로 노출하고, KB 변환 경로(kb_utils)에서만 소비.

### 3.2 `ParsedDocument.pages` 필드 추가
```python
pages: list[tuple[int, str]] = field(default_factory=list)  # (전역 페이지번호, 페이지 markdown)
```
- 기본 빈 리스트 → 다른 파서(Local/Hybrid)·다른 형식엔 무영향(하위 호환).

### 3.3 `_call_api` — elements 를 page 별로 묶어 `pages` 빌드
```python
elements = payload.get("elements", []) or []
by_page: dict[int, list[str]] = {}
for el in elements:
    pg = el.get("page")
    md = (el.get("content") or {}).get("markdown") or (el.get("content") or {}).get("text") or ""
    if pg is not None and md:
        by_page.setdefault(int(pg), []).append(md)
pages = [(pg, "\n".join(by_page[pg])) for pg in sorted(by_page)]
# .text 는 현행 그대로(aggregate). pages 는 추가 반환.
```
- 이 page 번호는 **이번 호출(=분할 시 part) 기준 로컬 번호**.

### 3.4 `_parse_pdf_split` — 글로벌 페이지 오프셋
```python
total_pages = 0
all_pages: list[tuple[int, str]] = []
for part_path in parts:
    part_result = self._call_api(part_path)
    texts.append(part_result.text)
    for pg, md in part_result.pages:
        all_pages.append((pg + total_pages, md))   # total_pages = 이전 parts 누적 = 오프셋
    total_pages += _count_pdf_pages(part_path)
return ParsedDocument(text="\n\n".join(texts), page_count=total_pages, pages=all_pages, ...)
```

### 3.5 kb_utils — 마커 심은 md 조립 (PDF 한정)
`convert_pdf_to_markdown` 가 `parsed.pages` 로 마커 md 생성:
```python
if parsed.pages:
    md = "\n".join(f"<!--page={pg}-->\n{block}" for pg, block in parsed.pages)
else:
    md = parsed.text   # pages 없으면 폴백
```
- `_convert_via_upstage` 에 `with_page_markers` 플래그를 두거나, PDF 전용 분기에서 처리.
- xlsx 경로(`convert_xlsx_to_markdown`)는 마커 없이 현행 유지.

### 3.6 마커 포맷
`<!--page=N-->` (HTML 주석).
- regex-split 로 깔끔히 제거되어 청크 텍스트 오염 없음.
- 실제 사규 본문과 충돌 가능성 거의 없음.
- 마커 없는 md(`_xlsx.md`·일반 md)엔 무영향.
- 색인용 중간 산출물에만 존재 — 사용자에겐 출처가 `originals/` 원본 PDF 로 매핑되므로 노출 안 됨.

### 3.7 Lambda `parse_md` — 마커 인라인 유지 + 청크 시작 페이지 태깅 (병합 허용, 확정)
페이지 블록으로 **선분할하지 않는다**(청크가 페이지 경계를 넘어 병합되도록 허용 → 짧은청크
파편화 방지). 마커를 텍스트에 남긴 채 **기존 헤더 섹셔닝 + `_chunk_text` 를 그대로** 수행하고,
생성된 청크를 문서 순서대로 후처리해 **시작 페이지**를 태깅하고 마커를 제거한다.
```python
_PAGE_MARKER_RE = re.compile(r"<!--page=(\d+)-->")

def parse_md(data, source):
    content = data.decode("utf-8", errors="replace")
    chunks  = _parse_md_sections(content, source)   # 기존 parse_md 본문(헤더 split + _chunk_text) 그대로, 마커는 텍스트에 포함된 채
    # 청크를 문서 순서대로 순회하며 시작 페이지 태깅 + 마커 제거
    current_page = None
    for c in chunks:
        markers = [int(m) for m in _PAGE_MARKER_RE.findall(c.text)]
        lead = re.match(r"\s*<!--page=(\d+)-->", c.text)
        start_page = int(lead.group(1)) if lead else current_page   # 경계 걸친 청크 = 시작 페이지
        if markers:
            current_page = markers[-1]        # 청크 내 마지막 마커를 다음 청크로 이월
        elif start_page is not None:
            current_page = start_page
        if start_page is not None:
            c.metadata["page"] = start_page
        c.text = _PAGE_MARKER_RE.sub("", c.text).strip()   # 마커 제거(임베딩/표시 오염 방지)
    return [c for c in chunks if c.text]
```
- **병합 허용**: 청크가 페이지 경계를 넘을 수 있고, 그 청크는 **시작 페이지**로 태깅(예: 4~5p 걸친 청크 → page=4). 결정성보다 청크 품질 우선.
- **`current_page` 이월**: 마커가 청크 끝에 걸려도 다음 청크가 그 페이지를 시작으로 받음(정확).
- 마커 없는 md(`_xlsx.md`·일반 md) → markers 없음 → `page` 미태깅 = **현행과 100% 동일** 동작.
- 마커는 자체 줄(`\n<!--page=N-->\n`)이라 헤더 split·recursive split 에 안 깨지고, 청크 사이즈에 ~13자만 더함(무시 가능).
- **블록분할이 없어** `section_index` 는 기존 단일 `enumerate` 카운터 그대로(별도 처리 불필요). `page` 는 청킹 후 후처리로 부여하므로 기존 `_chunk_text` 의 extra_meta(section_header/level/index)는 무변경.

## 4. 변경 파일 / 단계

| 파일 | 변경 | 배포 |
|---|---|---|
| `wellbot/services/files/file_parser.py` | `ParsedDocument.pages` 필드 + `_call_api` pages 빌드 + `_parse_pdf_split` 오프셋 | 앱 |
| `wellbot/services/knowledgebase/kb_utils.py` | `convert_pdf_to_markdown` 마커 md 조립 | 앱 |
| `scripts/transform_lambda.py` | `parse_md` 본문을 `_parse_md_sections` 로 추출 + 청크 후처리(시작 페이지 태깅 + 마커 제거, 병합 허용) | **Lambda 재배포(수동 zip)** |

첨부 경로(attachment_service)·표시 측(retriever~message_bubble)·xlsx 변환 **무변경**.

## 5. 리스크 / 롤백

- **Lambda 재배포(수동 zip 콘솔 업로드)** — 주된 운영 리스크. 배포는 사용자가 직접. 배포 전후 기능 검증 필요.
- **aggregate vs element md 차이**: KB 마커 md 는 element markdown 을 페이지별로 이어붙인 것이라 현재 aggregate `.text` 와 미세하게 다를 수 있음 → 색인용 중간 산출물이라 사용자 노출 없음, 허용.
- **마커 충돌**: 본문에 우연히 `<!--page=\d+-->` 가 있을 가능성 → HTML 주석이라 사실상 없음. 방어적으로 정규식 엄격히.
- **page 누락 element**: 일부 element 에 page 없으면 그 element 만 제외(텍스트는 인접 페이지에 흡수되거나 누락) — 샘플상 모두 int 라 실위험 낮음.
- **롤백**: 코드 롤백 + 직전 Lambda zip 재업로드. 표시 측은 page 없으면 자동 미표시라 안전.

## 6. 검증

1. 코드 변경 후 `py_compile` + 샘플 PDF 로 `convert_pdf_to_markdown` 단위 확인(`<!--page=N-->` 삽입, 분할 PDF 오프셋).
2. **Lambda 재배포(수동)** 후 샘플 PDF 를 KB 업로드 → ingestion 완료 대기.
3. 챗봇에서 해당 PDF 내용 질의 → **출처 칩에 page 표시** 확인 (Upstage 경로).
4. (선택) `PDF_VIA_UPSTAGE=False` 로 동일 PDF → pdfplumber 경로도 page 표시되는지 회귀 확인.
5. 분할 대상(대용량/100p 초과) PDF 로 **글로벌 페이지 번호 연속성** 확인.

## 7. 참고

- 표시 측은 이미 완성: `kb_retriever._coerce_page` → source_docs `page` → `pages_display`(인용 `[N]` 필터와 결합) → message_bubble.
- page 정확도: pdfplumber=PDF 물리 페이지, Upstage=element `page`(물리 페이지). 두 경로 모두 물리 페이지 번호로 일관.
