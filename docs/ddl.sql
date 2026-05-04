CREATE TABLE `dept_m` (
  `DEPT_CD` varchar(8) COLLATE utf8mb4_general_ci NOT NULL COMMENT '부서코드',
  `DEPT_NM` varchar(100) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '부서명',
  `DD_CLBY_TOKN_ECNT` decimal(10,0) DEFAULT NULL COMMENT '일별토큰개수',
  `MM_CLBY_TOKN_ECNT` decimal(10,0) DEFAULT NULL COMMENT '월별토큰개수',
  `PRMN_MDL_CNTT` json DEFAULT NULL COMMENT '허용모델내용',
  `RGST_DTM` datetime DEFAULT NULL COMMENT '등록일시',
  `RGSR_ID` varchar(20) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '등록자아이디',
  `UPD_DTM` datetime DEFAULT NULL COMMENT '수정일시',
  `UPPR_ID` varchar(20) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '수정자아이디',
  PRIMARY KEY (`DEPT_CD`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='부서마스터'


CREATE TABLE `emp_m` (
  `EMP_NO` varchar(15) COLLATE utf8mb4_general_ci NOT NULL COMMENT '사원번호',
  `ECR_PWD` varchar(80) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '암호화비밀번호',
  `USER_NM` varchar(50) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '사용자명',
  `LGN_SCS_DTM` datetime DEFAULT NULL COMMENT '로그인성공일시',
  `RGSR_ID` varchar(20) COLLATE utf8mb4_general_ci NOT NULL COMMENT '등록자아이디',
  `RGST_DTM` datetime NOT NULL COMMENT '등록일시',
  `UPPR_ID` varchar(20) COLLATE utf8mb4_general_ci NOT NULL COMMENT '수정자아이디',
  `UPD_DTM` datetime NOT NULL COMMENT '수정일시',
  `USER_ROLE_NM` varchar(50) COLLATE utf8mb4_general_ci NOT NULL COMMENT '사용자역할명',
  `PSTN_DEPT_CD` varchar(8) COLLATE utf8mb4_general_ci NOT NULL COMMENT '소속부서코드',
  `LGN_FLR_TSCNT` decimal(5,0) DEFAULT NULL COMMENT '로그인실패횟수',
  `LOCK_DSBN_DTM` datetime DEFAULT NULL COMMENT '잠금해제일시',
  `USER_UUID` varchar(36) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '사용자UUID',
  `ACNT_STS_NM` varchar(50) COLLATE utf8mb4_general_ci NOT NULL COMMENT '계정상태명',
  PRIMARY KEY (`EMP_NO`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='사원마스터'


CREATE TABLE `crtf_tokn_n` (
  `CRTF_TOKN_ID` varchar(50) COLLATE utf8mb4_general_ci NOT NULL COMMENT '인증토큰아이디',
  `EMP_NO` varchar(15) COLLATE utf8mb4_general_ci NOT NULL COMMENT '사원번호',
  `CRTF_ECR_TOKN_VAL` varchar(300) COLLATE utf8mb4_general_ci NOT NULL COMMENT '인증암호화토큰값',
  `DISS_YN` varchar(1) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '폐기여부',
  `TRTN_DTM` datetime DEFAULT NULL COMMENT '만료일시',
  `DISS_DTM` datetime DEFAULT NULL COMMENT '폐기일시',
  `RGST_DTM` datetime NOT NULL COMMENT '등록일시',
  `RGSR_ID` varchar(20) COLLATE utf8mb4_general_ci NOT NULL COMMENT '등록자아이디',
  `UPD_DTM` datetime NOT NULL COMMENT '수정일시',
  `UPPR_ID` varchar(20) COLLATE utf8mb4_general_ci NOT NULL COMMENT '수정자아이디',
  PRIMARY KEY (`CRTF_TOKN_ID`,`EMP_NO`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='인증토큰내역'


CREATE TABLE `agnt_m` (
  `AGNT_ID` varchar(50) COLLATE utf8mb4_general_ci NOT NULL COMMENT '에이전트아이디',
  `AGNT_SEQ` decimal(10,0) NOT NULL COMMENT '대행순번',
  `AGNT_NM` varchar(100) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '에이전트명',
  `AGNT_FRWK_NM` varchar(100) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '에이전트프레임워크명',
  `AGNT_PATH_ADDR` varchar(300) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '에이전트경로주소',
  `AGNT_DSCR_CNTT` mediumtext COLLATE utf8mb4_general_ci COMMENT '에이전트설명내용',
  `USE_YN` varchar(1) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '사용여부',
  `RGST_DTM` datetime DEFAULT NULL COMMENT '등록일시',
  `RGSR_ID` varchar(20) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '등록자아이디',
  `UPD_DTM` datetime DEFAULT NULL COMMENT '수정일시',
  `UPPR_ID` varchar(20) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '수정자아이디',
  PRIMARY KEY (`AGNT_ID`,`AGNT_SEQ`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='에이전트마스터'


CREATE TABLE `agnt_mmry_use_n` (
  `AGNT_ID` varchar(50) COLLATE utf8mb4_general_ci NOT NULL COMMENT '에이전트아이디',
  `AGNT_SEQ` decimal(10,0) NOT NULL COMMENT '대행순번',
  `EMP_NO` varchar(15) COLLATE utf8mb4_general_ci NOT NULL COMMENT '사원번호',
  `AGNT_MMRY_PATH_ADDR` varchar(300) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '에이전트메모리경로주소',
  `AGNT_TYPE_DSCR_CNTT` mediumtext COLLATE utf8mb4_general_ci COMMENT '에이전트유형설명내용',
  `USE_YN` varchar(1) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '사용여부',
  `LAST_SYNC_DTM` datetime DEFAULT NULL COMMENT '최종동기화일시',
  `RGST_DTM` datetime DEFAULT NULL COMMENT '등록일시',
  `RGSR_ID` varchar(20) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '등록자아이디',
  `UPD_DTM` datetime DEFAULT NULL COMMENT '수정일시',
  `UPPR_ID` varchar(20) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '수정자아이디',
  PRIMARY KEY (`AGNT_ID`,`AGNT_SEQ`,`EMP_NO`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='에이전트메모리사용내역'


CREATE TABLE `chtb_smry_d` (
  `CHTB_TLK_SMRY_ID` varchar(50) COLLATE utf8mb4_general_ci NOT NULL COMMENT '챗봇대화요약아이디',
  `EMP_NO` varchar(15) COLLATE utf8mb4_general_ci NOT NULL COMMENT '사원번호',
  `CHTB_TLK_SMRY_TTL` varchar(500) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '챗봇대화요약제목',
  `CHTB_MDL_NM` varchar(100) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '챗봇모델명',
  `BKMR_YN` varchar(1) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '즐겨찾기여부',
  `RGSR_ID` varchar(20) COLLATE utf8mb4_general_ci NOT NULL COMMENT '등록자아이디',
  `RGST_DTM` datetime NOT NULL COMMENT '등록일시',
  `UPPR_ID` varchar(20) COLLATE utf8mb4_general_ci NOT NULL COMMENT '수정자아이디',
  `UPD_DTM` datetime NOT NULL COMMENT '수정일시',
  PRIMARY KEY (`CHTB_TLK_SMRY_ID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='챗봇요약상세'


CREATE TABLE `chtb_msg_d` (
  `CHTB_TLK_SMRY_ID` varchar(50) COLLATE utf8mb4_general_ci NOT NULL COMMENT '챗봇대화요약아이디',
  `CHTB_TLK_ID` varchar(50) COLLATE utf8mb4_general_ci NOT NULL COMMENT '챗봇대화아이디',
  `CHTB_TLK_SEQ` decimal(10,0) NOT NULL COMMENT '챗봇대화순번',
  `AGNT_ID` varchar(50) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '에이전트아이디',
  `MSG_ROLE_NM` varchar(50) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '메시지역할명',
  `CHTB_MSG_CNTT` mediumtext COLLATE utf8mb4_general_ci COMMENT '챗봇메시지내용',
  `CHTB_MDL_NM` varchar(100) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '챗봇모델명',
  `CHTB_OFFR_MDL_NM` varchar(50) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '챗봇제공모델명',
  `CHTB_INPT_TOKN_ECNT` decimal(10,0) DEFAULT NULL COMMENT '챗봇입력토큰개수',
  `CHTB_OTPT_TOKN_ECNT` decimal(10,0) DEFAULT NULL COMMENT '챗봇출력토큰개수',
  `CHTB_TOT_TOKN_ECNT` decimal(10,0) DEFAULT NULL COMMENT '챗봇총토큰개수',
  `RPLY_TIME` decimal(5,2) DEFAULT NULL COMMENT '응답시간',
  `ATCH_FILE_NO` bigint DEFAULT NULL COMMENT '첨부파일번호',
  `RGSR_ID` varchar(20) COLLATE utf8mb4_general_ci NOT NULL COMMENT '등록자아이디',
  `RGST_DTM` datetime NOT NULL COMMENT '등록일시',
  `UPPR_ID` varchar(20) COLLATE utf8mb4_general_ci NOT NULL COMMENT '수정자아이디',
  `UPD_DTM` datetime NOT NULL COMMENT '수정일시',
  PRIMARY KEY (`CHTB_TLK_ID`,`CHTB_TLK_SMRY_ID`,`CHTB_TLK_SEQ`),
  UNIQUE KEY `uq_smry_seq` (`CHTB_TLK_SMRY_ID`,`CHTB_TLK_SEQ`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='챗봇메시지상세'


CREATE TABLE `chtb_msg_atch_file_d` (
  `CHTB_TLK_ID` varchar(50) COLLATE utf8mb4_general_ci NOT NULL COMMENT '챗봇대화아이디',
  `ATCH_FILE_NO` bigint NOT NULL COMMENT '첨부파일번호',
  `RGST_DTM` datetime DEFAULT NULL COMMENT '등록일시',
  `RGSR_ID` varchar(20) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '등록자아이디',
  `UPD_DTM` datetime DEFAULT NULL COMMENT '수정일시',
  `UPPR_ID` varchar(20) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '수정자아이디',
  PRIMARY KEY (`CHTB_TLK_ID`,`ATCH_FILE_NO`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='챗봇메시지첨부파일상세'


CREATE TABLE `atch_file_m` (
  `ATCH_FILE_NO` bigint NOT NULL AUTO_INCREMENT,
  `ATCH_FILE_NM` varchar(300) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '첨부파일명',
  `ATCH_FILE_URL_ADDR` varchar(500) COLLATE utf8mb4_general_ci DEFAULT NULL COMMENT '첨부파일URL주소',
  `ATCH_FILE_TOKN_ECNT` decimal(10,0) DEFAULT NULL COMMENT '첨부파일토큰개수',
  `RGST_DTM` datetime NOT NULL COMMENT '등록일시',
  `RGSR_ID` varchar(20) COLLATE utf8mb4_general_ci NOT NULL COMMENT '등록자아이디',
  `UPD_DTM` datetime NOT NULL COMMENT '수정일시',
  `UPPR_ID` varchar(20) COLLATE utf8mb4_general_ci NOT NULL COMMENT '수정자아이디',
  PRIMARY KEY (`ATCH_FILE_NO`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='첨부파일마스터'