"""보고서 오류 검출 — 브라우저 업로드 JS (페이지 레벨 window-global).

KB 업로드 스크립트와 동일한 backendBase 결정 규칙을 사용한다.
  - reportPickFile(): 파일 선택 다이얼로그 → {name, size} 반환(취소 시 null)
  - reportUpload(): 선택 파일을 /api/report_checker/upload 로 전송 → {job_id, filename, error}
정적 스크립트로, 페이지에서 rx.script 로 1회 등록한다.
"""

from __future__ import annotations

import json

REPORT_CHECKER_SCRIPT = """
window._reportBackendBase = window._reportBackendBase || async function() {
    try {
        var envResp = await fetch('/env.json');
        var env = await envResp.json();
        var pingUrl = env.PING || '';
        if (pingUrl) {
            var u = new URL(pingUrl);
            var loc = window.location;
            var isLocalDev = (
                (loc.hostname === 'localhost' || loc.hostname === '127.0.0.1') &&
                (u.hostname === 'localhost' || u.hostname === '127.0.0.1') &&
                u.port !== loc.port
            );
            if (isLocalDev) return loc.protocol + '//' + loc.hostname + ':' + u.port;
        }
    } catch (e) {}
    return '';
};

window._reportFile = null;

window.reportPickFile = function() {
    return new Promise(function(resolve) {
        var input = document.createElement('input');
        input.type = 'file';
        input.accept = '.pdf';
        input.style.display = 'none';
        input.onchange = function() {
            var f = (input.files && input.files[0]) || null;
            window._reportFile = f;
            try { document.body.removeChild(input); } catch (e) {}
            resolve(f ? {name: f.name, size: f.size} : null);
        };
        input.addEventListener('cancel', function() {
            try { document.body.removeChild(input); } catch (e) {}
            resolve(null);
        });
        document.body.appendChild(input);
        input.click();
    });
};

window.reportUpload = async function() {
    var f = window._reportFile;
    if (!f) return {job_id: '', error: '선택된 파일이 없습니다.'};
    var form = new FormData();
    form.append('file', f);
    try {
        var base = await window._reportBackendBase();
        var resp = await fetch(base + '/api/report_checker/upload', {
            method: 'POST', body: form, credentials: 'include'
        });
        var result = await resp.json().catch(function() { return {}; });
        if (!resp.ok) {
            return {job_id: '', error: (result && result.detail) ? result.detail : ('업로드 실패 (' + resp.status + ')')};
        }
        return result;
    } catch (e) {
        return {job_id: '', error: (e && e.message) ? e.message : String(e)};
    }
};
"""


def build_report_download_script(job_id: str, filename: str) -> str:
    """결과 HTML 다운로드 JS 본문 반환.

    백엔드 프록시(POST /api/report_checker/download)로 결과 HTML 을 받아
    a[download] 트릭으로 저장. 한글 파일명 대응(RFC 5987)은 서버 헤더가 처리하되,
    a.download 에는 원본 파일명을 그대로 지정한다. backendBase 는 REPORT_CHECKER_SCRIPT
    가 정의한 window._reportBackendBase 를 재사용.
    """
    job_js = json.dumps(job_id)
    name_js = json.dumps(filename)
    return f"""
    (async function() {{
        try {{
            var base = (typeof window._reportBackendBase === 'function')
                ? await window._reportBackendBase() : '';
            var resp = await fetch(base + '/api/report_checker/download', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{job_id: {job_js}, filename: {name_js}}}),
                credentials: 'include',
            }});
            if (!resp.ok) {{
                var err = await resp.json().catch(function() {{ return {{}}; }});
                alert(err.detail || '다운로드 실패');
                return;
            }}
            var blob = await resp.blob();
            var objUrl = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = objUrl;
            a.download = {name_js};
            a.style.display = 'none';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(objUrl);
        }} catch (e) {{
            console.error('[report download]', e);
        }}
    }})();
    """
