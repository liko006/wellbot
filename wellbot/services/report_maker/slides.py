"""보고서 아웃라인 마크다운 → 결정적 무손실 트리 파서 (슬라이드 렌더용).

report_maker 최종 아웃라인(build.finalize_outline 결과)은 사실상 '슬라이드 DSL' 이다:

    # {문서 제목}
    ## 보고서 개요                       → 문단
    ## [전체 TITLE]                      → (다음 줄) 표지 제목
    ## [전체 governing 메시지]           → (다음 줄) 전체 결론
    ## [N 페이지] {제목}                 / **[N 페이지 governing]** {결론}
    ### 좌측:/우측:/상단:/하단: {제목}   / **좌측 governing:** {결론}
    □ 대분류 / - 중항목 / · 세부

이 파서는 **LLM 없이** 위 구조를 위계 트리로 **무손실** 변환한다(내용 재생성 0).
레벨은 들여쓰기 공백 수가 아니라 **마커 문자(□/-/·)** 로 판정해 형식 변동에 견고하다.
레이아웃은 영역 헤더 조합으로 결정적 추론(좌·우=two_col, 상·하=two_row, 그 외 single).

설계 계약(b2): 이 트리가 유일한 '내용' 소스다. LLM 은 이후 단계에서 영역별
컴포넌트 타입({영역 → sections|timeline|table|...})만 태깅하며 내용에는 관여하지 않는다.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Item:
    """중항목(-) 하나와 그 세부(·) 목록."""
    text: str
    sub: list[str] = field(default_factory=list)


@dataclass
class Box:
    """대분류(□) 하나와 그 중항목 목록."""
    head: str
    items: list[Item] = field(default_factory=list)


@dataclass
class Area:
    """영역(좌측/우측/상단/하단, single 이면 "")."""
    side: str
    title: str
    governing: str = ""
    boxes: list[Box] = field(default_factory=list)


@dataclass
class Page:
    no: int
    title: str
    governing: str = ""
    layout: str = "single"        # two_col | two_row | single
    areas: list[Area] = field(default_factory=list)


@dataclass
class Outline:
    doc_title: str = ""           # 최상단 H1(주제)
    overview: str = ""            # 보고서 개요 문단
    overall_title: str = ""       # [전체 TITLE](표지 제목)
    overall_governing: str = ""   # [전체 governing 메시지]
    pages: list[Page] = field(default_factory=list)


_RE_PAGE = re.compile(r"^##\s*\[\s*(\d+)\s*페이지\s*\]\s*(.*)$")
_RE_PAGE_GOV = re.compile(r"^\*\*\s*\[\s*\d+\s*페이지\s*governing\s*\]\s*\*\*\s*(.*)$")
_RE_AREA = re.compile(r"^###\s*(좌측|우측|상단|하단)\s*[:：]\s*(.*)$")
_RE_AREA_GOV = re.compile(r"^\*\*\s*(좌측|우측|상단|하단)\s*governing\s*[:：]\s*\*\*\s*(.*)$")
_RE_OVERVIEW = re.compile(r"^##\s*보고서\s*개요\s*$")
_RE_OALL_TITLE = re.compile(r"^##\s*\[?\s*전체\s*TITLE\s*\]?\s*$")
_RE_OALL_GOV = re.compile(r"^##\s*\[?\s*전체\s*governing[^\]]*\]?\s*$")
_RE_H1 = re.compile(r"^#\s+(.+?)\s*$")
_RE_SEP = re.compile(r"^\s*-{3,}\s*$")

_L2_MARKERS = "-–"      # 중항목
_L3_MARKERS = "·•"      # 세부


def _clean(s: str) -> str:
    """마크다운 볼드 제거 + 공백 정리."""
    return s.replace("**", "").strip()


def parse_outline(md: str) -> Outline:
    """아웃라인 마크다운을 무손실 트리(Outline)로 변환한다."""
    out = Outline()
    page: Page | None = None
    area: Area | None = None
    box: Box | None = None
    item: Item | None = None
    capture: str | None = None   # "overview" | "overall_title" | "overall_governing"

    def ensure_area() -> Area | None:
        """single 페이지 등 영역 헤더가 없을 때 기본 영역을 만들어 박스를 담는다."""
        nonlocal area
        if area is None and page is not None:
            area = Area(side="", title="")
            page.areas.append(area)
        return area

    for raw in (md or "").split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if _RE_SEP.match(line):
            capture = None
            continue

        m = _RE_PAGE.match(line)
        if m:
            capture = None
            page = Page(no=int(m.group(1)), title=_clean(m.group(2)))
            out.pages.append(page)
            area = box = item = None
            continue
        m = _RE_PAGE_GOV.match(line)
        if m:
            if page is not None:
                page.governing = _clean(m.group(1))
            continue
        m = _RE_AREA.match(line)
        if m:
            capture = None
            area = Area(side=m.group(1), title=_clean(m.group(2)))
            if page is not None:
                page.areas.append(area)
            box = item = None
            continue
        m = _RE_AREA_GOV.match(line)
        if m:
            if area is not None:
                area.governing = _clean(m.group(2))
            continue

        if _RE_OVERVIEW.match(line):
            capture = "overview"
            continue
        if _RE_OALL_TITLE.match(line):
            capture = "overall_title"
            continue
        if _RE_OALL_GOV.match(line):
            capture = "overall_governing"
            continue
        m = _RE_H1.match(line)
        if m and not line.startswith("##"):
            if not out.doc_title:
                out.doc_title = _clean(m.group(1))
            continue

        # ── 위계 마커 ──
        if stripped.startswith("□"):
            capture = None
            box = Box(head=_clean(stripped[1:]))
            item = None
            a = ensure_area()
            if a is not None:
                a.boxes.append(box)
            continue
        if stripped[0] in _L2_MARKERS:      # separator 는 위에서 이미 걸러짐
            capture = None
            item = Item(text=_clean(stripped[1:]))
            if box is not None:
                box.items.append(item)
            continue
        if stripped[0] in _L3_MARKERS:
            capture = None
            if item is not None:
                item.sub.append(_clean(stripped[1:]))
            continue

        # ── report-level 텍스트 캡처(헤더 다음 줄) ──
        if capture == "overview":
            out.overview = (out.overview + " " + stripped).strip() if out.overview else stripped
        elif capture == "overall_title":
            out.overall_title = _clean(stripped)
            capture = None
        elif capture == "overall_governing":
            out.overall_governing = _clean(stripped)
            capture = None

    for p in out.pages:
        sides = {a.side for a in p.areas}
        if "좌측" in sides and "우측" in sides:
            p.layout = "two_col"
        elif "상단" in sides and "하단" in sides:
            p.layout = "two_row"
        else:
            p.layout = "single"

    return out


# ──────────────────────────────────────────────────────────────
# 컴포넌트 타입 태깅 (b2: LLM 은 '무엇을 어떤 컴포넌트로' 만 결정, 내용 X)
# ──────────────────────────────────────────────────────────────
COMPONENTS = ("sections", "timeline")   # 렌더러가 아는 무손실 컴포넌트 어휘

_RE_PHASE = re.compile(r"^\s*(M\s*\d|Step\s*\d|Phase\s*\d|\d+\s*단계|W\s*\d)", re.IGNORECASE)


def _heuristic_component(area: "Area") -> str:
    """폴백 휴리스틱 — 박스 머리가 대부분 단계(M1/W1/1단계…)면 timeline, 그 외 sections.

    LLM 태깅이 우선이며, 이는 LLM 미가용(오프라인/실패) 시에만 쓰는 안전 폴백이다.
    내용을 바꾸지 않으므로(레이아웃 판정만) 오탐이 나도 무손실은 유지된다.
    """
    if len(area.boxes) >= 2:
        phase_like = sum(1 for b in area.boxes if _RE_PHASE.match(b.head))
        if phase_like >= max(2, len(area.boxes) - 1):
            return "timeline"
    return "sections"


def suggest_component_tags(outline: "Outline") -> dict[tuple[int, str], str]:
    """영역별 컴포넌트 타입 {(page_no, area_title): component} 를 반환.

    1순위: LLM(bedrock.call_json) 이 영역 요약(제목·박스 머리)만 보고 컴포넌트 선택.
           내용 텍스트 전체는 넘기지 않아 토큰이 작고, 사실 변형 위험이 없다.
    2순위: 실패/미가용 시 결정적 휴리스틱(_heuristic_component)로 폴백.
    """
    areas = [(p.no, a) for p in outline.pages for a in p.areas]
    tags: dict[tuple[int, str], str] = {}
    # 폴백 기본값 선채우기
    for no, a in areas:
        tags[(no, a.title)] = _heuristic_component(a)

    try:
        from wellbot.services.report_maker import bedrock
        from wellbot.services.report_maker.config import get_config

        listing = "\n".join(
            f"- id={i} | 페이지{no} | 영역='{a.title}' | 항목: "
            + " / ".join(b.head[:40] for b in a.boxes)
            for i, (no, a) in enumerate(areas)
        )
        prompt = (
            "다음은 보고서 슬라이드의 영역 목록이다. 각 영역을 어떤 시각 컴포넌트로 그릴지 "
            f"{list(COMPONENTS)} 중에서 하나씩 고르라. 내용은 바꾸지 말고 '레이아웃 타입'만 판단한다.\n"
            "- timeline: 항목들이 시간/단계 순서(M1→M2, 1단계→2단계, 주차 등)로 이어질 때\n"
            "- sections: 그 외 일반 항목 나열\n\n"
            f"{listing}\n\n"
            'JSON 으로만 답하라: {"tags":[{"id":0,"component":"sections"}, ...]}'
        )
        result = bedrock.call_json(prompt, get_config().max_tokens_style, action="slide_tag")
        for row in (result.get("tags") or []):
            i = row.get("id")
            comp = row.get("component")
            if isinstance(i, int) and 0 <= i < len(areas) and comp in COMPONENTS:
                no, a = areas[i]
                tags[(no, a.title)] = comp
    except Exception:
        log.exception("슬라이드 컴포넌트 태깅(LLM) 실패 — 휴리스틱 폴백 사용")

    return tags


# ──────────────────────────────────────────────────────────────
# 렌더러 (트리 + 태그 → SK 브랜드 HTML, 무손실). 잠긴 브랜드/포맷.
# ──────────────────────────────────────────────────────────────
def _esc(s: str) -> str:
    return html.escape(s or "")


def _items_html(items: list["Item"]) -> str:
    if not items:
        return ""
    lis = []
    for it in items:
        sub = ""
        if it.sub:
            sub = '<ul class="l3">' + "".join(f"<li>{_esc(s)}</li>" for s in it.sub) + "</ul>"
        lis.append(f"<li>{_esc(it.text)}{sub}</li>")
    return '<ul class="l2">' + "".join(lis) + "</ul>"


def _render_area(area: "Area", component: str) -> str:
    if component == "timeline":
        body = '<div class="tl">' + "".join(
            f'<div class="ph"><div class="ph-t">{_esc(b.head)}</div>{_items_html(b.items)}</div>'
            for b in area.boxes
        ) + "</div>"
    else:  # sections (기본·폴백)
        body = "".join(
            f'<div class="box"><div class="h">{_esc(b.head)}</div>{_items_html(b.items)}</div>'
            for b in area.boxes
        )
    gov = f'<div class="agov">{_esc(area.governing)}</div>' if area.governing else ""
    title = f'<div class="atitle">{_esc(area.title)}</div>' if area.title else ""
    return f'<div class="col">{title}{gov}<div class="fitbox">{body}</div></div>'


def _render_page(page: "Page", report_title: str, total: int, tags: dict) -> str:
    cols = "1fr" if page.layout == "single" else "1fr 1fr"
    gov = f'<div class="pgov">{_esc(page.governing)}</div>' if page.governing else ""
    areas = "".join(
        _render_area(a, tags.get((page.no, a.title), "sections")) for a in page.areas
    )
    return (
        '<section class="slide">'
        '<div class="top">'
        f'<div><div class="rtitle">{_esc(report_title)}</div>'
        f'<div class="ptitle">{_esc(page.title)}</div></div>'
        f'<span class="pageno">{page.no} / {total}</span></div>'
        f"{gov}"
        f'<div class="cols" style="grid-template-columns:{cols}">{areas}</div>'
        '<div class="fitnote"></div></section>'
    )


def _cover_slide(outline: "Outline", report_title: str) -> str:
    if not (outline.overall_title or outline.overview):
        return ""
    gov = f'<div class="cov-gov">{_esc(outline.overall_governing)}</div>' if outline.overall_governing else ""
    ov = f'<div class="cov-ov">{_esc(outline.overview)}</div>' if outline.overview else ""
    return (
        '<section class="slide cover"><div class="cov-wrap">'
        f'<div class="cov-title">{_esc(report_title)}</div>{gov}{ov}'
        "</div></section>"
    )


def render_html(outline: "Outline", tags: dict | None = None) -> str:
    """트리(+컴포넌트 태그) → 자체 완결 HTML 문서(iframe srcdoc 용). 내용 무손실."""
    tags = tags or {}
    report_title = outline.overall_title or outline.doc_title or "보고서"
    total = len(outline.pages)
    body = _cover_slide(outline, report_title) + "".join(
        _render_page(p, report_title, total, tags) for p in outline.pages
    )
    return _HTML_SHELL.replace("{{BODY}}", body)


def build_deck(md: str, tags: dict | None = None) -> str:
    """편의 함수: 마크다운 → 파싱 → 렌더(HTML). tags 미지정 시 sections 기본."""
    return render_html(parse_outline(md), tags)


# ──────────────────────────────────────────────────────────────
# LLM 전체 생성 (레이아웃·시각화를 LLM 이 판단, 브랜드 셸로 감싸 잠금)
# ──────────────────────────────────────────────────────────────
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n|\n```\s*$")


def _strip_fences(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n", "", s)
        s = re.sub(r"\n```\s*$", "", s)
    return s.strip()


_SLIDE_DESIGN_PROMPT = """당신은 SK 임원 보고 장표 디자이너다. 아래 보고서 아웃라인을 16:9 슬라이드 HTML 로 변환하라.

[절대 규칙 — 내용 보존]
- 아웃라인의 모든 사실·수치·문구를 그대로 쓴다. 추가·삭제·왜곡·요약 금지(누락 0). □/-/· 항목을 빠짐없이 담는다.
- 페이지 구분(## [N 페이지])을 그대로 슬라이드로 나눈다. 각 페이지는 <section class="slide"> 하나.

[레이아웃 — 여기가 핵심]
- 페이지 내용에 맞게 좌우 2단 / 상하 2단 / 단일을 스스로 판단해 배치한다.
- 좌측/우측(상단/하단) 영역이 있으면 <div class="cols" style="grid-template-columns:1fr 1fr"> 로 2단 구성하고 각 영역을 <div class="col"> 로 감싼다.
- 표·타임라인·카드·KPI 등 시각 요소를 내용에 맞게 자유롭게 쓰되 임원 보고답게 절제한다.

[브랜드/포맷 — 아래 클래스를 쓰면 SK 톤(레드 #EA002C·오렌지 #F47725)이 자동 적용된다. 직접 색 지정은 자제]
- 상단바: <div class="top"><div><div class="rtitle">보고서 제목</div><div class="ptitle">페이지 제목</div></div><span class="pageno">N / 총</span></div>
- 페이지 결론 바: <div class="pgov">페이지 governing</div>
- 본문: <div class="cols" style="grid-template-columns:1fr 1fr"><div class="col"><div class="atitle">영역 제목</div><div class="agov">영역 governing</div> …블록… </div> …</div>
- 대분류 카드: <div class="box"><div class="h">□ 제목</div><ul class="l2"><li>중항목<ul class="l3"><li>세부</li></ul></li></ul></div>
- 타임라인: <div class="tl"><div class="ph"><div class="ph-t">단계</div><ul class="l2">…</ul></div></div>
- 표: <table class="exec"><thead><tr><th>…</th></tr></thead><tbody><tr><td>…</td></tr></tbody></table>
- KPI: <div class="kpis"><div class="kpi"><span class="v">값</span><span class="l">라벨</span></div></div>
- 표지(선택): <section class="slide cover"><div class="cov-wrap"><div class="cov-title">제목</div><div class="cov-gov">전체 governing</div></div></section>

[출력]
- <section class="slide"> 들만 출력한다. <html>/<head>/<style>/<script> 는 쓰지 마라(브랜드 셸이 자동 제공됨).
- 코드펜스·설명·주석 없이 HTML 만 출력한다.

[아웃라인]
{content}
"""


def render_html_llm(md: str) -> str:
    """LLM 이 슬라이드 본문(<section>들)을 생성 → 브랜드 셸로 감싼 완결 HTML.

    레이아웃(좌우/상하/단일)·시각화는 LLM 판단, 브랜드 CSS·fit·문서 유효성은 셸이 보장.
    실패/비정상 출력 시 예외를 던져 호출측이 결정적 렌더로 폴백하게 한다.
    """
    from wellbot.services.report_maker import bedrock
    from wellbot.services.report_maker.config import get_config

    prompt = _SLIDE_DESIGN_PROMPT.replace("{content}", md or "")
    body = _strip_fences(bedrock.call_model(prompt, get_config().max_tokens_outline, action="slide"))
    if "<section" not in body:
        raise ValueError("LLM 슬라이드 출력에 <section> 이 없음")
    return _HTML_SHELL.replace("{{BODY}}", body)


# ── 잠긴 브랜드 셸(SK 톤 CSS + 가독성 하한 fit 스크립트) ──
_HTML_SHELL = """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<style>
:root{--sk-red:#EA002C;--sk-orange:#F47725;--sk-red-d:#B80022;--ink:#20242b;--ink-2:#4a5261;
--ink-3:#828b99;--line:#e6e9ef;--soft:#f6f7f9;--grad:linear-gradient(100deg,var(--sk-red),var(--sk-orange));}
*{box-sizing:border-box;}
body{margin:0;background:#e9ebef;color:var(--ink);padding:22px 0;
font-family:"Pretendard","Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif;}
.slide{width:1280px;height:720px;margin:0 auto 26px;background:#fff;border:1px solid var(--line);
border-radius:12px;overflow:hidden;box-shadow:0 12px 34px rgba(32,36,43,.12);display:flex;flex-direction:column;}
.top{display:flex;align-items:center;gap:14px;padding:15px 28px;border-bottom:1px solid var(--line);}
.top .rtitle{font-size:13.5px;color:var(--ink-3);font-weight:600;}
.top .ptitle{font-size:19px;font-weight:800;letter-spacing:-.2px;}
.top .pageno{margin-left:auto;font-size:12px;color:#fff;background:var(--grad);border-radius:20px;padding:4px 13px;font-weight:700;}
.pgov{margin:15px 28px 4px;padding:12px 16px 12px 18px;position:relative;background:var(--soft);
border-radius:9px;font-weight:800;font-size:15px;color:#2a2f38;}
.pgov::before{content:"";position:absolute;left:0;top:8px;bottom:8px;width:5px;border-radius:5px;background:var(--grad);}
.cols{display:grid;gap:16px;flex:1;min-height:0;padding:12px 28px 8px;}
.col{display:flex;flex-direction:column;min-height:0;overflow:hidden;}
.atitle{font-size:14.5px;font-weight:800;color:var(--sk-red-d);margin:0 0 3px;display:flex;align-items:center;gap:7px;}
.atitle::before{content:"";width:16px;height:3px;border-radius:3px;background:var(--grad);}
.agov{font-size:11.5px;color:var(--ink-2);margin:0 0 9px;}
.fitbox{transform-origin:top left;}
.box{border:1px solid var(--line);border-radius:9px;padding:8px 12px 9px;margin-bottom:9px;background:#fff;}
.box>.h{font-weight:800;font-size:12.5px;color:var(--ink);line-height:1.4;display:flex;gap:7px;}
.box>.h::before{content:"";flex:0 0 auto;width:6px;height:6px;border-radius:50%;background:var(--grad);margin-top:5px;}
ul.l2{list-style:none;margin:6px 0 0;padding:0;}
ul.l2>li{position:relative;padding-left:13px;margin:4px 0;font-size:11.5px;color:var(--ink-2);line-height:1.5;}
ul.l2>li::before{content:"\\2013";position:absolute;left:0;color:var(--ink-3);}
ul.l3{list-style:none;margin:2px 0 2px;padding:0;}
ul.l3>li{position:relative;padding-left:13px;margin:1px 0;font-size:11px;color:var(--ink-3);line-height:1.45;}
ul.l3>li::before{content:"\\00B7";position:absolute;left:3px;color:#a7b0be;font-weight:700;}
.tl{position:relative;padding-left:24px;}
.tl::before{content:"";position:absolute;left:7px;top:4px;bottom:4px;width:2px;background:linear-gradient(var(--sk-red),var(--sk-orange));}
.ph{position:relative;margin-bottom:11px;}
.ph::before{content:"";position:absolute;left:-21px;top:2px;width:13px;height:13px;border-radius:50%;background:#fff;border:3px solid var(--sk-red);}
.ph .ph-t{font-weight:800;font-size:12.5px;color:var(--ink);margin-bottom:4px;line-height:1.4;}
.fitnote{font-size:10.5px;color:var(--ink-3);padding:0 28px 8px;}
.cover .cov-wrap{margin:auto;max-width:900px;padding:0 60px;}
.cover .cov-title{font-size:34px;font-weight:900;letter-spacing:-.5px;margin:18px 0 14px;
background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent;}
.cover .cov-gov{font-size:16px;font-weight:700;color:#2a2f38;margin-bottom:16px;line-height:1.5;}
.cover .cov-ov{font-size:13px;color:var(--ink-2);line-height:1.7;}
.dl-bar{position:fixed;top:14px;right:18px;z-index:9999;display:flex;gap:8px;}
.dl-bar button{font-size:12px;font-weight:700;color:#fff;background:var(--grad);border:none;
  border-radius:8px;padding:7px 13px;cursor:pointer;box-shadow:0 3px 10px rgba(234,0,44,.25);}
.dl-bar button.ghost{background:#fff;color:var(--ink-2);border:1px solid var(--line);box-shadow:none;}
@media print{
  body{background:#fff;padding:0;}
  .dl-bar,.fitnote{display:none !important;}
  .slide{box-shadow:none;border:none;border-radius:0;margin:0 auto;break-after:page;page-break-after:always;}
}
@page{size:1280px 720px;margin:0;}
</style></head><body>
<div class="dl-bar">
  <button onclick="rmSaveHtml()">다운로드</button>
</div>
{{BODY}}
<script>
// 자체 완결 HTML 다운로드(현재 문서 그대로) — Blob 저장
function rmSaveHtml(){
  var html="<!DOCTYPE html>\\n"+document.documentElement.outerHTML;
  var blob=new Blob([html],{type:"text/html;charset=utf-8"});
  var a=document.createElement("a");
  a.href=URL.createObjectURL(blob); a.download="report_slides.html";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(function(){URL.revokeObjectURL(a.href);},1500);
}
// 가독성 하한 fit: 작은 초과만 미세 축소, 큰 초과는 축소 대신 '분할 대상' 표시
var MIN=0.9;
requestAnimationFrame(function(){
  document.querySelectorAll(".col").forEach(function(col){
    var fb=col.querySelector(".fitbox"); if(!fb) return;
    var avail=col.clientHeight-fb.offsetTop, need=fb.scrollHeight;
    if(need<=avail) return;
    var s=avail/need, note=col.closest(".slide").querySelector(".fitnote");
    if(s>=MIN){fb.style.transform="scale("+s+")";fb.style.width=(100/s)+"%";}
    else{fb.style.transform="scale("+MIN+")";fb.style.width=(100/MIN)+"%";
      if(note) note.textContent="\\u203B \\uBD84\\uB7C9\\uC774 \\uD55C \\uC7A5\\uC744 \\uCD08\\uACFC \\u2014 \\uC5F0\\uC18D \\uC2AC\\uB77C\\uC774\\uB4DC \\uBD84\\uD560 \\uB300\\uC0C1";}
  });
});
</script></body></html>"""
