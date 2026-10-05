import re
import math
import pandas as pd
import numpy as np

# 집계 행(World, OECD, Region, Income 그룹 등) 감지 키워드
AGGREGATE_KEYWORDS = [
    "world", "oecd", "euro area", "european union", "high income", "low income",
    "middle income", "upper middle income", "lower middle income", "income", "area",
    "total", "latin america", "sub-saharan", "asia", "arab world", "caribbean",
    "ida & ibrd", "dividend", "small states", "demographic dividend"
]

def sanitize_value(val):
    """NaN, Inf, numpy 타입을 JSON 직렬화 가능한 순수 파이썬 타입으로 변환"""
    if val is None:
        return None
    if isinstance(val, (float, np.floating)):
        if math.isnan(val) or math.isinf(val):
            return None
        return float(val)
    if isinstance(val, (int, np.integer)):
        return int(val)
    if isinstance(val, dict):
        return {k: sanitize_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [sanitize_value(v) for v in val]
    if pd.isna(val):
        return None
    return val

def find_aggregate_indices(df: pd.DataFrame, label_column: str = None) -> set:
    """라벨 열에서 집계 키워드가 포함된 행 인덱스 세트를 반환합니다."""
    if df.empty:
        return set()
        
    target_col = label_column
    if not target_col or target_col not in df.columns:
        str_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        target_col = str_cols[0] if str_cols else df.columns[0]
        
    agg_indices = set()
    for idx, val in df[target_col].dropna().items():
        v_str = str(val).lower()
        if any(kw in v_str for kw in AGGREGATE_KEYWORDS):
            agg_indices.add(idx)
    return agg_indices

def filter_aggregates_helper(df: pd.DataFrame, exclude_aggregates: bool = True, label_column: str = None):
    """
    집계 행 제외 처리 헬퍼 함수.
    (필터링된 df, 제외된 행 수, 제외 사유) 튜플을 반환합니다.
    """
    if not exclude_aggregates:
        return df.copy(), 0, "집계 행 포함 (제외 안 함)"
        
    agg_indices = find_aggregate_indices(df, label_column)
    if not agg_indices:
        return df.copy(), 0, "감지된 집계 행 없음"
        
    filtered_df = df.drop(index=list(agg_indices))
    excluded_count = len(agg_indices)
    reason = f"전 세계/지역 집계 행(World, OECD, 소득그룹 등) {excluded_count}개 제외"
    return filtered_df, excluded_count, reason


# -------------------------------------------------------------
# 1. reshape_wide_to_long: 연도 열을 행으로 접기 (Melt)
# -------------------------------------------------------------
def reshape_wide_to_long(df: pd.DataFrame, id_vars=None, exclude_aggregates: bool = True, **kwargs):
    work_df, excluded_count, excluded_reason = filter_aggregates_helper(df, exclude_aggregates)
    
    year_pattern = re.compile(r"^(19|20)\d{2}$")
    year_cols = [c for c in work_df.columns if year_pattern.match(str(c).strip())]
    
    if not year_cols:
        return {"error": "언피벗할 4자리 연도 열(예: 1960~2025)을 찾을 수 없습니다."}
        
    if id_vars is None:
        id_vars = [c for c in work_df.columns if c not in year_cols and not str(c).startswith("Unnamed:")]
        
    melted_df = pd.melt(
        work_df,
        id_vars=id_vars,
        value_vars=year_cols,
        var_name="year",
        value_name="value"
    )
    
    valid_melted = melted_df.dropna(subset=["value"]).copy()
    preview = valid_melted.head(50).to_dict(orient="records")
    
    return sanitize_value({
        "tool": "reshape_wide_to_long",
        "reference_period": "전체 연도",
        "used_rows_count": len(work_df),
        "excluded_rows_count": excluded_count,
        "excluded_reason": excluded_reason,
        "used_columns": id_vars + year_cols,
        "unit": "행 (Wide -> Long 변환)",
        "caution": "연도 열을 행으로 접으면 행 수가 대폭 증가하므로 전체 테이블 크기에 주의해야 합니다.",
        "result": {
            "original_wide_shape": list(work_df.shape),
            "melted_rows_count": len(melted_df),
            "valid_data_rows_count": len(valid_melted),
            "preview_50_rows": preview
        }
    })


# -------------------------------------------------------------
# 2. quality_report: 결측·중복·타입·이상한 열 이름
# -------------------------------------------------------------
def quality_report(df: pd.DataFrame, exclude_aggregates: bool = False, **kwargs):
    work_df, excluded_count, excluded_reason = filter_aggregates_helper(df, exclude_aggregates)
    total_rows = len(work_df)
    
    col_reports = []
    unnamed_cols = []
    all_nan_cols = []
    
    for col in work_df.columns:
        col_str = str(col).strip()
        if col_str.startswith("Unnamed:") or col_str == "":
            unnamed_cols.append(col)
            
        missing_cnt = int(work_df[col].isna().sum())
        missing_pct = round((missing_cnt / total_rows) * 100, 2) if total_rows > 0 else 0.0
        
        if missing_cnt == total_rows:
            all_nan_cols.append(col)
            
        col_reports.append({
            "column": col,
            "dtype": str(work_df[col].dtype),
            "missing_count": missing_cnt,
            "missing_percent": missing_pct,
            "unique_count": int(work_df[col].nunique(dropna=True))
        })
        
    duplicate_rows = int(work_df.duplicated().sum())
    
    return sanitize_value({
        "tool": "quality_report",
        "reference_period": "전체",
        "used_rows_count": total_rows,
        "excluded_rows_count": excluded_count,
        "excluded_reason": excluded_reason,
        "used_columns": list(work_df.columns),
        "unit": "건수 / %",
        "caution": "결측률이 50%를 넘는 열은 통계 분석 시 편향이 발생할 수 있습니다.",
        "result": {
            "total_rows": total_rows,
            "total_columns": len(work_df.columns),
            "duplicate_rows": duplicate_rows,
            "unnamed_columns": unnamed_cols,
            "empty_columns": all_nan_cols,
            "column_details": col_reports
        }
    })


# -------------------------------------------------------------
# 3. year_coverage: 연도별 값 개수와 권장 기준연도
# -------------------------------------------------------------
def year_coverage(df: pd.DataFrame, exclude_aggregates: bool = True, **kwargs):
    work_df, excluded_count, excluded_reason = filter_aggregates_helper(df, exclude_aggregates)
    total_rows = len(work_df)
    
    year_pattern = re.compile(r"^(19|20)\d{2}$")
    year_cols = [c for c in work_df.columns if year_pattern.match(str(c).strip())]
    
    if not year_cols:
        return {"error": "분석 가능한 4자리 연도 열을 찾을 수 없습니다."}
        
    sorted_years = sorted(year_cols, key=lambda x: int(re.search(r"\d+", str(x)).group()))
    
    coverage_list = []
    chart_labels = []
    chart_values = []
    
    best_count = -1
    recommended_year = None
    
    for y_col in reversed(sorted_years):
        cnt = int(work_df[y_col].notna().sum())
        if cnt > best_count and cnt >= (total_rows * 0.5):
            best_count = cnt
            recommended_year = str(y_col)
            
    if not recommended_year and sorted_years:
        recommended_year = str(sorted_years[-1])
        
    for y_col in sorted_years:
        valid_cnt = int(work_df[y_col].notna().sum())
        ratio = round((valid_cnt / total_rows) * 100, 2) if total_rows > 0 else 0.0
        coverage_list.append({
            "year": str(y_col),
            "valid_count": valid_cnt,
            "valid_ratio_percent": ratio
        })
        chart_labels.append(str(y_col))
        chart_values.append(valid_cnt)
        
    return sanitize_value({
        "tool": "year_coverage",
        "reference_period": "전체 연도 범위",
        "used_rows_count": total_rows,
        "excluded_rows_count": excluded_count,
        "excluded_reason": excluded_reason,
        "used_columns": sorted_years,
        "unit": "개수(건)",
        "caution": "연도별로 집계된 국가 수가 다를 수 있으므로 시계열 비교 시 표본 변화에 유의해야 합니다.",
        "result": {
            "recommended_year": recommended_year,
            "total_sample_units": total_rows,
            "coverage_table": coverage_list
        },
        "chart_data": {
            "type": "bar",
            "labels": chart_labels,
            "datasets": [
                {
                    "label": "연도별 유효 데이터 개수",
                    "data": chart_values
                }
            ]
        }
    })


# -------------------------------------------------------------
# 4. detect_aggregates: 집계 행 후보 탐지
# -------------------------------------------------------------
def detect_aggregates(df: pd.DataFrame, label_column: str = None, **kwargs):
    target_col = label_column
    if not target_col or target_col not in df.columns:
        str_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        target_col = str_cols[0] if str_cols else df.columns[0]
        
    agg_candidates = []
    for idx, val in df[target_col].dropna().items():
        v_str = str(val).lower()
        matched_kw = [kw for kw in AGGREGATE_KEYWORDS if kw in v_str]
        if matched_kw:
            agg_candidates.append({
                "row_index": int(idx),
                "label": str(val),
                "matched_keywords": matched_kw
            })
            
    total_rows = len(df)
    agg_count = len(agg_candidates)
    
    return sanitize_value({
        "tool": "detect_aggregates",
        "reference_period": "전체",
        "used_rows_count": total_rows,
        "excluded_rows_count": 0,
        "excluded_reason": "집계 행 탐지 목적이므로 모든 행 검사",
        "used_columns": [target_col],
        "unit": "행 수",
        "caution": "집계 행은 개별 국가 분석 시 이중 합산 위험이 있으므로 국가 간 비교 분석 시 제외하는 것이 표준입니다.",
        "result": {
            "target_column": target_col,
            "detected_count": agg_count,
            "aggregate_ratio_percent": round((agg_count / total_rows) * 100, 2) if total_rows > 0 else 0.0,
            "aggregate_rows": agg_candidates
        }
    })


# -------------------------------------------------------------
# 5. describe_numeric: 개수·평균·중앙값·최소·최대·표준편차
# -------------------------------------------------------------
def describe_numeric(df: pd.DataFrame, column: str = None, reference_period: str = None, exclude_aggregates: bool = True, **kwargs):
    target_col = column or reference_period
    if not target_col:
        num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        if not num_cols:
            return {"error": "분석할 숫자형 열을 찾을 수 없습니다."}
        target_col = num_cols[-1]

    if target_col not in df.columns:
        return {"error": f"데이터셋에 열 '{target_col}'이 존재하지 않습니다."}

    work_df, agg_excluded, agg_reason = filter_aggregates_helper(df, exclude_aggregates)
    
    series = pd.to_numeric(work_df[target_col], errors="coerce")
    valid_series = series.dropna()
    
    nan_count = int(series.isna().sum())
    used_count = len(valid_series)
    total_excluded = agg_excluded + nan_count
    
    reason_parts = []
    if agg_excluded > 0:
        reason_parts.append(agg_reason)
    if nan_count > 0:
        reason_parts.append(f"값 누락(NaN) {nan_count}개 제외")
    combined_reason = ", ".join(reason_parts) if reason_parts else "제외된 행 없음"

    if valid_series.empty:
        return {"error": f"'{target_col}' 열에 유효한 수치 데이터가 없습니다."}

    stats = {
        "count": int(valid_series.count()),
        "mean": round(float(valid_series.mean()), 4),
        "std": round(float(valid_series.std()), 4) if len(valid_series) > 1 else 0.0,
        "median": round(float(valid_series.median()), 4),
        "min": round(float(valid_series.min()), 4),
        "q25": round(float(valid_series.quantile(0.25)), 4),
        "q75": round(float(valid_series.quantile(0.75)), 4),
        "max": round(float(valid_series.max()), 4)
    }

    return sanitize_value({
        "tool": "describe_numeric",
        "reference_period": str(target_col),
        "used_rows_count": used_count,
        "excluded_rows_count": total_excluded,
        "excluded_reason": combined_reason,
        "used_columns": [target_col],
        "unit": "수치 요약 통계",
        "caution": "데이터 분포가 한쪽으로 치우쳐 있는 경우 평균보다 중앙값이 더 신뢰할 수 있는 대표값입니다.",
        "result": {
            "column": target_col,
            "statistics": stats
        }
    })


# -------------------------------------------------------------
# 6. trend_line: 선택 항목들의 연도별 추이 (선 차트)
# -------------------------------------------------------------
def trend_line(df: pd.DataFrame, label_column: str = None, items: list = None, start_year: str = None, end_year: str = None, exclude_aggregates: bool = False, **kwargs):
    target_label_col = label_column
    if not target_label_col or target_label_col not in df.columns:
        str_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        target_label_col = str_cols[0] if str_cols else df.columns[0]

    work_df, agg_excluded, agg_reason = filter_aggregates_helper(df, exclude_aggregates, target_label_col)

    year_pattern = re.compile(r"^(19|20)\d{2}$")
    year_cols = [c for c in work_df.columns if year_pattern.match(str(c).strip())]
    if not year_cols:
        return {"error": "시계열 추이를 그릴 4자리 연도 열을 찾을 수 없습니다."}

    sorted_years = sorted(year_cols, key=lambda x: int(re.search(r"\d+", str(x)).group()))

    if start_year and str(start_year) in sorted_years:
        sorted_years = [y for y in sorted_years if int(y) >= int(start_year)]
    if end_year and str(end_year) in sorted_years:
        sorted_years = [y for y in sorted_years if int(y) <= int(end_year)]

    if not items:
        row_valid_counts = work_df[sorted_years].apply(pd.to_numeric, errors="coerce").notna().sum(axis=1)
        top_indices = row_valid_counts.nlargest(5).index
        items = work_df.loc[top_indices, target_label_col].tolist()

    sub_df = work_df[work_df[target_label_col].astype(str).isin([str(it) for it in items])].copy()

    datasets = []
    line_summary = []
    
    for _, row in sub_df.iterrows():
        item_name = str(row[target_label_col])
        values = []
        for y in sorted_years:
            val = pd.to_numeric(row[y], errors="coerce")
            values.append(round(float(val), 4) if pd.notna(val) else None)
        
        datasets.append({
            "label": item_name,
            "data": values
        })
        
        valid_vals = [v for v in values if v is not None]
        line_summary.append({
            "item": item_name,
            "first_valid_val": valid_vals[0] if valid_vals else None,
            "latest_valid_val": valid_vals[-1] if valid_vals else None,
            "valid_data_points": len(valid_vals)
        })

    return sanitize_value({
        "tool": "trend_line",
        "reference_period": f"{sorted_years[0]} ~ {sorted_years[-1]}" if sorted_years else "전체",
        "used_rows_count": len(sub_df),
        "excluded_rows_count": len(df) - len(sub_df),
        "excluded_reason": f"선택된 {len(sub_df)}개 항목 외 {len(df) - len(sub_df)}개 행 분석 대상 제외",
        "used_columns": [target_label_col] + sorted_years,
        "unit": "연도별 지표값",
        "caution": "연도별로 특정 시점에 조사가 이루어지지 않아 값이 누락(None)된 구간이 존재할 수 있습니다.",
        "result": {
            "selected_items": items,
            "years_range": sorted_years,
            "summary": line_summary
        },
        "chart_data": {
            "type": "line",
            "labels": sorted_years,
            "datasets": datasets
        }
    })


# -------------------------------------------------------------
# 7. top_bottom_n: 기준연도 기준 상위·하위 N (수평 막대 차트)
# -------------------------------------------------------------
def top_bottom_n(df: pd.DataFrame, reference_period: str = None, label_column: str = None, n: int = 10, order: str = "both", exclude_aggregates: bool = True, **kwargs):
    if not reference_period:
        return {"error": "기준 시점(reference_period)은 필수 파라미터입니다."}

    if reference_period not in df.columns:
        return {"error": f"데이터셋에 기준 시점 열 '{reference_period}'이 존재하지 않습니다."}

    target_label_col = label_column
    if not target_label_col or target_label_col not in df.columns:
        str_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        target_label_col = str_cols[0] if str_cols else df.columns[0]

    work_df, agg_excluded, agg_reason = filter_aggregates_helper(df, exclude_aggregates, target_label_col)

    work_df["__val__"] = pd.to_numeric(work_df[reference_period], errors="coerce")
    valid_df = work_df.dropna(subset=["__val__"]).copy()
    
    nan_count = len(work_df) - len(valid_df)
    total_excluded = agg_excluded + nan_count

    reason_parts = []
    if agg_excluded > 0:
        reason_parts.append(agg_reason)
    if nan_count > 0:
        reason_parts.append(f"기준시점({reference_period}) 값 누락 {nan_count}개 제외")
    combined_reason = ", ".join(reason_parts) if reason_parts else "제외된 행 없음"

    if valid_df.empty:
        return {"error": f"기준시점 '{reference_period}'에 유효한 데이터가 없습니다."}

    n = max(1, int(n))
    top_df = valid_df.sort_values(by="__val__", ascending=False).head(n)
    bottom_df = valid_df.sort_values(by="__val__", ascending=True).head(n)

    top_items = [
        {"rank": i + 1, "label": str(r[target_label_col]), "value": round(float(r["__val__"]), 4)}
        for i, (_, r) in enumerate(top_df.iterrows())
    ]
    bottom_items = [
        {"rank": i + 1, "label": str(r[target_label_col]), "value": round(float(r["__val__"]), 4)}
        for i, (_, r) in enumerate(bottom_df.iterrows())
    ]

    chart_labels = []
    chart_data = []

    if order == "top":
        chart_labels = [it["label"] for it in reversed(top_items)]
        chart_data = [it["value"] for it in reversed(top_items)]
        chart_title = f"{reference_period}년 상위 {len(top_items)}개 항목"
    elif order == "bottom":
        chart_labels = [it["label"] for it in bottom_items]
        chart_data = [it["value"] for it in bottom_items]
        chart_title = f"{reference_period}년 하위 {len(bottom_items)}개 항목"
    else:  # both
        chart_labels = [it["label"] for it in top_items]
        chart_data = [it["value"] for it in top_items]
        chart_title = f"{reference_period}년 상위 {len(top_items)}개 항목 (하위 목록 별도 표기)"

    return sanitize_value({
        "tool": "top_bottom_n",
        "reference_period": str(reference_period),
        "used_rows_count": len(valid_df),
        "excluded_rows_count": total_excluded,
        "excluded_reason": combined_reason,
        "used_columns": [target_label_col, reference_period],
        "unit": "수치 (순위별)",
        "caution": "특정 소국이나 표본 규모가 작은 국가가 상/하위 극단치에 포함될 수 있으므로 국가 규모를 함께 확인해야 합니다.",
        "result": {
            "n": n,
            "order": order,
            "top": top_items,
            "bottom": bottom_items
        },
        "chart_data": {
            "type": "bar",
            "indexAxis": "y",
            "labels": chart_labels,
            "datasets": [
                {
                    "label": chart_title,
                    "data": chart_data
                }
            ]
        }
    })


# -------------------------------------------------------------
# 8. group_summary: 그룹별 평균·합계·개수 (막대 차트)
# -------------------------------------------------------------
def group_summary(df: pd.DataFrame, reference_period: str = None, group_column: str = None, agg_func: str = "mean", exclude_aggregates: bool = True, **kwargs):
    if not reference_period:
        return {"error": "기준 시점(reference_period)은 필수 파라미터입니다."}

    if reference_period not in df.columns:
        return {"error": f"데이터셋에 기준 시점 열 '{reference_period}'이 존재하지 않습니다."}

    target_group_col = group_column
    if not target_group_col or target_group_col not in df.columns:
        str_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c]) and not str(c).startswith("Unnamed:")]
        if len(str_cols) >= 2:
            target_group_col = str_cols[1]
        elif str_cols:
            target_group_col = str_cols[0]
        else:
            return {"error": "그룹화에 사용할 범주형(문자열) 열을 찾을 수 없습니다."}

    work_df, agg_excluded, agg_reason = filter_aggregates_helper(df, exclude_aggregates)

    work_df["__val__"] = pd.to_numeric(work_df[reference_period], errors="coerce")
    valid_df = work_df.dropna(subset=["__val__", target_group_col]).copy()

    nan_count = len(work_df) - len(valid_df)
    total_excluded = agg_excluded + nan_count

    reason_parts = []
    if agg_excluded > 0:
        reason_parts.append(agg_reason)
    if nan_count > 0:
        reason_parts.append(f"기준시점({reference_period}) 또는 그룹값 누락 {nan_count}개 제외")
    combined_reason = ", ".join(reason_parts) if reason_parts else "제외된 행 없음"

    if valid_df.empty:
        return {"error": f"기준시점 '{reference_period}'에 유효한 데이터가 없습니다."}

    func_map = {
        "mean": lambda s: round(float(s.mean()), 4),
        "sum": lambda s: round(float(s.sum()), 4),
        "median": lambda s: round(float(s.median()), 4),
        "count": lambda s: int(s.count())
    }
    chosen_func = func_map.get(agg_func.lower(), func_map["mean"])

    grouped = valid_df.groupby(target_group_col)["__val__"].agg([
        ("value", chosen_func),
        ("count", "count")
    ]).reset_index()

    grouped = grouped.sort_values(by="value", ascending=False).head(20)

    summary_list = [
        {
            "group": str(r[target_group_col]),
            "value": round(float(r["value"]), 4),
            "sample_count": int(r["count"])
        }
        for _, r in grouped.iterrows()
    ]

    labels = [it["group"] for it in summary_list]
    values = [it["value"] for it in summary_list]

    return sanitize_value({
        "tool": "group_summary",
        "reference_period": str(reference_period),
        "used_rows_count": len(valid_df),
        "excluded_rows_count": total_excluded,
        "excluded_reason": combined_reason,
        "used_columns": [target_group_col, reference_period],
        "unit": f"그룹별 {agg_func.upper()}",
        "caution": "각 그룹별 표본 개수(sample_count)의 차이가 클 경우 소수 표본 그룹의 평균은 왜곡될 수 있습니다.",
        "result": {
            "group_column": target_group_col,
            "agg_func": agg_func,
            "groups_count": len(summary_list),
            "summary_table": summary_list
        },
        "chart_data": {
            "type": "bar",
            "labels": labels,
            "datasets": [
                {
                    "label": f"그룹별 {target_group_col} ({agg_func})",
                    "data": values
                }
            ]
        }
    })


# -------------------------------------------------------------
# 9. change_rate: 두 시점 사이 변화량·변화율 (막대 차트)
# -------------------------------------------------------------
def change_rate(df: pd.DataFrame, reference_period: str = None, compare_period: str = None, label_column: str = None, n: int = 10, exclude_aggregates: bool = True, **kwargs):
    """
    9번 규칙 준수: reference_period 필수.
    두 시점 사이의 절대 변화량과 상대 변화율(%) 계산.
    """
    if not reference_period:
        return {"error": "기준 시점(reference_period)은 필수 파라미터입니다."}

    if reference_period not in df.columns:
        return {"error": f"데이터셋에 기준 시점 열 '{reference_period}'이 존재하지 않습니다."}

    target_label_col = label_column
    if not target_label_col or target_label_col not in df.columns:
        str_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        target_label_col = str_cols[0] if str_cols else df.columns[0]

    # 비교 시점 자동 결정 (기본값: 5년 또는 10년 전, 혹은 이전 연도)
    if not compare_period or compare_period not in df.columns:
        year_pattern = re.compile(r"^(19|20)\d{2}$")
        year_cols = sorted([c for c in df.columns if year_pattern.match(str(c).strip())], key=lambda x: int(x))
        if reference_period in year_cols:
            idx = year_cols.index(reference_period)
            # 5년 전 또는 10년 전 또는 바로 직전 연도
            if idx >= 5:
                compare_period = year_cols[idx - 5]
            elif idx >= 1:
                compare_period = year_cols[idx - 1]
            else:
                return {"error": "비교할 이전 시점을 결정할 수 없습니다."}
        else:
            return {"error": "비교 시점(compare_period)을 지정해 주세요."}

    work_df, agg_excluded, agg_reason = filter_aggregates_helper(df, exclude_aggregates, target_label_col)

    val_ref = pd.to_numeric(work_df[reference_period], errors="coerce")
    val_cmp = pd.to_numeric(work_df[compare_period], errors="coerce")

    valid_mask = val_ref.notna() & val_cmp.notna()
    valid_df = work_df[valid_mask].copy()

    nan_count = len(work_df) - len(valid_df)
    total_excluded = agg_excluded + nan_count

    reason_parts = []
    if agg_excluded > 0:
        reason_parts.append(agg_reason)
    if nan_count > 0:
        reason_parts.append(f"두 시점({compare_period}, {reference_period}) 중 값 누락 {nan_count}개 제외")
    combined_reason = ", ".join(reason_parts) if reason_parts else "제외된 행 없음"

    if valid_df.empty:
        return {"error": f"두 시점({compare_period}, {reference_period})에 모두 유효한 데이터가 없습니다."}

    p1 = pd.to_numeric(valid_df[compare_period])
    p2 = pd.to_numeric(valid_df[reference_period])
    
    diff = p2 - p1
    # 분모가 0에 매우 가까운 경우 안전 처리
    pct_change = np.where(p1 > 0.001, (diff / p1) * 100, np.nan)

    valid_df["__diff__"] = diff
    valid_df["__pct__"] = pct_change

    # 절대 증가량 기준 상위 N
    top_diff = valid_df.sort_values(by="__diff__", ascending=False).head(int(n))
    top_items = [
        {
            "rank": i + 1,
            "label": str(r[target_label_col]),
            "start_val": round(float(r[compare_period]), 4),
            "end_val": round(float(r[reference_period]), 4),
            "abs_change": round(float(r["__diff__"]), 4),
            "pct_change": round(float(r["__pct__"]), 2) if pd.notna(r["__pct__"]) else None
        }
        for i, (_, r) in enumerate(top_diff.iterrows())
    ]

    labels = [it["label"] for it in top_items]
    values = [it["abs_change"] for it in top_items]

    return sanitize_value({
        "tool": "change_rate",
        "reference_period": f"{compare_period} -> {reference_period}",
        "used_rows_count": len(valid_df),
        "excluded_rows_count": total_excluded,
        "excluded_reason": combined_reason,
        "used_columns": [target_label_col, compare_period, reference_period],
        "unit": "변화량(포인트 차이) / 변화율(%)",
        "caution": "초기값이 매우 작았던 국가의 경우 상대적 변화율(%)이 비정상적으로 과장되어 보일 수 있습니다.",
        "result": {
            "compare_period": compare_period,
            "reference_period": reference_period,
            "top_changers": top_items
        },
        "chart_data": {
            "type": "bar",
            "labels": labels,
            "datasets": [
                {
                    "label": f"변화량 ({compare_period} 대비 {reference_period})",
                    "data": values
                }
            ]
        }
    })


# -------------------------------------------------------------
# 10. distribution: 구간별 분포 (히스토그램)
# -------------------------------------------------------------
def distribution(df: pd.DataFrame, reference_period: str = None, bins: int = 10, exclude_aggregates: bool = True, **kwargs):
    """
    10번 규칙 준수: reference_period 필수.
    기준 시점 수치 데이터를 bins 구간으로 나누어 히스토그램 생성.
    """
    if not reference_period:
        return {"error": "기준 시점(reference_period)은 필수 파라미터입니다."}

    if reference_period not in df.columns:
        return {"error": f"데이터셋에 기준 시점 열 '{reference_period}'이 존재하지 않습니다."}

    work_df, agg_excluded, agg_reason = filter_aggregates_helper(df, exclude_aggregates)

    series = pd.to_numeric(work_df[reference_period], errors="coerce")
    valid_series = series.dropna()

    nan_count = int(series.isna().sum())
    total_excluded = agg_excluded + nan_count

    reason_parts = []
    if agg_excluded > 0:
        reason_parts.append(agg_reason)
    if nan_count > 0:
        reason_parts.append(f"기준시점({reference_period}) 값 누락 {nan_count}개 제외")
    combined_reason = ", ".join(reason_parts) if reason_parts else "제외된 행 없음"

    if valid_series.empty:
        return {"error": f"기준시점 '{reference_period}'에 유효한 수치 데이터가 없습니다."}

    bins_count = max(3, min(20, int(bins)))
    counts, bin_edges = np.histogram(valid_series, bins=bins_count)

    bin_labels = []
    bin_data = []
    distribution_table = []

    for i in range(len(counts)):
        left = round(float(bin_edges[i]), 2)
        right = round(float(bin_edges[i+1]), 2)
        label_str = f"{left} ~ {right}"
        cnt = int(counts[i])
        ratio = round((cnt / len(valid_series)) * 100, 2)
        
        bin_labels.append(label_str)
        bin_data.append(cnt)
        distribution_table.append({
            "bin_range": label_str,
            "min_val": left,
            "max_val": right,
            "count": cnt,
            "ratio_percent": ratio
        })

    return sanitize_value({
        "tool": "distribution",
        "reference_period": str(reference_period),
        "used_rows_count": len(valid_series),
        "excluded_rows_count": total_excluded,
        "excluded_reason": combined_reason,
        "used_columns": [reference_period],
        "unit": "구간별 빈도(국가 수)",
        "caution": "특정 구간에 데이터가 과도하게 밀집된 경우 구간 폭(bin size) 설정에 따라 형태가 달라 보일 수 있습니다.",
        "result": {
            "bins": bins_count,
            "min_value": round(float(valid_series.min()), 4),
            "max_value": round(float(valid_series.max()), 4),
            "distribution_table": distribution_table
        },
        "chart_data": {
            "type": "bar",
            "labels": bin_labels,
            "datasets": [
                {
                    "label": f"{reference_period}년 구간별 분포 빈도",
                    "data": bin_data
                }
            ]
        }
    })


# -------------------------------------------------------------
# 11. correlation_scatter: 두 숫자 열 상관계수 (산점도)
# -------------------------------------------------------------
def correlation_scatter(df: pd.DataFrame, reference_period: str = None, x_column: str = None, y_column: str = None, label_column: str = None, exclude_aggregates: bool = True, **kwargs):
    """
    11번 규칙 준수: reference_period 필수.
    절대 규칙: 상관계수 계산 시 "상관관계는 인과관계가 아니다" 주의사항 필수 포함!
    """
    if not reference_period:
        return {"error": "기준 시점(reference_period)은 필수 파라미터입니다."}

    target_y = y_column or reference_period
    target_x = x_column

    # x_column이 명시되지 않은 경우, 기준연도 10년 전 또는 이전 연도 자동 선정
    if not target_x or target_x not in df.columns:
        year_pattern = re.compile(r"^(19|20)\d{2}$")
        year_cols = sorted([c for c in df.columns if year_pattern.match(str(c).strip())], key=lambda x: int(x))
        if reference_period in year_cols:
            idx = year_cols.index(reference_period)
            if idx >= 10:
                target_x = year_cols[idx - 10]
            elif idx >= 1:
                target_x = year_cols[0]
        else:
            num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c != target_y]
            if num_cols:
                target_x = num_cols[0]

    if not target_x or target_x not in df.columns:
        return {"error": "비교할 X축 수치 열(또는 과거 연도)을 찾을 수 없습니다."}

    target_label_col = label_column
    if not target_label_col or target_label_col not in df.columns:
        str_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        target_label_col = str_cols[0] if str_cols else df.columns[0]

    work_df, agg_excluded, agg_reason = filter_aggregates_helper(df, exclude_aggregates, target_label_col)

    x_s = pd.to_numeric(work_df[target_x], errors="coerce")
    y_s = pd.to_numeric(work_df[target_y], errors="coerce")

    valid_mask = x_s.notna() & y_s.notna()
    valid_df = work_df[valid_mask].copy()

    nan_count = len(work_df) - len(valid_df)
    total_excluded = agg_excluded + nan_count

    reason_parts = []
    if agg_excluded > 0:
        reason_parts.append(agg_reason)
    if nan_count > 0:
        reason_parts.append(f"두 변수({target_x}, {target_y}) 중 값 누락 {nan_count}개 제외")
    combined_reason = ", ".join(reason_parts) if reason_parts else "제외된 행 없음"

    if len(valid_df) < 3:
        return {"error": "상관계수를 산출하기에 유효한 데이터 쌍(최소 3개)이 부족합니다."}

    corr = float(valid_df[target_x].astype(float).corr(valid_df[target_y].astype(float)))
    corr_round = round(corr, 4)

    # 산점도 데이터 포인트 구성
    scatter_points = []
    for _, r in valid_df.iterrows():
        scatter_points.append({
            "x": round(float(r[target_x]), 4),
            "y": round(float(r[target_y]), 4),
            "label": str(r[target_label_col])
        })

    return sanitize_value({
        "tool": "correlation_scatter",
        "reference_period": f"{target_x} vs {target_y}",
        "used_rows_count": len(valid_df),
        "excluded_rows_count": total_excluded,
        "excluded_reason": combined_reason,
        "used_columns": [target_label_col, target_x, target_y],
        "unit": "피어슨 상관계수 (r)",
        "caution": "상관관계는 인과관계가 아닙니다. 두 변수 간의 통계적 관련성이 원인과 결과를 증명하지 않습니다.",
        "result": {
            "x_column": target_x,
            "y_column": target_y,
            "pearson_correlation": corr_round,
            "correlation_strength": "강한 양의 상관관계" if corr >= 0.7 else ("뚜렷한 양의 상관관계" if corr >= 0.4 else ("약한 상관관계" if corr >= 0.1 else ("강한 음의 상관관계" if corr <= -0.7 else "상관관계 미미")))
        },
        "chart_data": {
            "type": "scatter",
            "datasets": [
                {
                    "label": f"{target_x} vs {target_y} (r={corr_round})",
                    "data": scatter_points
                }
            ]
        }
    })


# -------------------------------------------------------------
# 12. compare_one_vs_all: 특정 항목 vs 전체 평균·중앙값 (강조 막대)
# -------------------------------------------------------------
def compare_one_vs_all(df: pd.DataFrame, reference_period: str = None, target_item: str = None, label_column: str = None, exclude_aggregates: bool = True, **kwargs):
    """
    12번 규칙 준수: reference_period 필수.
    특정 국가/항목과 전체 통계치(평균, 중앙값, 최댓값, 백분위 순위) 비교.
    """
    if not reference_period:
        return {"error": "기준 시점(reference_period)은 필수 파라미터입니다."}

    if reference_period not in df.columns:
        return {"error": f"데이터셋에 기준 시점 열 '{reference_period}'이 존재하지 않습니다."}

    target_label_col = label_column
    if not target_label_col or target_label_col not in df.columns:
        str_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        target_label_col = str_cols[0] if str_cols else df.columns[0]

    work_df, agg_excluded, agg_reason = filter_aggregates_helper(df, exclude_aggregates, target_label_col)

    work_df["__val__"] = pd.to_numeric(work_df[reference_period], errors="coerce")
    valid_df = work_df.dropna(subset=["__val__"]).copy()

    nan_count = len(work_df) - len(valid_df)
    total_excluded = agg_excluded + nan_count

    reason_parts = []
    if agg_excluded > 0:
        reason_parts.append(agg_reason)
    if nan_count > 0:
        reason_parts.append(f"기준시점({reference_period}) 값 누락 {nan_count}개 제외")
    combined_reason = ", ".join(reason_parts) if reason_parts else "제외된 행 없음"

    if valid_df.empty:
        return {"error": f"기준시점 '{reference_period}'에 유효한 데이터가 없습니다."}

    # 비교할 특정 타겟 항목 선정 (기본값: Korea, Rep. 또는 첫 번째 관측치)
    if not target_item:
        korea_matches = valid_df[valid_df[target_label_col].astype(str).str.contains("Korea", case=False)]
        if not korea_matches.empty:
            target_item = str(korea_matches.iloc[0][target_label_col])
        else:
            target_item = str(valid_df.iloc[0][target_label_col])

    target_row = valid_df[valid_df[target_label_col].astype(str) == str(target_item)]
    if target_row.empty:
        return {"error": f"기준시점 '{reference_period}'에서 '{target_item}'의 유효 데이터를 찾을 수 없습니다."}

    target_val = round(float(target_row.iloc[0]["__val__"]), 4)
    all_vals = valid_df["__val__"]
    
    mean_val = round(float(all_vals.mean()), 4)
    median_val = round(float(all_vals.median()), 4)
    min_val = round(float(all_vals.min()), 4)
    max_val = round(float(all_vals.max()), 4)

    # 전체 순위 및 백분위 계산
    rank_asc = (all_vals < target_val).sum() + 1
    rank_desc = (all_vals > target_val).sum() + 1
    total_valid = len(valid_df)
    percentile = round(((all_vals <= target_val).sum() / total_valid) * 100, 2)

    diff_from_mean = round(target_val - mean_val, 4)
    diff_from_median = round(target_val - median_val, 4)

    labels = [str(target_item), "전체 평균", "전체 중앙값", "최댓값"]
    values = [target_val, mean_val, median_val, max_val]

    return sanitize_value({
        "tool": "compare_one_vs_all",
        "reference_period": str(reference_period),
        "used_rows_count": total_valid,
        "excluded_rows_count": total_excluded,
        "excluded_reason": combined_reason,
        "used_columns": [target_label_col, reference_period],
        "unit": "지표값 및 백분위 순위",
        "caution": "단일 국가 비교 시 해당 국가의 인구 규모, 경제 수준 등 배경 요인을 함께 고려해야 합니다.",
        "result": {
            "target_item": target_item,
            "target_value": target_val,
            "mean_value": mean_val,
            "median_value": median_val,
            "min_value": min_val,
            "max_value": max_val,
            "rank": int(rank_desc),
            "total_countries": total_valid,
            "percentile_rank": percentile,
            "difference_from_mean": diff_from_mean,
            "difference_from_median": diff_from_median
        },
        "chart_data": {
            "type": "bar",
            "labels": labels,
            "datasets": [
                {
                    "label": f"{target_item} vs 전체 통계 비교 ({reference_period}년)",
                    "data": values
                }
            ]
        }
    })


# -------------------------------------------------------------
# 12대 도구 카탈로그 메타데이터 및 디스패처
# -------------------------------------------------------------
TOOL_CATALOG = [
    {
        "name": "reshape_wide_to_long",
        "title": "연도 열을 행으로 접기 (Wide -> Long)",
        "requires_reference_period": False,
        "description": "가로로 나열된 연도별 열들을 하나의 '연도'와 '값' 행으로 정규화 변환합니다."
    },
    {
        "name": "quality_report",
        "title": "데이터 품질 진단 보고서",
        "requires_reference_period": False,
        "description": "결측치 비율, 중복 행, 열별 데이터 타입, 비정상 열을 종합 점검합니다."
    },
    {
        "name": "year_coverage",
        "title": "연도별 충실도 및 권장 기준연도",
        "requires_reference_period": False,
        "description": "연도별 데이터 유효 건수를 집계하고 가장 분석 적합도가 높은 최적 기준연도를 산출합니다."
    },
    {
        "name": "detect_aggregates",
        "title": "집계 행(World/OECD 등) 탐지",
        "requires_reference_period": False,
        "description": "국가가 아닌 전 세계 합산이나 지역 단위 집계 행을 탐지합니다."
    },
    {
        "name": "describe_numeric",
        "title": "기초 통계 요약 (평균·중앙값 등)",
        "requires_reference_period": False,
        "description": "개수, 평균, 중앙값, 표준편차, 4분위수, 최솟값, 최댓값을 요약합니다."
    },
    {
        "name": "trend_line",
        "title": "선택 항목들의 연도별 시계열 추이",
        "requires_reference_period": False,
        "description": "특정 국가나 항목들의 시계열 변화 흐름을 선 차트로 비교합니다."
    },
    {
        "name": "top_bottom_n",
        "title": "기준연도 기준 상위·하위 N 순위",
        "requires_reference_period": True,
        "description": "특정 기준연도를 기준으로 지표가 가장 높거나 낮은 국가 순위를 수평 막대로 도출합니다."
    },
    {
        "name": "group_summary",
        "title": "그룹별 집계 요약 (평균/합계)",
        "requires_reference_period": True,
        "description": "대륙, 소득군 또는 범주형 그룹별로 지표의 평균/합계를 계산합니다."
    },
    {
        "name": "change_rate",
        "title": "두 시점 간 변화량 및 변화율 분석",
        "requires_reference_period": True,
        "description": "과거 시점과 기준 시점 사이의 증가폭(포인트 차이)과 증가율(%)을 계산합니다."
    },
    {
        "name": "distribution",
        "title": "구간별 수치 분포 (히스토그램)",
        "requires_reference_period": True,
        "description": "기준 시점의 지표값을 여러 구간으로 나누어 국가들의 분포 빈도를 시각화합니다."
    },
    {
        "name": "correlation_scatter",
        "title": "두 시점/변수 간 상관계수 (산점도)",
        "requires_reference_period": True,
        "description": "두 수치 열 사이의 피어슨 상관계수를 계산하고 산점도를 작성합니다. (상관!=인과)"
    },
    {
        "name": "compare_one_vs_all",
        "title": "특정 항목 vs 전체 평균·중앙값 비교",
        "requires_reference_period": True,
        "description": "특정 국가가 전체 평균, 중앙값 대비 어느 위치(순위 및 백분위)에 있는지 강조 비교합니다."
    }
]

TOOL_FUNCTIONS = {
    "reshape_wide_to_long": reshape_wide_to_long,
    "quality_report": quality_report,
    "year_coverage": year_coverage,
    "detect_aggregates": detect_aggregates,
    "describe_numeric": describe_numeric,
    "trend_line": trend_line,
    "top_bottom_n": top_bottom_n,
    "group_summary": group_summary,
    "change_rate": change_rate,
    "distribution": distribution,
    "correlation_scatter": correlation_scatter,
    "compare_one_vs_all": compare_one_vs_all,
}
