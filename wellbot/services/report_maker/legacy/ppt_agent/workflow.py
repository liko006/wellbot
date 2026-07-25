# 에이전트 구성:
#   StyleAnalyzerAgent    : S3 신규 파일 탐색 → 파싱/이미지추출 → 분석 → AgentCore writing 저장
#   MemoryLoaderAgent     : AgentCore writing+preference 로드 → S3 fallback
#   OutlineGeneratorAgent : 스타일 + 주제(또는 이미지 텍스트) → 아웃라인 JSON 생성
#   OutlineEditorAgent    : planner → apply_edit (대화 루프 반복)
#
# Interrupt 지점:
#   [1] generate_outline  : 아웃라인 승인 또는 수정 지시 입력
#   [2] apply_edit        : 수정 내용 사전 확인 (y/n)

import json, re
from pathlib import Path

from strands import Agent, tool
from strands.models import BedrockModel

from config import MODEL_ID, bedrock
from storage import (
    list_files_from_s3,
    download_file_from_s3,
    get_analyzed_history,
    save_analyzed_history,
    save_combined_style,
    load_combined_style,
    extract_doc_style,
    extract_text_from_image,
    save_style_to_agentcore,
    is_image_file,
)
from memory import analyze_style_with_claude, build_style_desc, load_style

_model = BedrockModel(model_id=MODEL_ID, region_name="ap-northeast-2")
newline = '\n'

# ══════════════════════════════════════════════════════════════
# STEP 1. StyleAnalyzerAgent
# 파일 타입 분기:
#   .pptx/.pdf → extract_doc_style() + analyze_style_with_claude() → /writing/ 저장
#   .jpg/.png  → extract_text_from_image() → 텍스트만 반환
# ══════════════════════════════════════════════════════════════
def run_style_analyzer(user_id: str, template_id: str) -> str:
    _state = {
        "analyzed":  get_analyzed_history(user_id, template_id),
        "last_desc": "",
    }

    @tool
    def scan_new_files() -> str:
        files = list_files_from_s3(user_id, template_id)
        new = [o["Key"] for o in files if Path(o["Key"]).name not in _state["analyzed"]]
        print(f"   전체 {len(files)}개 / 신규 {len(new)}개")
        return json.dumps(
            {"total": len(files), "new_count": len(new), "keys": new},
            ensure_ascii=False,
        )

    @tool
    def analyze_and_save(s3_key: str) -> str:
        filename = Path(s3_key).name
        local_path = download_file_from_s3(s3_key)
 
        if is_image_file(local_path):
            print(f"    이미지 텍스트 추출: {filename}")
            extracted = extract_text_from_image(local_path)
            _state["analyzed"].add(filename)
            save_analyzed_history(user_id, template_id, _state["analyzed"])
            return json.dumps(
                {"filename": filename, "type": "image", "extracted_text": extracted},
                ensure_ascii=False,
            )

        print(f"   문서 스타일 분석: {filename}")
        doc_style = extract_doc_style(local_path)
        analysis = analyze_style_with_claude(doc_style)
        
        style_desc = f"[문서명: {filename}]{newline}" + build_style_desc(doc_style, analysis)
        save_style_to_agentcore(actor_id, style_desc)
        save_combined_style(user_id, template_id, style_desc)

        _state["analyzed"].add(filename)
        save_analyzed_history(user_id, template_id, _state["analyzed"])
        _state["last_desc"] = style_desc

        print(f"     저장 완료: {filename}")
        return json.dumps({"filename": filename, "type": "doc", "saved": True},
                          ensure_ascii=False)

    agent = Agent(
        model=_model,
        system_prompt="""당신은 문서 스타일 분석 전문가입니다.

실행 순서:
1. scan_new_files로 신규 파일 목록을 확인하세요.
2. 신규 파일이 없으면 "신규 파일 없음"을 반환하세요.
3. 각 s3_key에 대해 analyze_and_save를 순서대로 호출하세요.
4. 완료 후 "분석 완료: N개"를 반환하세요.""",
        tools=[scan_new_files, analyze_and_save],
        callback_handler=None,
    )

    print("\n  [StyleAnalyzerAgent] 실행 중...")
    agent("S3 신규 파일을 모두 분석하고 저장해줘")
    return _state["last_desc"]


# ══════════════════════════════════════════════════════════════
# STEP 2. MemoryLoaderAgent
# AgentCore /writing/ + /preference/ 통합 로드 → S3 fallback
# ══════════════════════════════════════════════════════════════
def run_memory_loader(actor_id, user_id, template_id: str) -> str:

    # 1. AgentCore
    style = load_style(actor_id, user_id, template_id)
    if style:
        print(f"AgentCore 로드 완료 ({len(style)}자)")
        return style

    # 2. S3 combined_style.json
    style = load_combined_style(user_id, template_id)
    if style:
        print(f"combined_style.json 로드 완료 ({len(style)}자)")
        return style
    
    return ""

