"""슬라이드 파서(slides.parse_outline) — 대표 아웃라인 픽스처로 무손실 검증.

핵심 계약: 결정적 파서가 최종 아웃라인 마크다운을 위계 트리로 '한 항목도 빠짐없이'
쪼갠다(내용은 이후 LLM 을 거치지 않음). 마커(□/-/·) 총량이 트리와 원문에서 일치하면
무손실이 보장된다.
"""

import re

from wellbot.services.report_maker import slides

# 실제 산출물과 동일한 위계·마커 구조를 갖는 가상 아웃라인(2 페이지 구조 중 1 페이지).
# 고객사·제품 등 식별 정보는 A사 / P1 / 분야N / 선도사N 으로 일반화했다.
REAL = """# 통합 데이터 플랫폼 구축 보고서

## 보고서 개요
본 보고서는 A사의 통합 데이터 플랫폼 전환에 따른 데이터 허브 구축 필요성과 추진 방안을 담고 있습니다.

     ---

## [전체 TITLE]
통합 데이터 플랫폼 데이터 허브 구축 전략

## [전체 governing 메시지]
데이터 전문 인력 투입 및 데이터 라이프사이클 관리체계 수립으로 6대 서비스 BM 구체화 기반 마련

     ---

## [1 페이지] 추진 배경 및 전체 로드맵

**[1 페이지 governing]** 10주 프로젝트로 BM 구체화와 데이터 관리 정책 수립 병행 추진

### 좌측: 추진 배경 및 필요성

**좌측 governing:** 신제품 출시로 플랫폼 전환 가속, 현행 데이터 관리 한계 해소 필요

□ **통합 데이터 플랫폼 전환 목적 및 비전 확립**
     - 신제품 P1 출시로 사업 방향성이 통합 데이터 플랫폼으로 전환
          · 단일 제품 중심에서 통합 서비스 생태계로 사업모델 확장
          · 6대 서비스 분야(분야1, 분야2, 분야3, 분야4, 분야5, 분야6) 기반 플랫폼 구축 방향 설정
□ **6대 서비스 분야 BM 및 핵심 Data 정의 필요성 확보**
     - 기정의된 BM 검토 및 필요 Data 항목 정의·보유수준 파악 필요
          · 6대 서비스별 BM 개발 현황 및 계획 전면 재검토
          · BM별 필요 데이터 확보 현황 및 Gap 분석 추진
     - 시장성·차별성·실행용이성·사업확장성·리스크 5대 평가 기준 기반 우선순위 선정
          · 런칭 우선순위 기반 타겟고객·Value Proposition 구체화
          · 서비스 시나리오 기준 필요 데이터 상세화 및 중요도 평가
□ **현행 Data 관리 Pain Point 해소 통한 데이터 활용 체계 구축 기반 마련**
     - 기존 분석계는 일부 데이터만 ETL 중, 신규 P1 데이터와 주문 데이터 결합 불가
          · 기간계·IoT 허브 데이터 부분 연계로 통합 분석 한계 직면
          · P1 디바이스 데이터와 고객 주문·행동 데이터 간 연결 체계 부재
     - 데이터 수집·분석·활용·보관/폐기 단계별 Pain Point 도출 및 개선 방향 수립 필요
          · 데이터 라이프사이클 전 단계 관리 정책 재정립
          · 식별/비식별 데이터 통합 방안 및 컴플라이언스 대응 체계 확립

### 우측: 전체 추진 로드맵

**우측 governing:** 10주 3단계 구조로 BM 평가·데이터 정의·관리체계 순차 완성

□ **M1 (W1～W3): 사업 현황분석 및 선도사 벤치마킹 완료**
     - A사 중장기 사업전략 및 플랫폼 전환 목적·비전 분석
          · 경영진 인터뷰 및 전략문서 기반 추진 현황 파악
          · 플랫폼 관련 조직 구성 및 R&R 검토
     - 선도사(선도사1, 선도사2, 선도사3 등) BM 및 주요 데이터 활용 현황 조사
          · 선도사별 데이터 수집/활용 방식 및 수익모델 연계 분석
          · 선도사 데이터 관리체계(분석 인프라·관리 정책) 벤치마킹
□ **M2 (W4～W6): 6대 서비스별 BM 평가·구체화 및 중간보고**
     - 6대 서비스 분야별 BM 서비스 경쟁력 분석 및 평가
          · 시장성·차별성·실행용이성·사업확장성·리스크 5대 기준 종합 평가
          · 우선 추진 BM 선정 및 런칭 우선순위 확정
     - 런칭 우선순위 높은 BM 대상 정교화 추진
          · 타겟고객 세그먼트·Value Proposition·서비스 시나리오 설계
          · 핵심 활동·필요 자원(예측모델 개발·콘텐츠 수급 등) 정의
     - 중간보고 (W4～W5)
          · BM 구체화 결과 및 핵심 데이터 정의 방향 보고
□ **M3 (W7～W10): 핵심 Data 정의, 소싱 전략, 관리 정책 수립 및 완료보고**
     - BM별 핵심 데이터 정의 및 확보 우선순위 선정 (W7～W8)
          · 서비스 시나리오 기준 필요 데이터 도출·상세화
          · 데이터 중요도(BM 연계성·희소성·분석/활용 적합성) 평가
     - 외부 데이터 소싱 전략 수립 (W7～W8)
          · 공공데이터·데이터구매·데이터제휴 옵션별 비용·품질·리스크 평가
          · 3rd Party 파트너 Pool 정의 및 협업 방식 검토
     - 데이터 관리 정책 수립 (W1～W10 병행)
          · 데이터 특성 분류 체계 및 등급화(개인식별성·민감도·활용시점·구조·품질)
          · 라이프사이클 관리 정책(수집～폐기) 및 컴플라이언스 대응 방안
     - 데이터 허브 구축 방안 제시 및 완료보고 (W9～W10)
          · 기정의 논리 아키텍처 기반 구축 요건 Mapping
          · Data Hub 구축 체크리스트 및 로드맵 제시
          · PoC 후보 과제 정의 및 수행계획 수립
"""


def _count_raw_markers(md: str) -> tuple[int, int, int]:
    """원문에서 □/-(중)/·(세부) 라인 수를 센다(separator 제외)."""
    box = item = sub = 0
    for raw in md.split("\n"):
        s = raw.strip()
        if not s or re.match(r"^\s*-{3,}\s*$", raw):
            continue
        if s.startswith("□"):
            box += 1
        elif s[0] in "-–":
            item += 1
        elif s[0] in "·•":
            sub += 1
    return box, item, sub


def _tree_marker_counts(out) -> tuple[int, int, int]:
    boxes = [b for p in out.pages for a in p.areas for b in a.boxes]
    items = [it for b in boxes for it in b.items]
    subs = [s for it in items for s in it.sub]
    return len(boxes), len(items), len(subs)


def test_lossless_marker_counts():
    """원문 □/-/· 총량 == 트리 □/-/· 총량 → 무손실(누락 0)."""
    out = slides.parse_outline(REAL)
    assert _tree_marker_counts(out) == _count_raw_markers(REAL)


def test_report_level_preamble():
    out = slides.parse_outline(REAL)
    assert out.doc_title == "통합 데이터 플랫폼 구축 보고서"
    assert out.overall_title == "통합 데이터 플랫폼 데이터 허브 구축 전략"
    assert out.overall_governing.startswith("데이터 전문 인력 투입")
    assert "A사" in out.overview


def test_page_and_layout():
    out = slides.parse_outline(REAL)
    assert len(out.pages) == 1
    p = out.pages[0]
    assert p.no == 1
    assert p.title == "추진 배경 및 전체 로드맵"
    assert p.governing.startswith("10주 프로젝트")
    assert p.layout == "two_col"


def test_areas_and_boxes():
    out = slides.parse_outline(REAL)
    p = out.pages[0]
    assert [a.side for a in p.areas] == ["좌측", "우측"]
    left, right = p.areas
    assert left.title == "추진 배경 및 필요성"
    assert left.governing.startswith("신제품 출시로")
    assert len(left.boxes) == 3
    assert left.boxes[0].head == "통합 데이터 플랫폼 전환 목적 및 비전 확립"
    assert len(right.boxes) == 3
    assert right.boxes[0].head.startswith("M1 (W1")


def test_deep_content_preserved():
    """가장 깊은 · 세부까지 원문 그대로 보존되는지(무손실의 실질 확인)."""
    out = slides.parse_outline(REAL)
    left = out.pages[0].areas[0]
    box1 = left.boxes[0]
    assert box1.items[0].text.startswith("신제품 P1 출시로")
    assert box1.items[0].sub[1].startswith("6대 서비스 분야(분야1")


def test_render_html_is_lossless_and_branded():
    """렌더 HTML 에 모든 □ 머리 + 최심 · 세부가 그대로 담기고 SK 셸이 붙는다."""
    out = slides.parse_outline(REAL)
    doc = slides.render_html(out)
    # 표지·페이지 제목
    assert "통합 데이터 플랫폼 데이터 허브 구축 전략" in doc
    assert "추진 배경 및 전체 로드맵" in doc
    # 모든 □ 머리 포함
    for p in out.pages:
        for a in p.areas:
            for b in a.boxes:
                assert b.head in doc
    # 최심 세부(·)까지 보존
    assert "6대 서비스 분야(분야1" in doc
    assert "PoC 후보 과제 정의 및 수행계획 수립" in doc
    # 잠긴 브랜드 셸
    assert "--sk-red:#EA002C" in doc
    assert doc.strip().startswith("<!DOCTYPE html>")


def test_render_timeline_tag_switches_component():
    out = slides.parse_outline(REAL)
    p = out.pages[0]
    tags = {(p.no, "전체 추진 로드맵"): "timeline", (p.no, "추진 배경 및 필요성"): "sections"}
    doc = slides.render_html(out, tags)
    assert 'class="tl"' in doc      # timeline 컴포넌트 렌더됨
    assert 'class="ph"' in doc
    # 타임라인이어도 내용은 무손실
    assert "선도사(선도사1, 선도사2, 선도사3 등)" in doc


def test_heuristic_tags_roadmap_as_timeline():
    """LLM 미가용 폴백: M1/M2/M3 로드맵 영역은 timeline 으로 판정."""
    out = slides.parse_outline(REAL)
    left = out.pages[0].areas[0]   # 추진 배경 (일반) → sections
    right = out.pages[0].areas[1]  # 로드맵 (M1/M2/M3) → timeline
    assert slides._heuristic_component(left) == "sections"
    assert slides._heuristic_component(right) == "timeline"


def test_render_html_llm_wraps_body_and_strips_fences(monkeypatch):
    """LLM 이 낸 <section> 본문을 브랜드 셸로 감싸고 코드펜스는 제거한다."""
    from wellbot.services.report_maker import bedrock
    monkeypatch.setattr(
        bedrock, "call_model",
        lambda prompt, mt, **kw: '```html\n<section class="slide">본문</section>\n```',
    )
    doc = slides.render_html_llm("## [1 페이지] 제목\n□ **a**")
    assert doc.strip().startswith("<!DOCTYPE html>")
    assert "--sk-red:#EA002C" in doc                       # 브랜드 셸
    assert '<section class="slide">본문</section>' in doc   # 펜스 제거 + 본문 삽입


def test_render_html_llm_rejects_nonslide_output(monkeypatch):
    """LLM 이 <section> 없는 잡음을 내면 예외 → 호출측이 폴백하도록."""
    import pytest
    from wellbot.services.report_maker import bedrock
    monkeypatch.setattr(bedrock, "call_model", lambda prompt, mt, **kw: "설명만 있고 슬라이드 없음")
    with pytest.raises(ValueError):
        slides.render_html_llm("x")


def test_single_layout_without_area_headers():
    """single 페이지(영역 헤더 없음): 박스가 기본 영역에 무손실로 담긴다."""
    md = """## [1 페이지] 한 장 요약

**[1 페이지 governing]** 결론 한 줄

□ **핵심 과제 A**
     - 근거 1
          · 세부 1
□ **핵심 과제 B**
     - 근거 2
"""
    out = slides.parse_outline(md)
    p = out.pages[0]
    assert p.layout == "single"
    assert len(p.areas) == 1
    assert [b.head for b in p.areas[0].boxes] == ["핵심 과제 A", "핵심 과제 B"]
    assert _tree_marker_counts(out) == _count_raw_markers(md)
