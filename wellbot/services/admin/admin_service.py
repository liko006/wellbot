"""Admin CRUD 서비스 - 부서, 사원, 에이전트"""

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from wellbot.constants import KST

import bcrypt

from wellbot.logger import log_context
from wellbot.models.agent import Agent
from wellbot.models.dept import Dept
from wellbot.models.employee import Employee
from wellbot.services.core.database import get_session

log = logging.getLogger(__name__)


def _to_dict_model(row: Any) -> dict:
    """mapped_column 의 Python 속성명 기반으로 dict 변환"""
    mapper = row.__class__.__mapper__
    result = {}
    for prop in mapper.column_attrs:
        val = getattr(row, prop.key)
        if isinstance(val, datetime):
            val = val.isoformat()
        elif isinstance(val, Decimal):
            val = int(val)
        result[prop.key] = val
    return result


# ── 부서 CRUD ──


def list_depts() -> list[dict]:
    """부서 목록 조회"""
    with get_session() as session:
        rows = session.query(Dept).order_by(Dept.dept_cd).all()
        return [_to_dict_model(r) for r in rows]


def create_dept(
    dept_cd: str,
    dept_nm: str,
    dd_tokn: int | None = None,
    mm_tokn: int | None = None,
    prmn_mdl: dict | None = None,
) -> dict:
    """부서 생성"""
    now = datetime.now(KST)
    with get_session() as session:
        dept = Dept(
            dept_cd=dept_cd,
            dept_nm=dept_nm,
            dd_clby_tokn_ecnt=dd_tokn,
            mm_clby_tokn_ecnt=mm_tokn,
            prmn_mdl_cntt=prmn_mdl,
            rgst_dtm=now,
            rgsr_id="ADMIN",
            upd_dtm=now,
            uppr_id="ADMIN",
        )
        session.add(dept)
        session.flush()
        return _to_dict_model(dept)


def update_dept(dept_cd: str, **kwargs: Any) -> dict:
    """부서 수정"""
    with get_session() as session:
        dept = session.get(Dept, dept_cd)
        if not dept:
            raise ValueError(f"부서 '{dept_cd}'를 찾을 수 없습니다.")
        for key, val in kwargs.items():
            if hasattr(dept, key):
                setattr(dept, key, val)
        dept.upd_dtm = datetime.now(KST)
        dept.uppr_id = "ADMIN"
        session.flush()
        return _to_dict_model(dept)


def delete_dept(dept_cd: str) -> bool:
    """부서 삭제"""
    with get_session() as session:
        dept = session.get(Dept, dept_cd)
        if not dept:
            return False
        session.delete(dept)
        return True


# ── 사원 CRUD ──


def list_employees() -> list[dict]:
    """사원 목록 조회 (비밀번호 제외)"""
    with get_session() as session:
        rows = session.query(Employee).order_by(Employee.emp_no).all()
        result = []
        for r in rows:
            d = _to_dict_model(r)
            d.pop("ecr_pwd", None)
            result.append(d)
        return result


def create_employee(
    emp_no: str,
    password: str,
    user_nm: str,
    user_role_nm: str,
    pstn_dept_cd: str,
    acnt_sts_nm: str = "ACTIVE",
) -> dict:
    """사원 생성 (bcrypt 해싱)"""
    now = datetime.now(KST)
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with get_session() as session:
        emp = Employee(
            emp_no=emp_no,
            ecr_pwd=hashed,
            user_nm=user_nm,
            user_role_nm=user_role_nm,
            pstn_dept_cd=pstn_dept_cd,
            acnt_sts_nm=acnt_sts_nm,
            user_uuid=str(uuid.uuid4()),
            lgn_flr_tscnt=0,
            rgst_dtm=now,
            rgsr_id="ADMIN",
            upd_dtm=now,
            uppr_id="ADMIN",
        )
        session.add(emp)
        session.flush()
        d = _to_dict_model(emp)
        d.pop("ecr_pwd", None)
        return d


def update_employee(emp_no: str, **kwargs: Any) -> dict:
    """사원 수정 (password 포함 시 bcrypt 재해싱)"""
    with get_session() as session:
        emp = session.get(Employee, emp_no)
        if not emp:
            raise ValueError(f"사원 '{emp_no}'를 찾을 수 없습니다.")
        if "password" in kwargs:
            pw = kwargs.pop("password")
            if pw:
                emp.ecr_pwd = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        for key, val in kwargs.items():
            if hasattr(emp, key):
                setattr(emp, key, val)
        emp.upd_dtm = datetime.now(KST)
        emp.uppr_id = "ADMIN"
        session.flush()
        d = _to_dict_model(emp)
        d.pop("ecr_pwd", None)
        return d


def approve_employee(emp_no: str) -> dict:
    """가입 대기(PENDING) 사원을 승인(ACTIVE)하고 로그인 실패/잠금 상태 초기화."""
    log_context.bind(emp_no=emp_no)
    result = update_employee(
        emp_no, acnt_sts_nm="ACTIVE", lgn_flr_tscnt=0, lock_dsbn_dtm=None
    )
    log.info("admin approved employee: emp_no=%s", emp_no)
    return result


def unlock_employee(emp_no: str) -> dict:
    """로그인 실패 누적/잠금을 해제 (계정 상태는 유지)."""
    log_context.bind(emp_no=emp_no)
    result = update_employee(emp_no, lgn_flr_tscnt=0, lock_dsbn_dtm=None)
    log.info("admin unlocked employee: emp_no=%s", emp_no)
    return result


def delete_employee(emp_no: str) -> bool:
    """사원 삭제"""
    with get_session() as session:
        emp = session.get(Employee, emp_no)
        if not emp:
            return False
        session.delete(emp)
        return True


def authenticate_admin(emp_no: str, password: str) -> bool:
    """DB 기반 관리자 인증 (ADMIN 역할 + bcrypt 검증)"""
    with get_session() as session:
        emp = session.get(Employee, emp_no)
        if not emp or emp.user_role_nm != "ADMIN":
            return False
        if not emp.ecr_pwd:
            return False
        return bcrypt.checkpw(password.encode(), emp.ecr_pwd.encode())


# ── 에이전트 CRUD ──


def list_agents() -> list[dict]:
    """에이전트 목록 조회"""
    with get_session() as session:
        rows = session.query(Agent).order_by(Agent.agnt_id, Agent.agnt_seq).all()
        return [_to_dict_model(r) for r in rows]


def create_agent(
    agnt_id: str,
    agnt_seq: int,
    agnt_nm: str,
    agnt_frwk_nm: str = "",
    agnt_path_addr: str = "",
    agnt_dscr_cntt: str = "",
    use_yn: str = "Y",
) -> dict:
    """에이전트 생성"""
    now = datetime.now(KST)
    with get_session() as session:
        agent = Agent(
            agnt_id=agnt_id,
            agnt_seq=agnt_seq,
            agnt_nm=agnt_nm,
            agnt_frwk_nm=agnt_frwk_nm or None,
            agnt_path_addr=agnt_path_addr or None,
            agnt_dscr_cntt=agnt_dscr_cntt or None,
            use_yn=use_yn,
            rgst_dtm=now,
            rgsr_id="ADMIN",
            upd_dtm=now,
            uppr_id="ADMIN",
        )
        session.add(agent)
        session.flush()
        return _to_dict_model(agent)


def update_agent(agnt_id: str, agnt_seq: int, **kwargs: Any) -> dict:
    """에이전트 수정"""
    with get_session() as session:
        agent = session.get(Agent, (agnt_id, agnt_seq))
        if not agent:
            raise ValueError(f"에이전트 '{agnt_id}-{agnt_seq}'를 찾을 수 없습니다.")
        for key, val in kwargs.items():
            if hasattr(agent, key):
                setattr(agent, key, val)
        agent.upd_dtm = datetime.now(KST)
        agent.uppr_id = "ADMIN"
        session.flush()
        return _to_dict_model(agent)


def delete_agent(agnt_id: str, agnt_seq: int) -> bool:
    """에이전트 삭제"""
    with get_session() as session:
        agent = session.get(Agent, (agnt_id, agnt_seq))
        if not agent:
            return False
        session.delete(agent)
        return True
