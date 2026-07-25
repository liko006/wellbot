"""다운로드용 자체완결 HTML 리포트 생성.

원본 generate_html 을 이식하되, 모든 동적 텍스트에 html.escape 를 적용해
LLM/문서에서 온 내용이 HTML 로 주입되지 않도록 한다(원본 XSS/깨짐 버그 수정).
"""

from __future__ import annotations

import html
import re

from wellbot.services.report_checker.config import get_config
from wellbot.services.report_checker.models import AnalysisResult


def _esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _hl(context: str, target: str) -> str:
    """context 를 이스케이프한 뒤 target 부분만 <strong> 로 강조.

    이스케이프 후 매칭하므로 정규식 백레퍼런스/HTML 주입 위험이 없다.
    """
    esc_ctx = _esc(context)
    if not target:
        return esc_ctx
    esc_target = _esc(target)
    return re.sub(
        re.escape(esc_target),
        lambda m: f'<strong class="hl">{m.group(0)}</strong>',
        esc_ctx,
        flags=re.IGNORECASE,
    )


def generate_html(
    result: AnalysisResult,
    source_file: str,
    consistency_checked: bool = True,
    attention_checked: bool = False,
    notation_checked: bool = False,
) -> str:
    """분석 결과를 자체완결 HTML 문서 문자열로 렌더.

    consistency_checked=False 면 일관성 검사를 수행하지 않은 것으로 보고,
    0건 대신 '검사하지 않음'으로 표기한다.
    attention_checked=True 면 주의 항목 섹션을 함께 표시한다.
    """
    cfg = get_config()

    typo_rows = ""
    for e in result.typo_errors:
        ctx = _hl(e.context, e.original) if e.context else ""
        typo_rows += f"""
      <tr>
        <td><span class="pbadge">{_esc(e.page)}p</span></td>
        <td><strong class="err">{_esc(e.original)}</strong></td>
        <td class="cor">→ {_esc(e.correction)}</td>
        <td class="ctx">{ctx}</td>
      </tr>"""

    cons_rows = ""
    for e in result.consistency_errors:
        pages_str = " / ".join(
            f'<span class="pbadge orange">{_esc(p)}p</span>' for p in sorted(e.pages)
        )
        vals_html = " vs ".join(
            f'<strong class="val">{_esc(v)}</strong>' for v in e.values
        )
        cons_rows += f"""
      <tr>
        <td>{pages_str}</td>
        <td><span class="key-tag">{_esc(e.key)}</span><br><small>{vals_html}</small></td>
        <td>{_esc(e.inconsistent_content)}</td>
        <td class="reason">{_esc(e.reason)}</td>
      </tr>"""

    attn_rows = ""
    for e in result.attention_errors:
        attn_rows += f"""
      <tr>
        <td><span class="pbadge green">{_esc(e.page)}p</span></td>
        <td><span class="key-tag green">{_esc(e.rule)}</span></td>
        <td class="ctx">{_esc(e.excerpt)}</td>
        <td class="reason">{_esc(e.issue)}</td>
      </tr>"""

    notation_rows = ""
    for e in result.notation_errors:
        vlist = " · ".join(
            f'<strong class="val">{_esc(v["form"])}</strong>'
            f'<small> ({", ".join(f"{_esc(p)}p" for p in v["pages"])})</small>'
            for v in e.variants
        )
        notation_rows += f"""
      <tr>
        <td><span class="key-tag">{_esc(e.concept)}</span></td>
        <td>{vlist}</td>
      </tr>"""

    tc, cc = len(result.typo_errors), len(result.consistency_errors)
    ac = len(result.attention_errors)
    ntc = len(result.notation_errors)
    src = _esc(source_file)
    model = _esc(cfg.model_id)

    empty_typo = '<p class="empty"><em>✓</em>오탈자가 발견되지 않았습니다.</p>'
    typo_table = f"""
  <table>
    <thead><tr><th>페이지</th><th>원문 (오류)</th><th>교정</th><th>문맥</th></tr></thead>
    <tbody>{typo_rows}</tbody>
  </table>"""

    empty_cons = '<p class="empty"><em>✓</em>수치·기술 오류가 발견되지 않았습니다.</p>'
    skipped_cons = '<p class="empty"><em>—</em>일관성 검사를 실행하지 않았습니다.</p>'
    cons_table = f"""
  <table>
    <thead><tr><th>페이지</th><th>항목 / 충돌 값</th><th>불일치 내용</th><th>교정 필요 사유</th></tr></thead>
    <tbody>{cons_rows}</tbody>
  </table>"""
    if not consistency_checked:
        cons_body = skipped_cons
        cc_stat = "—"
        cc_badge = "미검사"
    else:
        cons_body = empty_cons if not result.consistency_errors else cons_table
        cc_stat = str(cc)
        cc_badge = f"{cc}건"

    # 표기 일관성 섹션 (선택 시에만 표시)
    if notation_checked:
        empty_notation = '<p class="empty"><em>✓</em>표기 불일치가 발견되지 않았습니다.</p>'
        notation_table = f"""
  <table>
    <thead><tr><th>개념</th><th>표기 변형 (페이지)</th></tr></thead>
    <tbody>{notation_rows}</tbody>
  </table>"""
        notation_body = empty_notation if not result.notation_errors else notation_table
        notation_stat_html = f'<div class="sc p"><div class="n">{ntc}</div><div class="l">표기 불일치</div></div>'
        notation_section_html = (
            f'<section><h2>🔤 표기 일관성 <span class="badge p">{ntc}건</span></h2>{notation_body}</section>'
        )
    else:
        notation_stat_html = ""
        notation_section_html = ""

    # 주의 항목 섹션 (사용자 규칙이 있을 때만 표시)
    if attention_checked:
        empty_attn = '<p class="empty"><em>✓</em>주의 항목 위반이 발견되지 않았습니다.</p>'
        attn_table = f"""
  <table>
    <thead><tr><th>페이지</th><th>규칙</th><th>발췌</th><th>위반 내용</th></tr></thead>
    <tbody>{attn_rows}</tbody>
  </table>"""
        attn_body = empty_attn if not result.attention_errors else attn_table
        attn_stat_html = f'<div class="sc g"><div class="n">{ac}</div><div class="l">주의 항목</div></div>'
        attn_section_html = (
            f'<section><h2>🔎 주의 항목 <span class="badge g">{ac}건</span></h2>{attn_body}</section>'
        )
    else:
        attn_stat_html = ""
        attn_section_html = ""

    total_errors = tc + cc + (ac if attention_checked else 0) + (ntc if notation_checked else 0)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>보고서 오류 탐지 결과</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:#f0f2f8;color:#1a202c;font-size:14px}}
header{{background:linear-gradient(135deg,#2b4fff 0%,#6a00f4 100%);color:#fff;padding:28px 36px}}
header h1{{font-size:1.5rem;font-weight:800;letter-spacing:-.5px}}
header p{{margin-top:6px;opacity:.75;font-size:.85rem}}
.stats{{display:flex;gap:14px;padding:20px 36px;flex-wrap:wrap}}
.sc{{background:#fff;border-radius:14px;padding:16px 22px;min-width:160px;
     box-shadow:0 2px 8px rgba(0,0,0,.07);flex:1}}
.sc .n{{font-size:2.2rem;font-weight:800;line-height:1}}
.sc .l{{font-size:.75rem;color:#718096;margin-top:4px}}
.sc.t .n{{color:#2b4fff}}
.sc.e .n{{color:#e53e3e}}
.sc.w .n{{color:#dd6b20}}
.sc.g .n{{color:#2f855a}}
.sc.p .n{{color:#6b46c1}}
section{{margin:0 36px 28px}}
h2{{font-size:1rem;font-weight:700;margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.badge{{padding:2px 10px;border-radius:999px;font-size:.72rem;font-weight:700}}
.badge.r{{background:#fed7d7;color:#c53030}}
.badge.o{{background:#feebc8;color:#c05621}}
.badge.g{{background:#c6f6d5;color:#276749}}
.badge.p{{background:#e9d8fd;color:#553c9a}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:14px;
       overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.07)}}
thead tr{{background:#f7fafc}}
th{{padding:10px 14px;text-align:left;font-size:.72rem;text-transform:uppercase;
    letter-spacing:.06em;color:#718096;border-bottom:2px solid #e2e8f0}}
td{{padding:11px 14px;border-bottom:1px solid #edf2f7;vertical-align:top;line-height:1.55}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#fafbff}}
.pbadge{{display:inline-block;padding:2px 8px;border-radius:6px;
         font-weight:700;font-size:.78rem;background:#ebf4ff;color:#2b4fff;white-space:nowrap}}
.pbadge.orange{{background:#fef3c7;color:#92400e}}
.pbadge.green{{background:#c6f6d5;color:#276749}}
.key-tag.green{{background:#c6f6d5;color:#276749}}
.err{{color:#e53e3e}}
.cor{{color:#276749;font-weight:700}}
.ctx{{color:#4a5568;font-size:.82rem;font-style:italic}}
strong.hl{{background:#fefcbf;color:#744210;padding:0 2px;border-radius:3px}}
.key-tag{{display:inline-block;background:#e9d8fd;color:#553c9a;padding:2px 8px;
          border-radius:5px;font-weight:700;font-size:.8rem;margin-bottom:4px}}
.val{{color:#c05621}}
.reason{{color:#744210;font-size:.83rem}}
.empty{{padding:36px;text-align:center;color:#a0aec0}}
.empty em{{display:block;font-size:2rem;font-style:normal;margin-bottom:8px}}
footer{{text-align:center;padding:24px;color:#a0aec0;font-size:.78rem}}
</style>
</head>
<body>
<header>
  <h1>📄 보고서 오류 탐지 결과</h1>
  <p>파일: <strong>{src}</strong> &nbsp;·&nbsp; 모델: {model}</p>
</header>

<div class="stats">
  <div class="sc t"><div class="n">{total_errors}</div><div class="l">총 오류 건수</div></div>
  <div class="sc e"><div class="n">{tc}</div><div class="l">오탈자</div></div>
  <div class="sc w"><div class="n">{cc_stat}</div><div class="l">수치/기술 오류</div></div>
  {notation_stat_html}
  {attn_stat_html}
</div>

<section>
  <h2>✏️ 오탈자 <span class="badge r">{tc}건</span></h2>
  {empty_typo if not result.typo_errors else typo_table}
</section>

<section>
  <h2>⚠️ 수치/기술 오류 <span class="badge o">{cc_badge}</span></h2>
  {cons_body}
</section>

{notation_section_html}

{attn_section_html}

<footer>Generated by WellBot report_checker &nbsp;·&nbsp; AWS Bedrock Claude</footer>
</body>
</html>"""
