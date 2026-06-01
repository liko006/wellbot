"""인증 서비스 - 로그인, 세션 토큰 관리"""

import logging
import os
import uuid
from datetime import datetime, timedelta

import bcrypt
import jwt

from wellbot.constants import KST, LOCK_DURATION_MINUTES, LOCK_THRESHOLD, TOKEN_EXPIRE_HOURS
from wellbot.models.auth_token import AuthToken
from wellbot.models.dept import Dept
from wellbot.models.employee import Employee
from wellbot.services.core.database import get_session

log = logging.getLogger(__name__)


def _ensure_aware(dt: datetime | None) -> datetime | None:
    """offset-naive datetime 을 KST 로 변환. 이미 aware 이면 그대로 반환"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt


def _get_jwt_secret() -> str:
    """JWT 서명 키 조회.

    import 시점이 아닌 첫 호출 시점에 검증.
    환경변수가 필요 없는 코드 경로(단위 테스트 등) 의 import 가 깨지지 않도록 lazy 검증.
    """
    secret = os.environ.get("JWT_SECRET", "")
    if not secret:
        raise RuntimeError(
            "JWT_SECRET 환경변수가 설정되지 않았습니다. "
            "엔트리포인트에서 wellbot.env.init_env() 가 호출되었는지 확인하세요."
        )
    return secret


def authenticate_user(emp_no: str, password: str) -> dict:
    """사원번호 + 비밀번호 인증.

    Returns:
        {"success": True, "user": {...}} 또는
        {"success": False, "error": "에러 메시지"}
    """
    with get_session() as session:
        emp = session.query(Employee).get(emp_no)
        if not emp:
            log.info("login failed: unknown emp_no=%s", emp_no)
            return {"success": False, "error": "사원번호 또는 비밀번호가 올바르지 않습니다."}

        if emp.acnt_sts_nm != "ACTIVE":
            log.info("login denied: inactive emp_no=%s status=%s", emp_no, emp.acnt_sts_nm)
            return {"success": False, "error": "비활성 계정입니다. 관리자에게 문의하세요."}

        fail_count = int(emp.lgn_flr_tscnt or 0)
        if fail_count >= LOCK_THRESHOLD:
            lock_dtm = _ensure_aware(emp.lock_dsbn_dtm)
            if lock_dtm and lock_dtm > datetime.now(KST):
                remaining = (lock_dtm - datetime.now(KST)).seconds // 60
                log.warning("login denied: locked emp_no=%s remaining_min=%s", emp_no, remaining + 1)
                return {
                    "success": False,
                    "error": f"계정이 잠겨있습니다. {remaining + 1}분 후 다시 시도해주세요.",
                }
            # 잠금 해제 시간이 경과했으므로 카운터 초기화
            emp.lgn_flr_tscnt = 0
            emp.lock_dsbn_dtm = None

        if not emp.ecr_pwd or not bcrypt.checkpw(
            password.encode(), emp.ecr_pwd.encode()
        ):
            emp.lgn_flr_tscnt = int(emp.lgn_flr_tscnt or 0) + 1
            if int(emp.lgn_flr_tscnt) >= LOCK_THRESHOLD:
                emp.lock_dsbn_dtm = datetime.now(KST) + timedelta(
                    minutes=LOCK_DURATION_MINUTES
                )
                log.warning(
                    "account locked: emp_no=%s fail_count=%s lock_min=%s",
                    emp_no, emp.lgn_flr_tscnt, LOCK_DURATION_MINUTES,
                )
            else:
                log.info(
                    "login failed: bad password emp_no=%s fail_count=%s",
                    emp_no, emp.lgn_flr_tscnt,
                )
            emp.upd_dtm = datetime.now(KST)
            emp.uppr_id = emp_no
            return {"success": False, "error": "사원번호 또는 비밀번호가 올바르지 않습니다."}

        emp.lgn_flr_tscnt = 0
        emp.lock_dsbn_dtm = None
        emp.lgn_scs_dtm = datetime.now(KST)
        emp.upd_dtm = datetime.now(KST)
        emp.uppr_id = emp_no

        log.info("login success: emp_no=%s role=%s", emp_no, emp.user_role_nm)
        return {
            "success": True,
            "user": {
                "emp_no": emp.emp_no,
                "user_nm": emp.user_nm or "",
                "user_role_nm": emp.user_role_nm,
                "pstn_dept_cd": emp.pstn_dept_cd,
            },
        }


def create_session_token(emp_no: str) -> str:
    """세션 토큰(JWT) 발급 및 DB 저장"""
    now = datetime.now(KST)
    expires = now + timedelta(hours=TOKEN_EXPIRE_HOURS)
    token_id = uuid.uuid4().hex[:50]

    payload = {
        "emp_no": emp_no,
        "token_id": token_id,
        "exp": int(expires.timestamp()),
    }
    token = jwt.encode(payload, _get_jwt_secret(), algorithm="HS256")

    with get_session() as session:
        record = AuthToken(
            crtf_tokn_id=token_id,
            emp_no=emp_no,
            crtf_ecr_tokn_val=token,
            diss_yn="N",
            trtn_dtm=expires,
            rgst_dtm=now,
            rgsr_id=emp_no[:20],
            upd_dtm=now,
            uppr_id=emp_no[:20],
        )
        session.add(record)

    return token


def validate_session_token(token: str) -> dict | None:
    """세션 토큰 검증. 유효하면 사용자 정보 dict, 아니면 None."""
    if not token:
        return None

    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

    token_id = payload.get("token_id", "")
    emp_no = payload.get("emp_no", "")
    if not token_id or not emp_no:
        return None

    with get_session() as session:
        record = session.query(AuthToken).get((token_id, emp_no))
        if not record or record.diss_yn != "N":
            return None
        if _ensure_aware(record.trtn_dtm) and _ensure_aware(record.trtn_dtm) < datetime.now(KST):
            return None

        emp = session.query(Employee).get(emp_no)
        if not emp or emp.acnt_sts_nm != "ACTIVE":
            return None

        return {
            "emp_no": emp.emp_no,
            "user_nm": emp.user_nm or "",
            "user_role_nm": emp.user_role_nm,
            "pstn_dept_cd": emp.pstn_dept_cd,
        }


def invalidate_session_token(token: str) -> bool:
    """세션 토큰 폐기"""
    if not token:
        return False

    try:
        payload = jwt.decode(
            token, _get_jwt_secret(), algorithms=["HS256"], options={"verify_exp": False}
        )
    except jwt.InvalidTokenError:
        return False

    token_id = payload.get("token_id", "")
    emp_no = payload.get("emp_no", "")
    if not token_id or not emp_no:
        return False

    now = datetime.now(KST)
    with get_session() as session:
        record = session.query(AuthToken).get((token_id, emp_no))
        if not record:
            return False
        record.diss_yn = "Y"
        record.diss_dtm = now
        record.upd_dtm = now
        record.uppr_id = emp_no[:20]

    log.info("logout: emp_no=%s", emp_no)
    return True


def register_user(
    emp_no: str,
    password: str,
    user_nm: str,
    pstn_dept_cd: str,
) -> dict:
    """회원가입 (PENDING 상태로 생성).

    Returns:
        {"success": True} 또는 {"success": False, "error": "에러 메시지"}
    """
    if not emp_no or not password or not user_nm:
        return {"success": False, "error": "모든 필드를 입력해주세요."}

    with get_session() as session:
        existing = session.query(Employee).get(emp_no)
        if existing:
            return {"success": False, "error": "이미 등록된 사원번호입니다."}

        now = datetime.now(KST)
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        emp = Employee(
            emp_no=emp_no,
            ecr_pwd=hashed,
            user_nm=user_nm,
            user_role_nm="USER",
            pstn_dept_cd=pstn_dept_cd,
            acnt_sts_nm="PENDING",
            user_uuid=str(uuid.uuid4()),
            lgn_flr_tscnt=0,
            rgst_dtm=now,
            rgsr_id=emp_no[:20],
            upd_dtm=now,
            uppr_id=emp_no[:20],
        )
        session.add(emp)

    log.info("user registered (PENDING): emp_no=%s dept=%s", emp_no, pstn_dept_cd)
    return {"success": True}


def list_dept_options() -> list[dict]:
    """부서 목록 조회 (회원가입 드롭다운용)"""
    with get_session() as session:
        rows = (
            session.query(Dept.dept_cd, Dept.dept_nm)
            .order_by(Dept.dept_cd)
            .all()
        )
        return [{"code": r[0], "name": r[1] or r[0]} for r in rows]


def change_password(emp_no: str, current_password: str, new_password: str) -> dict:
    """비밀번호 변경.

    Args:
        emp_no: 사원번호
        current_password: 현재 비밀번호
        new_password: 새 비밀번호

    Returns:
        {"success": True} 또는 {"success": False, "error": "에러 메시지"}
    """
    if not emp_no or not current_password or not new_password:
        return {"success": False, "error": "모든 필드를 입력해주세요."}

    with get_session() as session:
        emp = session.query(Employee).get(emp_no)
        if not emp:
            return {"success": False, "error": "사용자를 찾을 수 없습니다."}

        if emp.acnt_sts_nm != "ACTIVE":
            return {"success": False, "error": "비활성 계정입니다."}

        if not emp.ecr_pwd or not bcrypt.checkpw(
            current_password.encode(), emp.ecr_pwd.encode()
        ):
            return {"success": False, "error": "현재 비밀번호가 올바르지 않습니다."}

        hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        emp.ecr_pwd = hashed
        emp.upd_dtm = datetime.now(KST)
        emp.uppr_id = emp_no[:20]

    return {"success": True}
