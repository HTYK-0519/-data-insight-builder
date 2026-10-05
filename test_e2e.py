import os
import sys
import json

# Windows 콘솔 유니코드 이모지 출력 지원
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app import app

def run_e2e_test():
    print("=" * 60)
    print("AI Data Insight Builder - End-to-End 전체 통합 테스트")
    print("=" * 60)

    client = app.test_client()

    # 1. GET /health
    print("\n[E2E 1] GET /health (서버 및 API Key 점검)")
    res_health = client.get("/health")
    assert res_health.status_code == 200, f"Health check failed: {res_health.status_code}"
    h_data = res_health.get_json()
    print(f"-> 상태: {h_data['status']}, API Key 준비 여부: {h_data['api_key_configured']}, 모델: {h_data['model']}")

    # 2. GET /
    print("\n[E2E 2] GET / (index.html 웹 페이지 렌더링)")
    res_index = client.get("/")
    assert res_index.status_code == 200, f"GET / failed: {res_index.status_code}"
    print(f"-> 상태 코드: {res_index.status_code}, HTML 크기: {len(res_index.data):,} 바이트")

    # 3. POST /inspect
    print("\n[E2E 3] POST /inspect (실제 World Bank 지표 CSV 업로드 및 13대 진단)")
    csv_path = r"data\API_IT.NET.USER.ZS_DS2_en_csv_v2_442976\API_IT.NET.USER.ZS_DS2_en_csv_v2_442976.csv"
    with open(csv_path, "rb") as f:
        res_inspect = client.post(
            "/inspect",
            data={"file": (f, "API_IT.NET.USER.ZS.csv")},
            content_type="multipart/form-data"
        )
    assert res_inspect.status_code == 200, f"Inspect failed: {res_inspect.status_code}"
    insp = res_inspect.get_json()
    print(f"-> 파일명: {insp['filename']}")
    print(f"-> 성공 인코딩: {insp['encoding']}, 건너뛴 헤더 줄 수: {insp['skipped_rows']}줄")
    print(f"-> 데이터 형태: {insp['rows']}행 x {insp['columns']}열")
    print(f"-> 권장 기준연도: {insp['recommended_year']}년")
    print(f"-> 감지된 집계 행 후보: {len(insp['aggregate_row_candidates'])}개")
    print(f"-> 생성된 경고 수: {len(insp['warnings'])}개")

    # 4. POST /suggest
    print("\n[E2E 4] POST /suggest (Gemini API 12대 도구 기반 맞춤형 제안)")
    res_suggest = client.post("/suggest")
    assert res_suggest.status_code == 200, f"Suggest failed: {res_suggest.status_code}"
    sugg = res_suggest.get_json()
    suggestions = sugg.get("suggestions", [])
    print(f"-> 수신된 AI 분석 제안: {len(suggestions)}개")
    for idx, s in enumerate(suggestions, 1):
        print(f"   [{idx}] {s.get('tool')} (기준: {s.get('reference_period')}) - {s.get('why')[:35]}...")

    # 5. POST /run
    print("\n[E2E 5] POST /run (선택된 top_bottom_n 도구 Pandas 정밀 계산)")
    run_payload = {
        "tool": "top_bottom_n",
        "params": {
            "reference_period": "2020",
            "n": 5,
            "exclude_aggregates": True
        }
    }
    res_run = client.post("/run", json=run_payload)
    assert res_run.status_code == 200, f"Run failed: {res_run.status_code}"
    run_res = res_run.get_json()
    print(f"-> 실행 도구: {run_res['tool']}, 기준시점: {run_res['reference_period']}")
    print(f"-> 사용 행 수: {run_res['used_rows_count']}개, 제외 행 수: {run_res['excluded_rows_count']}개")
    print(f"-> 제외 사유: {run_res['excluded_reason']}")
    print(f"-> 1위 국가: {run_res['result']['top'][0]['label']} ({run_res['result']['top'][0]['value']}%)")
    print(f"-> 차트 종류: {run_res['chart_data']['type']} (indexAxis: {run_res['chart_data'].get('indexAxis')})")

    # 6. POST /explain (모드 B: 정식 보고서)
    print("\n[E2E 6] POST /explain [모드 B] (Pandas 계산 수치 기반 8대 규칙 엄격 준수 보고서)")
    res_b = client.post("/explain", json={"mode": "B"})
    assert res_b.status_code == 200, f"Explain Mode B failed: {res_b.status_code}"
    b_res = res_b.get_json()
    print("-> 모드 B 생성 완료! 보고서 미리보기:")
    preview_b = "\n".join(b_res["report"].splitlines()[:12])
    print(preview_b)

    # 7. POST /explain (모드 A: 비교용 보고서)
    print("\n[E2E 7] POST /explain [모드 A] (계산 결과 없이 AI가 추측한 비교용 보고서)")
    res_a = client.post("/explain", json={"mode": "A"})
    assert res_a.status_code == 200, f"Explain Mode A failed: {res_a.status_code}"
    a_res = res_a.get_json()
    print("-> 모드 A 생성 완료! 보고서 미리보기:")
    preview_a = "\n".join(a_res["report"].splitlines()[:6])
    print(preview_a)

    print("\n" + "=" * 60)
    print("🎉 [축하합니다!] 모든 7개 E2E 통합 테스트 단계가 완벽히 통과했습니다!")
    print("=" * 60)

if __name__ == "__main__":
    run_e2e_test()
