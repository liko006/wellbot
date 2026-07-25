# memory.py — Claude 스타일 분석 + AgentCore 메모리 로드/저장
import json, re
from config import MEMORY_ID, MODEL_ID, bedrock, mem_client
from storage import load_combined_style

newline ='\n'

# ──────────────────────────────────────────────────────────────
# Claude 스타일 분석
# ──────────────────────────────────────────────────────────────

def analyze_style_with_claude(doc_style: dict) -> dict:
    toc_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(doc_style["toc"]))
    slide_count = doc_style.get("slide_count", len(doc_style.get("slide_details", [])))
 
    # ── 슬라이드별 세부 내용 + 위치(좌/중/우) 표기 ──
    slides_text = ""
    for s in doc_style["slide_details"]:
        slides_text += f"\n[슬라이드 {s['slide_num']}] {s['title']}\n"
 
        # content_items(zone 포함)가 있으면 zone별로 묶어 표기 → LLM이 좌우 판단
        items = s.get("content_items", [])
        if items:
            zones = {"좌": [], "중": [], "우": []}
            for it in items:
                zones.get(it.get("zone", "중"), zones["중"]).append(it.get("text", ""))
            for z in ("좌", "중", "우"):
                if zones[z]:
                    joined = " ".join(t for t in zones[z] if t)
                    slides_text += f"  ({z}) {joined[:300]}\n"
        else:
            # content_items 없으면 기존 방식(위치정보 없음)
            for txt in s.get("content", []):
                slides_text += f"  - {txt}\n"
 
        for t in s.get("tables", []):
            slides_text += f"  [표] {t['structure']} | 헤더: {t['headers']}\n"
            for row in t["rows"][:3]:
                slides_text += f"      {row}\n"
 
    prompt = f"""다음 PPT/PDF 문서를 분석해서 작성 스타일을 JSON으로만 답변하세요.
 
[슬라이드 수]
{slide_count}장
 
[슬라이드 제목 시퀀스]
{toc_text}
 
[슬라이드별 세부 내용]
{slides_text}
 
[레이아웃 판정 지침]
각 슬라이드가 좌우 2단(two_col)인지 단일 영역(single)인지 판정하세요.
- (좌)와 (우)에 서로 다른 성격의 내용이 나뉘어 있으면 two_col
- 한 흐름으로 이어지거나, 한쪽(또는 중앙)에만 내용이 있으면 single
- 좌우로 갈렸어도 단순히 같은 목록이 이어진 것뿐이면 single
- 표 하나로 페이지가 채워지면 single
 
JSON 형식:
{{
  "문서_작성_스타일": {{
    "문서유형": "...",
    "문서목적": "...",
    "영역별_bullet_수": "...",
    "불렛_구조": "... (예: 1단계만 사용 / 2단계: 주장+근거 / 3단계: 주장+근거+사례)",
    "전개_방식": "..."
  }},
  "표현규칙": {{
    "문장종결": "...",
    "보고서_톤": "...",
    "약어_사용_방식": "...",
    "선호_문체": "...",
    "근거_제시_방식": "..."
  }},
  "출력형식": {{
    "슬라이드_분량": "{slide_count}장",
    "표_활용": "..."
  }},
  "페이지별_레이아웃": [
    {{"슬라이드": 1, "레이아웃": "single 또는 two_col"}}
  ]
}}"""
 
    resp = bedrock.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 10000,
            "messages": [{"role": "user", "content": prompt}],
        }),
    )
    text = json.loads(resp["body"].read())["content"][0]["text"]
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        result = json.loads(m.group()) if m else {}
    except Exception:
        result = {}
 
    # ── LLM의 페이지별 레이아웃 판정 → layout_ratio 로 집계 ──
    per_page = result.get("페이지별_레이아웃", [])
    # ── 디버그: 페이지별 판정 ──
    for p in per_page:
        print(f"{p.get('슬라이드')}페이지 → {p.get('레이아웃')}")
    # ────────────────────────
    counts: dict = {}
    for p in per_page:
        lt = p.get("레이아웃", "single")
        lt = lt if lt in ("single", "two_col") else "single"
        counts[lt] = counts.get(lt, 0) + 1
    total = sum(counts.values()) or 1
    layout_ratio = {k: f"{round(v/total*100)}%" for k, v in counts.items()}
 
    # doc_style 에 되돌려서, build_style_desc 의 기존 90% 강제 로직이 쓰게 한다
    doc_style["layout_ratio"] = layout_ratio
    result.setdefault("출력형식", {})["선호_슬라이드_구성"] = (
        ", ".join(f"{k} {v}" for k, v in layout_ratio.items()) if layout_ratio else "N/A"
    )
 
    return result
    
    
def build_style_desc(doc_style: dict, analysis: dict) -> str:
    doc  = analysis.get("문서_작성_스타일", {})
    expr = analysis.get("표현규칙", {})
    fmt  = analysis.get("출력형식", {})

    lr = doc_style.get("layout_ratio", {})
    if lr:
        merged = {}
        for k, v in lr.items():
            key = "single" if k in ("single", "title_only") else k
            pct = int(str(v).rstrip("%") or 0)
            merged[key] = merged.get(key, 0) + pct
        order = sorted(merged.items(), key=lambda x: -x[1])
        primary = order[0][0]
        ratio_str = ", ".join(f"{k} {v}%" for k, v in order)
        layout_hint = (
            f"- 레이아웃: 주로 {primary}로 구성하되, 내용·스토리라인상 다른 레이아웃이 "
            f"자연스러운 페이지는 그에 맞게 둔다. 억지로 비율을 맞추지 말고 페이지 내용에 따라 "
            f"판단한다. (참고 비율: {ratio_str})\n"
        )
    else:
        layout_hint = ""

    return (
        "[문서 작성 스타일]\n"
        f"* 문서유형: {doc.get('문서유형', 'N/A')}\n"
        f"* 문서목적: {doc.get('문서목적', 'N/A')}\n"
        f"* 영역별 bullet 수: {doc.get('영역별_bullet_수', 'N/A')}\n"
        f"* 전개 방식: {doc.get('전개_방식', 'N/A')}\n\n"
        "[표현규칙]\n"
        f"* 문장종결: {expr.get('문장종결', 'N/A')}\n"
        f"* 보고서 톤: {expr.get('보고서_톤', 'N/A')}\n"
        f"* 약어 사용 방식: {expr.get('약어_사용_방식', 'N/A')}\n"
        f"* 선호 문체: {expr.get('선호_문체', 'N/A')}\n"
        f"* 근거 제시 방식: {expr.get('근거_제시_방식', 'N/A')}\n\n"
        "[출력형식]\n"
        f"* 슬라이드 분량: {fmt.get('슬라이드_분량', 'N/A')}\n"
        f"* 선호 슬라이드 구성: {fmt.get('선호_슬라이드_구성', 'N/A')}\n"
        f"{layout_hint}" 
        f"* 표 활용: {fmt.get('표_활용', 'N/A')}"
    )

# ──────────────────────────────────────────────────────────────
# AgentCore 메모리 로드
# writing/{actor_id}/ + preference/{actor_id}/ 통합
# AgentCore 없으면 S3 combined_style.json fallback
# ──────────────────────────────────────────────────────────────


def load_style(actor_id: str, user_id: str, template_id: str, top_k: int = 10) -> str:
    parts = []
    for namespace, label in [
        (f"/writing/{actor_id}/",    "문서 스타일"),
        (f"/preference/{actor_id}/", "선호/피드백"),
    ]:
        try:
            resp = mem_client.list_memory_records(
                memory_id=MEMORY_ID,
                namespace=namespace,
            )
            records = resp if isinstance(resp, list) else resp.get("memoryRecordSummaries", [])
            if not records:
                continue

            records = sorted(
                records,
                key=lambda r: (r.get("createdAt", "") if isinstance(r, dict) else ""),
                reverse=True,
            )[:top_k]

            lines = []
            for r in records:
                content = r.get("content", "") if isinstance(r, dict) else str(r)
                text    = content.get("text", "") if isinstance(content, dict) else str(content)
                if text:
                    lines.append(text)

            if lines:
                parts.append(f"[{label}]{newline}" + "{newline}---{newline}".join(lines))
                print(f"{label} {len(lines)}개 로드")

        except Exception as e:
            print(f"{label} 로드 실패: {e}")

    if parts:
        return "\n\n".join(parts)

    # S3 combined_style.json fallback
    print("AgentCore 메모리 없음 — S3 combined_style.json 시도")
    combined = load_combined_style(user_id, template_id)
    if combined:
        return combined


    return ""