"""파일 다운로드용 JS 스크립트 빌더.

ChatState.download_attachment 가 rx.call_script 로 실행할 JS 본문 생성.
백엔드 프록시(POST /api/download/{file_no}) 경유로 다운로드한 뒤
브라우저에서 a[download] 트릭으로 저장.
"""

from __future__ import annotations

import json


def build_download_script(file_no: int) -> str:
    """첨부파일 다운로드를 실행하는 JS 본문 반환"""
    return f"""
            (async function() {{
                try {{
                    let backendBase = '';
                    try {{
                        const envResp = await fetch('/env.json');
                        const env = await envResp.json();
                        const pingUrl = env.PING || '';
                        if (pingUrl) {{
                            const u = new URL(pingUrl);
                            const loc = window.location;
                            const isLocalDev = (
                                (loc.hostname === 'localhost' || loc.hostname === '127.0.0.1') &&
                                (u.hostname === 'localhost' || u.hostname === '127.0.0.1') &&
                                u.port !== loc.port
                            );
                            if (isLocalDev) {{
                                backendBase = loc.protocol + '//' + loc.hostname + ':' + u.port;
                            }}
                        }}
                    }} catch(e) {{}}

                    const resp = await fetch(backendBase + '/api/download/{file_no}', {{
                        method: 'POST',
                        credentials: 'include',
                    }});
                    if (!resp.ok) {{
                        const err = await resp.json().catch(function() {{ return {{}}; }});
                        alert(err.detail || '다운로드 실패');
                        return;
                    }}
                    const blob = await resp.blob();
                    const cd = resp.headers.get('Content-Disposition') || '';
                    const fnMatch = cd.match(/filename\\*=UTF-8''(.+)/);
                    const filename = fnMatch ? decodeURIComponent(fnMatch[1]) : 'download';
                    const objUrl = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = objUrl;
                    a.download = filename;
                    a.style.display = 'none';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(objUrl);
                }} catch (e) {{
                    console.error('[wellbot download]', e);
                }}
            }})();
            """


def build_kb_download_script(s3_uri: str, filename: str) -> str:
    """KB 출처 문서 다운로드를 실행하는 JS 본문 반환

    백엔드 프록시(POST /api/download_kb)로 S3 문서를 받아 a[download] 로 저장.
    내부망에서 presigned URL 직접 사용이 막히는 환경 대응. 첨부 다운로드와 달리
    파일 식별자가 file_no 가 아닌 s3_uri 이므로 JSON body 로 전달.
    """
    uri_js = json.dumps(s3_uri)
    name_js = json.dumps(filename)
    return f"""
            (async function() {{
                try {{
                    let backendBase = '';
                    try {{
                        const envResp = await fetch('/env.json');
                        const env = await envResp.json();
                        const pingUrl = env.PING || '';
                        if (pingUrl) {{
                            const u = new URL(pingUrl);
                            const loc = window.location;
                            const isLocalDev = (
                                (loc.hostname === 'localhost' || loc.hostname === '127.0.0.1') &&
                                (u.hostname === 'localhost' || u.hostname === '127.0.0.1') &&
                                u.port !== loc.port
                            );
                            if (isLocalDev) {{
                                backendBase = loc.protocol + '//' + loc.hostname + ':' + u.port;
                            }}
                        }}
                    }} catch(e) {{}}

                    const resp = await fetch(backendBase + '/api/download_kb', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{s3_uri: {uri_js}, filename: {name_js}}}),
                        credentials: 'include',
                    }});
                    if (!resp.ok) {{
                        const err = await resp.json().catch(function() {{ return {{}}; }});
                        alert(err.detail || '다운로드 실패');
                        return;
                    }}
                    const blob = await resp.blob();
                    const objUrl = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = objUrl;
                    a.download = {name_js};
                    a.style.display = 'none';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(objUrl);
                }} catch (e) {{
                    console.error('[wellbot kb download]', e);
                }}
            }})();
            """
