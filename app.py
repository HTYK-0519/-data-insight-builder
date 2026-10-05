import io
import re
import csv
import os
import json
import math
import logging
import traceback
from flask import Flask, jsonify, request, render_template
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from google import genai
from google.genai import types

import tools

# .env 환경변수 로드
load_dotenv()

# 로깅 모듈 설정 (print() 사용 금지 규칙 준수)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("DataInsightBuilder")

app = Flask(__name__)

# 환경변수 읽기 (절대 코드에 하드코딩하지 않음)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", 10))

app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# 인메모리 데이터셋 보관 (디스크 영구 저장 배제)
CURRENT_DATASET = {}

def try_decode_csv(raw_bytes: bytes):
    """utf-8-sig, utf-8, cp949, euc-kr 순서로 인코딩을 시도합니다."""
    candidate_encodings = ["utf-8-sig", "utf-8", "cp949", "euc-kr"]
    for enc in candidate_encodings:
        try:
            text = raw_bytes.decode(enc)
            logger.info("[ENCODING] %s 시도 성공", enc)
            return text, enc
        except UnicodeDecodeError:
            logger.info("[ENCODING] %s 시도 실패 (디코딩 불가)", enc)
            continue
    return None, None

def detect_header_row(text: str, max_check_lines: int = 30):
    """앞쪽 여러 줄을 검사하여 유효 컬럼 수가 가장 많은 실제 열 이름(Header) 행을 감지합니다."""
    lines = text.splitlines()
    check_limit = min(len(lines), max_check_lines)
    
    best_row_idx = 0
    max_non_empty_cols = 0
    
    for idx in range(check_limit):
        line = lines[idx].strip()
        if not line:
            continue
        try:
            reader = csv.reader([line])
            cols = next(reader)
            non_empty_cols = sum(1 for c in cols if c.strip())
            if non_empty_cols > max_non_empty_cols:
                max_non_empty_cols = non_empty_cols
                best_row_idx = idx
        except Exception:
            continue

    logger.info("[HEADER] 헤더 줄 감지 결과 (건너뛴 줄 수: %d, 헤더 열 수: %d)", best_row_idx, max_non_empty_cols)
    return best_row_idx

def sanitize_for_json(obj):
    """NaN, Inf, NumPy 데이터 타입을 JSON 직렬화 가능한 순수 파이썬 타입으로 안전 변환"""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif pd.isna(obj):
        return None
    elif isinstance(obj, (np.integer, int)):
        return int(obj)
    elif isinstance(obj, (np.floating, float)):
        return float(obj)
    return obj

def diagnose_dataframe(df: pd.DataFrame, filename: str, file_size: int, encoding: str, skipped_rows: int):
    """13가지 필수 진단 항목을 분석하여 JSON 호환 딕셔너리로 반환합니다."""
    rows_count, cols_count = df.shape
    columns = list(df.columns)
    warnings = []

    # 1. Unnamed 열 및 빈 열 목록 탐지
    unnamed_cols = [c for c in columns if str(c).strip().startswith("Unnamed:") or str(c).strip() == ""]
    empty_cols = [c for c in columns if df[c].isna().all()]
    
    if unnamed_cols:
        warnings.append(f"이름이 없는 열(Unnamed)이 {len(unnamed_cols)}개 감지되었습니다: {unnamed_cols}")
    if empty_cols:
        warnings.append(f"데이터가 완전히 비어있는 열이 {len(empty_cols)}개 있습니다: {empty_cols}")

    # 2. 열별 데이터 타입 및 결측치
    dtypes = {}
    missing_stats = {}
    numeric_cols = []
    string_cols = []
    
    for col in columns:
        dtype_str = str(df[col].dtype)
        dtypes[col] = dtype_str
        
        missing_count = int(df[col].isna().sum())
        missing_ratio = round((missing_count / rows_count) * 100, 2) if rows_count > 0 else 0.0
        missing_stats[col] = {
            "count": missing_count,
            "ratio_percent": missing_ratio
        }
        
        if pd.api.types.is_numeric_dtype(df[col]):
            numeric_cols.append(col)
        else:
            converted = pd.to_numeric(df[col].dropna(), errors="coerce")
            if len(df[col].dropna()) > 0 and converted.notna().sum() / len(df[col].dropna()) > 0.8:
                numeric_cols.append(col)
            else:
                string_cols.append(col)

    # 3. 연도 및 날짜 후보 열 감지
    year_pattern = re.compile(r"^(19|20)\d{2}$")
    year_cols = [c for c in columns if year_pattern.match(str(c).strip())]
    date_cols = [c for c in columns if any(kw in str(c).lower() for kw in ["year", "date", "연도", "일자", "년도"])]
    date_candidate_cols = sorted(list(set(year_cols + date_cols)))

    # 4. 연도별 데이터 충실도 및 권장 기준연도
    year_coverage = {}
    recommended_year = None
    
    if year_cols:
        best_count = -1
        sorted_years = sorted(year_cols, key=lambda x: str(x), reverse=True)
        for y_col in sorted_years:
            valid_count = int(df[y_col].notna().sum())
            valid_ratio = round((valid_count / rows_count) * 100, 2) if rows_count > 0 else 0.0
            year_coverage[y_col] = {
                "valid_count": valid_count,
                "valid_ratio_percent": valid_ratio
            }
        
        for y_col in sorted_years:
            cnt = year_coverage[y_col]["valid_count"]
            if cnt > best_count and cnt >= (rows_count * 0.5):
                best_count = cnt
                recommended_year = y_col
        if not recommended_year and sorted_years:
            recommended_year = sorted_years[0]

    # 5. 집계 행 후보 탐지
    agg_keywords = [
        "world", "oecd", "euro area", "european union", "high income", "low income", 
        "middle income", "upper middle income", "lower middle income", "income", "area", 
        "total", "latin america", "sub-saharan", "asia", "arab world", "caribbean",
        "ida & ibrd", "dividend"
    ]
    aggregate_row_candidates = []
    target_text_col = string_cols[0] if string_cols else (columns[0] if columns else None)
    if target_text_col:
        for idx, val in df[target_text_col].dropna().items():
            val_str = str(val).lower()
            if any(k in val_str for k in agg_keywords):
                aggregate_row_candidates.append({
                    "row_index": int(idx),
                    "label": str(val),
                    "matched_in_column": target_text_col
                })
    
    if aggregate_row_candidates:
        warnings.append(
            f"국가/개별 데이터가 아닌 '집계 행(World, OECD, Region 등)' 후보가 {len(aggregate_row_candidates)}개 발견되었습니다. 분석 시 제외 옵션을 권장합니다."
        )

    # 6. 중복 행 수
    duplicate_rows_count = int(df.duplicated().sum())
    if duplicate_rows_count > 0:
        warnings.append(f"완전히 중복된 행이 {duplicate_rows_count}개 발견되었습니다.")

    # 7. 데이터 미리보기 10행
    preview_df = df.head(10).copy()
    preview_10_rows = preview_df.to_dict(orient="records")
    preview_10_rows = sanitize_for_json(preview_10_rows)

    diagnosis = {
        "filename": filename,
        "file_size_bytes": file_size,
        "encoding": encoding,
        "skipped_rows": skipped_rows,
        "rows": rows_count,
        "columns": cols_count,
        "column_names": columns,
        "dtypes": dtypes,
        "numeric_columns": numeric_cols,
        "string_columns": string_cols,
        "date_candidate_columns": date_candidate_cols,
        "missing_stats": missing_stats,
        "duplicate_rows_count": duplicate_rows_count,
        "unnamed_columns": unnamed_cols,
        "empty_columns": empty_cols,
        "year_coverage": year_coverage,
        "recommended_year": recommended_year,
        "aggregate_row_candidates": aggregate_row_candidates,
        "preview_10_rows": preview_10_rows,
        "warnings": warnings,
        "memory_notice": "데이터는 서버 메모리에만 임시 보관되며 서버를 재시작하면 사라집니다."
    }

    logger.info("[DIAGNOSE] 진단 완료 warnings=%d agg_candidates=%d rec_year=%s", 
                len(warnings), len(aggregate_row_candidates), str(recommended_year))
    return diagnosis

@app.route("/", methods=["GET"])
def index():
    """index.html 반환"""
    logger.info("[REQUEST] GET /")
    return render_template("index.html")

@app.route("/health", methods=["GET"])
def health():
    """서버 상태와 API Key 설정 여부만 반환 (키 값 자체는 절대 반환/노출 금지)"""
    logger.info("[REQUEST] GET /health")
    has_api_key = bool(GEMINI_API_KEY and GEMINI_API_KEY.strip() != "your_gemini_api_key_here")
    response = {
        "status": "ok",
        "api_key_configured": has_api_key,
        "model": GEMINI_MODEL
    }
    logger.info("[RESPONSE] status=200 api_key_configured=%s", has_api_key)
    return jsonify(response), 200

@app.route("/inspect", methods=["POST"])
def inspect():
    """POST /inspect (CSV 업로드 및 13가지 정밀 진단)"""
    logger.info("[REQUEST] POST /inspect")
    
    if "file" not in request.files:
        logger.warning("[ERROR] 파일이 전송되지 않음")
        return jsonify({"error": "파일이 선택되지 않았습니다. 분석할 CSV 파일을 업로드해 주세요."}), 400
    
    file = request.files["file"]
    if not file or file.filename.strip() == "":
        logger.warning("[ERROR] 빈 파일명 전송됨")
        return jsonify({"error": "선택된 파일이 없습니다."}), 400
    
    filename = file.filename
    if not filename.lower().endswith(".csv"):
        logger.warning("[ERROR] CSV가 아닌 파일 확장자: %s", filename)
        return jsonify({"error": "지원하지 않는 파일 형식입니다. .csv 확장자 파일만 업로드할 수 있습니다."}), 400
    
    raw_bytes = file.read()
    file_size = len(raw_bytes)
    logger.info("[FILE] name=%s size=%d bytes", filename, file_size)
    
    if file_size == 0:
        logger.warning("[ERROR] 내용이 비어있는 파일")
        return jsonify({"error": "파일 내용이 비어 있습니다. 유효한 CSV 데이터를 업로드해 주세요."}), 400
    
    text_content, successful_encoding = try_decode_csv(raw_bytes)
    if text_content is None:
        logger.error("[ERROR] 모든 인코딩 시도 실패")
        return jsonify({"error": "파일 인코딩을 인식할 수 없습니다. (utf-8 또는 cp949 지원)"}), 400
    
    skipped_rows = detect_header_row(text_content)
    
    try:
        df = pd.read_csv(io.StringIO(text_content), skiprows=skipped_rows)
        df.columns = [str(c).strip() for c in df.columns]
    except Exception as e:
        logger.error("[ERROR] CSV 파싱 중 오류: %s\n%s", str(e), traceback.format_exc())
        return jsonify({"error": f"CSV 데이터를 읽는 중 오류가 발생했습니다: {str(e)}"}), 400
    
    rows_count, cols_count = df.shape
    logger.info("[CSV] parsing 완료 rows=%d columns=%d", rows_count, cols_count)
    
    diagnosis_result = diagnose_dataframe(df, filename, file_size, successful_encoding, skipped_rows)

    CURRENT_DATASET.clear()
    CURRENT_DATASET.update({
        "df": df,
        "filename": filename,
        "file_size": file_size,
        "encoding": successful_encoding,
        "skipped_rows": skipped_rows,
        "diagnosis": diagnosis_result
    })
    
    logger.info("[RESPONSE] status=200 filename=%s rows=%d cols=%d warnings=%d", 
                filename, rows_count, cols_count, len(diagnosis_result["warnings"]))
    return jsonify(diagnosis_result), 200

@app.route("/suggest", methods=["POST"])
def suggest():
    """
    POST /suggest
    진단 요약과 도구 카탈로그를 Gemini에게 전달하여 3~5개 분석 제안 수신
    """
    logger.info("[REQUEST] POST /suggest")
    
    if "df" not in CURRENT_DATASET or "diagnosis" not in CURRENT_DATASET:
        logger.warning("[ERROR] 데이터셋 미로드 상태에서 suggest 요청")
        return jsonify({"error": "분석할 데이터셋이 없습니다. 먼저 CSV 파일을 업로드해 주세요."}), 400
    
    if not GEMINI_API_KEY or GEMINI_API_KEY.strip() == "your_gemini_api_key_here":
        logger.error("[ERROR] GEMINI_API_KEY 미설정")
        return jsonify({"error": "Gemini API 키가 설정되지 않았습니다. .env 파일에 올바른 API 키를 입력해 주세요."}), 500

    diagnosis = CURRENT_DATASET["diagnosis"]
    df = CURRENT_DATASET["df"]

    data_summary = {
        "filename": diagnosis["filename"],
        "rows": diagnosis["rows"],
        "columns": diagnosis["columns"],
        "column_names": diagnosis["column_names"][:50],
        "numeric_columns": diagnosis["numeric_columns"][:30],
        "string_columns": diagnosis["string_columns"],
        "recommended_year": diagnosis["recommended_year"],
        "aggregate_rows_count": len(diagnosis.get("aggregate_row_candidates", [])),
        "warnings": diagnosis.get("warnings", [])
    }

    catalog_info = tools.TOOL_CATALOG

    prompt = f"""
당신은 교육용 데이터 분석 웹앱의 수석 데이터 분석 어시스턴트입니다.
사용자가 업로드한 CSV 데이터셋의 진단 요약 정보와, 우리가 실제로 실행할 수 있는 '12대 분석 도구 카탈로그'를 제공합니다.

[진단 요약 정보]
{json.dumps(data_summary, ensure_ascii=False, indent=2)}

[실행 가능한 12대 분석 도구 카탈로그]
{json.dumps(catalog_info, ensure_ascii=False, indent=2)}

[절대 규칙]
1. 반드시 도구 카탈로그의 'name'에 정의된 도구만 선택해야 합니다. 카탈로그에 없는 가상의 도구는 절대 제안할 수 없습니다.
2. 파라미터(params)에 들어갈 열 이름은 반드시 위 진단 정보의 실제 열 이름 목록(column_names, numeric_columns, string_columns)에 존재하는 것만 사용해야 합니다.
3. 7~12번 도구는 reference_period(기준시점)가 필수입니다. 권장 기준연도({diagnosis['recommended_year']}) 또는 데이터가 풍부한 연도를 기준으로 설정하십시오.
4. 분석 제안은 이 데이터셋의 특성에 맞춰 가장 통찰을 줄 수 있는 3~5개를 선별하십시오.
5. 계산은 당신이 하지 않습니다. 당신은 제안(tool, params, why, caution)만 합니다.
6. 응답은 반드시 마크다운 백틱(```) 없이 순수 JSON 형식만 반환하십시오.

[출력 JSON 형식]
{{
  "suggestions": [
    {{
      "tool": "top_bottom_n",
      "params": {{
        "reference_period": "{diagnosis['recommended_year']}",
        "label_column": "{data_summary['string_columns'][0] if data_summary['string_columns'] else 'Country Name'}",
        "n": 10,
        "exclude_aggregates": true
      }},
      "reference_period": "{diagnosis['recommended_year']}",
      "why": "이 분석이 이 데이터에서 의미 있는 이유를 초보자도 알기 쉽게 설명",
      "caution": "해석할 때 주의할 점"
    }}
  ]
}}
"""

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("[AI] suggest 요청 시작 (model=%s)", GEMINI_MODEL)
        
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2
            )
        )
        logger.info("[AI] suggest 응답 수신 완료")

        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

        parsed = json.loads(raw_text)
        suggestions = parsed.get("suggestions", [])
        logger.info("[AI] JSON 파싱 결과: %d개 제안 도출", len(suggestions))

        valid_suggestions = []
        for s in suggestions:
            tool_name = s.get("tool")
            if tool_name in tools.TOOL_FUNCTIONS:
                if "params" not in s or not isinstance(s["params"], dict):
                    s["params"] = {}
                if "reference_period" not in s and "reference_period" in s["params"]:
                    s["reference_period"] = str(s["params"]["reference_period"])
                valid_suggestions.append(s)
            else:
                logger.warning("[AI] 카탈로그에 없는 허위 도구 제안 무시: %s", tool_name)

        if not valid_suggestions:
            rec_y = str(diagnosis["recommended_year"] or "2020")
            lbl_col = data_summary["string_columns"][0] if data_summary["string_columns"] else "Country Name"
            valid_suggestions = [
                {
                    "tool": "top_bottom_n",
                    "params": {"reference_period": rec_y, "label_column": lbl_col, "n": 10, "exclude_aggregates": True},
                    "reference_period": rec_y,
                    "why": f"{rec_y}년 기준 인터넷 보급률이 가장 높은 국가와 가장 낮은 국가 순위를 한눈에 파악합니다.",
                    "caution": "인구 규모가 작은 소국이 상위/하위 극단에 포함될 수 있습니다."
                },
                {
                    "tool": "distribution",
                    "params": {"reference_period": rec_y, "bins": 5, "exclude_aggregates": True},
                    "reference_period": rec_y,
                    "why": f"전 세계 국가들의 인터넷 보급률이 어느 구간에 집중되어 있는지 분포를 확인합니다.",
                    "caution": "특정 구간에 국가들이 몰려 있을 경우 구간 간격에 따라 형태가 달라 보일 수 있습니다."
                },
                {
                    "tool": "trend_line",
                    "params": {"label_column": lbl_col, "exclude_aggregates": False},
                    "reference_period": "전체 시계열",
                    "why": "대표 국가들의 연도별 인터넷 보급률 성장 추이를 선 그래프로 비교합니다.",
                    "caution": "과거 연도에는 조사가 누락된 결측치가 존재할 수 있습니다."
                }
            ]

        logger.info("[RESPONSE] status=200 suggestions=%d", len(valid_suggestions))
        return jsonify({"suggestions": valid_suggestions}), 200

    except Exception as e:
        logger.error("[ERROR] suggest API 호출 중 예외: %s\n%s", str(e), traceback.format_exc())
        return jsonify({"error": f"AI 분석 제안 생성 중 오류가 발생했습니다: {str(e)}"}), 500

@app.route("/run", methods=["POST"])
def run_tool():
    """
    POST /run
    선택된 분석 도구를 Pandas로 직접 실행하고 계산 결과와 차트 데이터를 반환합니다.
    """
    logger.info("[REQUEST] POST /run")
    
    if "df" not in CURRENT_DATASET:
        logger.warning("[ERROR] 데이터셋 미로드 상태에서 run 요청")
        return jsonify({"error": "분석할 데이터셋이 없습니다. 먼저 CSV 파일을 업로드해 주세요."}), 400
        
    data = request.get_json(silent=True) or {}
    tool_name = data.get("tool")
    params = data.get("params", {})
    
    if not tool_name:
        logger.warning("[ERROR] 도구명 미전송")
        return jsonify({"error": "실행할 도구(tool)명이 전달되지 않았습니다."}), 400
        
    if tool_name not in tools.TOOL_FUNCTIONS:
        logger.warning("[ERROR] 등록되지 않은 도구 요청: %s", tool_name)
        return jsonify({"error": f"도구 목록에 존재하지 않는 도구입니다: '{tool_name}'"}), 400
        
    catalog_entry = next((c for c in tools.TOOL_CATALOG if c["name"] == tool_name), None)
    if catalog_entry and catalog_entry.get("requires_reference_period"):
        ref_period = params.get("reference_period") or data.get("reference_period")
        if not ref_period:
            logger.warning("[ERROR] 도구 %s에 필수인 기준시점 누락", tool_name)
            return jsonify({"error": f"도구 '{tool_name}'을(를) 실행하려면 기준 시점(reference_period)이 반드시 필요합니다."}), 400
        params["reference_period"] = str(ref_period)

    df = CURRENT_DATASET["df"]
    
    try:
        result = tools.TOOL_FUNCTIONS[tool_name](df, **params)
        
        if isinstance(result, dict) and "error" in result:
            logger.warning("[ERROR] 도구 %s 실행 실패: %s", tool_name, result["error"])
            return jsonify(result), 400
            
        used_rows = result.get("used_rows_count", len(df))
        excluded_rows = result.get("excluded_rows_count", 0)
        
        logger.info("[TOOL] 도구명=%s 파라미터=%s 사용행=%s 제외행=%s", 
                    tool_name, params, used_rows, excluded_rows)
                    
        CURRENT_DATASET["last_run_result"] = result
        
        logger.info("[RESPONSE] status=200 tool=%s", tool_name)
        return jsonify(result), 200
        
    except Exception as e:
        logger.error("[ERROR] 도구 %s 실행 중 예외: %s\n%s", tool_name, str(e), traceback.format_exc())
        return jsonify({"error": f"도구 실행 중 오류가 발생했습니다: {str(e)}"}), 500

@app.route("/explain", methods=["POST"])
def explain():
    """
    POST /explain
    모드 A / 모드 B 비교 보고서 생성
    - 모드 A (비교용, 의도적 허술): 진단 요약만 전달, Pandas 계산결과 미제공, 자유 보고서 작성 요청
    - 모드 B (정식): Pandas 계산 결과만 전달, 8대 엄격 규칙 및 7개 고정 섹션 형식 준수
    """
    logger.info("[REQUEST] POST /explain")
    
    if "df" not in CURRENT_DATASET or "diagnosis" not in CURRENT_DATASET:
        logger.warning("[ERROR] 데이터셋 미로드 상태에서 explain 요청")
        return jsonify({"error": "분석할 데이터셋이 없습니다. 먼저 CSV 파일을 업로드해 주세요."}), 400
        
    if not GEMINI_API_KEY or GEMINI_API_KEY.strip() == "your_gemini_api_key_here":
        logger.error("[ERROR] GEMINI_API_KEY 미설정")
        return jsonify({"error": "Gemini API 키가 설정되지 않았습니다. .env 파일을 확인해 주세요."}), 500

    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "B").upper()
    if mode not in ["A", "B"]:
        mode = "B"

    diagnosis = CURRENT_DATASET["diagnosis"]
    tool_result = data.get("tool_result") or CURRENT_DATASET.get("last_run_result")

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        if mode == "A":
            # -------------------------------------------------------------
            # 모드 A (비교용, 의도적으로 허술하게):
            # 진단 요약만 전달하고 "이 데이터를 분석해서 보고서를 써줘"라고 요청.
            # Pandas 계산 결과를 주지 않는다.
            # -------------------------------------------------------------
            logger.info("[AI] explain 요청 시작 (모드 A)")
            
            prompt_a = f"""
다음은 사용자가 업로드한 CSV 데이터셋의 진단 요약 정보입니다.
이 데이터를 분석해서 전반적인 데이터 분석 보고서를 작성해 주세요.

[데이터 진단 요약]
- 파일명: {diagnosis['filename']}
- 데이터 크기: {diagnosis['rows']}행 x {diagnosis['columns']}열
- 포함된 열 이름: {', '.join(diagnosis['column_names'][:25])}
- 권장 기준연도: {diagnosis['recommended_year']}
- 발견된 경고: {diagnosis.get('warnings', [])}

자유롭게 이 지표의 의미와 국가들의 현황을 추정하여 분석 보고서를 작성하십시오.
"""
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt_a,
                config=types.GenerateContentConfig(temperature=0.7)
            )
            report_text = response.text
            logger.info("[AI] explain 응답 수신 (모드 A)")
            logger.info("[RESPONSE] status=200 mode=A")
            return jsonify({
                "mode": "A",
                "mode_title": "모드 A (비교용: 계산 결과 없이 AI가 추정한 보고서)",
                "report": report_text
            }), 200

        else:
            # -------------------------------------------------------------
            # 모드 B (정식):
            # Pandas가 계산한 결과(표, 기준시점, 제외 행 수)만 전달하고
            # 8대 규칙과 지정된 7개 마크다운 형식을 지켜 엄격하게 해석하게 한다.
            # -------------------------------------------------------------
            if not tool_result:
                logger.warning("[ERROR] 모드 B 실행 시 선행 계산 결과(tool_result) 부재")
                return jsonify({
                    "error": "모드 B는 먼저 분석 도구(/run)를 실행하여 산출된 정확한 계산 결과가 필요합니다."
                }), 400

            logger.info("[AI] explain 요청 시작 (모드 B)")

            calculation_payload = {
                "tool": tool_result.get("tool"),
                "reference_period": tool_result.get("reference_period"),
                "used_rows_count": tool_result.get("used_rows_count"),
                "excluded_rows_count": tool_result.get("excluded_rows_count"),
                "excluded_reason": tool_result.get("excluded_reason"),
                "used_columns": tool_result.get("used_columns"),
                "unit": tool_result.get("unit"),
                "caution": tool_result.get("caution"),
                "result_data": tool_result.get("result")
            }

            prompt_b = f"""
당신은 교육용 데이터 분석 웹앱의 수석 데이터 해석 전문가입니다.
Python(Pandas)이 실제로 계산한 정밀 분석 결과가 아래에 주어집니다.
당신은 새로운 숫자를 계산하거나 추측하지 말고, 오직 제공된 계산 결과만을 근거로 객관적인 해석 보고서를 작성해야 합니다.

[Pandas 실제 계산 결과]
{json.dumps(calculation_payload, ensure_ascii=False, indent=2)}

[지표 메타 정보]
- 파일명: {diagnosis['filename']}
- 총 데이터 행 수: {diagnosis['rows']}행

[해석 시 반드시 지켜야 할 8대 절대 규칙]
1. 제공된 계산 결과에 있는 숫자만 사용한다.
2. 직접 계산하거나 추정하지 않는다.
3. 데이터에 없는 국가·연도·열을 언급하지 않는다.
4. 기준 시점과 제외된 행 수를 반드시 본문에 명시한다.
5. 상관관계를 인과관계로 표현하지 않는다.
6. 지표의 정의와 출처를 보고서 앞부분에 표기한다.
7. 확인할 수 없는 것은 "이 데이터로는 확인할 수 없다"라고 쓴다.
8. 중학생도 이해할 수 있게 쓰되 정확성을 희생하지 않는다.

[반드시 준수해야 하는 출력 형식]
## 무엇을 봤는가
## 데이터 출처와 정의
## 기준 시점과 제외된 항목
## 계산 결과
## 읽어낼 수 있는 것
## 이 데이터로는 알 수 없는 것
## 다음에 확인해볼 것
"""

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt_b,
                config=types.GenerateContentConfig(temperature=0.2)
            )
            report_text = response.text
            logger.info("[AI] explain 응답 수신 (모드 B)")
            logger.info("[RESPONSE] status=200 mode=B")
            return jsonify({
                "mode": "B",
                "mode_title": "모드 B (정식: Pandas 계산 수치 엄격 기반 보고서)",
                "report": report_text,
                "calculation_used": calculation_payload
            }), 200

    except Exception as e:
        logger.error("[ERROR] explain API 호출 중 예외: %s\n%s", str(e), traceback.format_exc())
        return jsonify({"error": f"AI 해석 생성 중 오류가 발생했습니다: {str(e)}"}), 500

if __name__ == "__main__":
    logger.info("[SERVER] Starting Data Insight Builder backend...")
    app.run(host="127.0.0.1", port=5000, debug=FLASK_DEBUG)
