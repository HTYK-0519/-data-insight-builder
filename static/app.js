/**
 * AI Data Insight Builder - Frontend Logic (Vanilla JS)
 * 순수 바닐라 JavaScript + Chart.js 연동
 * 프레임워크(React, Vue, Bootstrap 등) 일절 배제
 */

// 전역 상태
let selectedCsvFile = null;
let currentChart = null;
let lastInspectData = null;
let lastRunResult = null;

// 12대 도구 카탈로그 정의 (드롭다운 채우기용)
const TOOL_DEFINITIONS = [
    { name: "top_bottom_n", title: "기준연도 기준 상위·하위 N 순위", requiresRef: true },
    { name: "trend_line", title: "선택 항목들의 연도별 시계열 추이", requiresRef: false },
    { name: "change_rate", title: "두 시점 간 변화량 및 변화율 분석", requiresRef: true },
    { name: "distribution", title: "구간별 수치 분포 (히스토그램)", requiresRef: true },
    { name: "compare_one_vs_all", title: "특정 항목 vs 전체 통계 비교 (강조 막대)", requiresRef: true },
    { name: "correlation_scatter", title: "두 시점/변수 간 상관계수 (산점도)", requiresRef: true },
    { name: "group_summary", title: "그룹별 집계 요약 (평균/합계)", requiresRef: true },
    { name: "describe_numeric", title: "기초 수치 요약 통계 (평균·중앙값)", requiresRef: false },
    { name: "year_coverage", title: "연도별 충실도 및 권장 기준연도", requiresRef: false },
    { name: "quality_report", title: "데이터 품질 진단 보고서", requiresRef: false },
    { name: "detect_aggregates", title: "집계 행(World/OECD 등) 탐지", requiresRef: false },
    { name: "reshape_wide_to_long", title: "연도 열을 행으로 접기 (Wide -> Long)", requiresRef: false }
];

let deferredInstallPrompt = null;

document.addEventListener("DOMContentLoaded", () => {
    console.log("[PAGE] 페이지 로드 완료");
    
    initPwa();
    initAuthHandlers();
    checkAuthStatus();
    initToolSelectDropdown();
    checkServerHealth();
    initUploadEvents();
    initButtonHandlers();
    initModeSelector();
    initQaEvents();
});

// -------------------------------------------------------------
// 0-A. PWA 서비스 워커 및 앱 설치 처리
// -------------------------------------------------------------
function initPwa() {
    // 1. 서비스 워커 등록
    if ("serviceWorker" in navigator) {
        window.addEventListener("load", () => {
            navigator.serviceWorker.register("/sw.js")
                .then((reg) => {
                    console.log("[PWA] Service Worker 등록 성공 (스코프:", reg.scope, ")");
                })
                .catch((err) => {
                    console.warn("[PWA] Service Worker 등록 실패:", err);
                });
        });
    }

    const btnInstall = document.getElementById("btnPwaInstall");

    // 2. 브라우저의 PWA 설치 가능 이벤트 감지
    window.addEventListener("beforeinstallprompt", (e) => {
        console.log("[PWA] beforeinstallprompt 이벤트 발생 감지");
        e.preventDefault();
        deferredInstallPrompt = e;
        if (btnInstall) {
            btnInstall.classList.remove("hidden");
        }
    });

    // 3. 설치 버튼 클릭 시 설치 프롬프트 호출
    if (btnInstall) {
        btnInstall.addEventListener("click", async () => {
            if (!deferredInstallPrompt) {
                alert("이미 앱이 설치되어 있거나 현재 환경에서 바로 설치를 지원하지 않습니다.\n브라우저 오른쪽 상단 메뉴(⋮)에서 '홈 화면에 추가' 또는 '앱 설치'를 이용해 주세요.");
                return;
            }
            deferredInstallPrompt.prompt();
            const { outcome } = await deferredInstallPrompt.userChoice;
            console.log("[PWA] 설치 프롬프트 결과:", outcome);
            deferredInstallPrompt = null;
            btnInstall.classList.add("hidden");
        });
    }

    // 4. 앱 설치 완료 이벤트
    window.addEventListener("appinstalled", () => {
        console.log("[PWA] 앱 설치 완료");
        deferredInstallPrompt = null;
        if (btnInstall) btnInstall.classList.add("hidden");
    });

    // 5. 이미 독립 실행(Standalone PWA) 모드로 실행 중인 경우 설치 버튼 숨김
    if (window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true) {
        console.log("[PWA] Standalone 모드로 실행 중");
        if (btnInstall) btnInstall.classList.add("hidden");
    }
}

// -------------------------------------------------------------
// 0-B. 사이트 접속 비밀번호 인증 및 보안 잠금 관리
// -------------------------------------------------------------
async function checkAuthStatus() {
    const lockOverlay = document.getElementById("lockScreenOverlay");
    const passwordInput = document.getElementById("inputSitePassword");
    const errorMsg = document.getElementById("lockErrorMsg");

    try {
        const res = await fetch("/api/auth/status");
        if (!res.ok) throw new Error("인증 상태 확인 실패");
        const data = await res.json();

        if (data.authenticated || !data.auth_required) {
            if (lockOverlay) lockOverlay.classList.add("hidden");
        } else {
            if (lockOverlay) {
                lockOverlay.classList.remove("hidden");
                if (errorMsg) errorMsg.classList.add("hidden");
                if (passwordInput) setTimeout(() => passwordInput.focus(), 250);
            }
        }
    } catch (err) {
        console.warn("[AUTH] 인증 상태 확인 중 통신 오류:", err);
    }
}

function handleUnauthorized() {
    const lockOverlay = document.getElementById("lockScreenOverlay");
    const errorMsg = document.getElementById("lockErrorMsg");
    const passwordInput = document.getElementById("inputSitePassword");

    if (lockOverlay) {
        lockOverlay.classList.remove("hidden");
        if (errorMsg) {
            errorMsg.textContent = "접근 권한이 만료되었거나 잠겨 있습니다. 비밀번호를 다시 입력해 주세요.";
            errorMsg.classList.remove("hidden");
        }
        if (passwordInput) {
            passwordInput.value = "";
            setTimeout(() => passwordInput.focus(), 250);
        }
    }
}

function initAuthHandlers() {
    const lockForm = document.getElementById("lockForm");
    const passwordInput = document.getElementById("inputSitePassword");
    const btnTogglePassword = document.getElementById("btnTogglePassword");
    const btnUnlock = document.getElementById("btnUnlockSite");
    const errorMsg = document.getElementById("lockErrorMsg");
    const lockOverlay = document.getElementById("lockScreenOverlay");
    const btnLockApp = document.getElementById("btnLockApp");

    // 비밀번호 보기/숨기기 토글
    if (btnTogglePassword && passwordInput) {
        btnTogglePassword.addEventListener("click", () => {
            const isPassword = passwordInput.type === "password";
            passwordInput.type = isPassword ? "text" : "password";
            btnTogglePassword.textContent = isPassword ? "🙈" : "👁️";
        });
    }

    // 잠금 해제 폼 제출
    async function submitUnlock() {
        const pwd = passwordInput ? passwordInput.value.trim() : "";
        if (!pwd) {
            if (errorMsg) {
                errorMsg.textContent = "접속 비밀번호를 입력해 주세요.";
                errorMsg.classList.remove("hidden");
            }
            if (passwordInput) passwordInput.focus();
            return;
        }

        if (btnUnlock) {
            btnUnlock.disabled = true;
            btnUnlock.innerHTML = '<span class="spinner"></span> 확인 중...';
        }
        if (errorMsg) errorMsg.classList.add("hidden");

        try {
            const res = await fetch("/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ password: pwd })
            });
            const data = await res.json();

            if (res.ok && data.success) {
                if (lockOverlay) lockOverlay.classList.add("hidden");
                if (passwordInput) passwordInput.value = "";
                checkServerHealth();
            } else {
                if (errorMsg) {
                    errorMsg.textContent = data.message || "비밀번호가 올바르지 않습니다.";
                    errorMsg.classList.remove("hidden");
                }
                if (passwordInput) {
                    passwordInput.select();
                }
            }
        } catch (err) {
            if (errorMsg) {
                errorMsg.textContent = "서버 통신 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.";
                errorMsg.classList.remove("hidden");
            }
        } finally {
            if (btnUnlock) {
                btnUnlock.disabled = false;
                btnUnlock.innerHTML = '<span class="btn-text">잠금 해제 및 접속</span>';
            }
        }
    }

    if (lockForm) {
        lockForm.addEventListener("submit", (e) => {
            e.preventDefault();
            submitUnlock();
        });
    }

    // 헤더 '잠금' 버튼 클릭 시 로그아웃 처리
    if (btnLockApp) {
        btnLockApp.addEventListener("click", async () => {
            try {
                await fetch("/api/auth/logout", { method: "POST" });
            } catch (e) {
                console.warn("[AUTH] 로그아웃 요청 실패:", e);
            }
            if (lockOverlay) lockOverlay.classList.remove("hidden");
            if (passwordInput) {
                passwordInput.value = "";
                setTimeout(() => passwordInput.focus(), 250);
            }
            if (errorMsg) errorMsg.classList.add("hidden");
        });
    }
}

// -------------------------------------------------------------
// 1. 초기화 및 서버 헬스체크
// -------------------------------------------------------------
function initToolSelectDropdown() {
    const select = document.getElementById("selectTool");
    if (!select) return;
    select.innerHTML = "";
    TOOL_DEFINITIONS.forEach(t => {
        const opt = document.createElement("option");
        opt.value = t.name;
        opt.textContent = `${t.title} [${t.name}]`;
        select.appendChild(opt);
    });
}

async function checkServerHealth() {
    const statusBadge = document.getElementById("serverStatusBadge");
    const modelBadge = document.getElementById("modelBadge");

    try {
        const res = await fetch("/health");
        if (!res.ok) throw new Error("서버 응답 오류 (HTTP " + res.status + ")");
        const data = await res.json();

        if (statusBadge) {
            if (data.api_key_configured) {
                statusBadge.textContent = "● 서버 정상 (API Key 준비됨)";
                statusBadge.className = "badge badge-success";
            } else {
                statusBadge.textContent = "⚠️ API Key 미설정 (.env 확인 필요)";
                statusBadge.className = "badge badge-info";
            }
        }
        if (modelBadge) {
            modelBadge.textContent = "Model: " + (data.model || "gemini-3.5-flash-lite");
        }
    } catch (err) {
        console.error("[ERROR] 헬스체크 실패:", err);
        if (statusBadge) {
            statusBadge.textContent = "✕ 서버 오프라인";
            statusBadge.className = "badge badge-info";
        }
    }
}

// -------------------------------------------------------------
// 2. 파일 업로드 및 드래그 앤 드롭
// -------------------------------------------------------------
function initUploadEvents() {
    const dropZone = document.getElementById("dropZone");
    const fileInput = document.getElementById("csvFileInput");
    const fileLabel = document.getElementById("selectedFileLabel");
    const btnInspect = document.getElementById("btnInspect");

    if (!dropZone || !fileInput) return;

    // 드래그 앤 드롭 시각 효과
    ["dragenter", "dragover"].forEach(name => {
        dropZone.addEventListener(name, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropZone.classList.add("dragover");
        });
    });

    ["dragleave", "drop"].forEach(name => {
        dropZone.addEventListener(name, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropZone.classList.remove("dragover");
        });
    });

    dropZone.addEventListener("drop", (e) => {
        const files = e.dataTransfer.files;
        if (files && files.length > 0) {
            handleFileSelection(files[0]);
        }
    });

    fileInput.addEventListener("change", (e) => {
        if (e.target.files && e.target.files.length > 0) {
            handleFileSelection(e.target.files[0]);
        }
    });

    function handleFileSelection(file) {
        if (!file.name.toLowerCase().endsWith(".csv")) {
            console.error("[ERROR] 확장자 오류: .csv 아님");
            alert("CSV 파일(.csv 확장자)만 업로드할 수 있습니다.");
            return;
        }

        selectedCsvFile = file;
        console.log(`[UPLOAD] 파일 선택 (${file.name}, ${file.size} bytes)`);

        if (fileLabel) {
            fileLabel.innerHTML = `선택된 파일: <strong>${file.name}</strong> (${formatBytes(file.size)})`;
        }
        if (btnInspect) {
            btnInspect.disabled = false;
        }
    }
}

// -------------------------------------------------------------
// 3. 버튼 이벤트 핸들러 바인딩
// -------------------------------------------------------------
function initButtonHandlers() {
    // Step 1: /inspect 실행
    const btnInspect = document.getElementById("btnInspect");
    if (btnInspect) {
        btnInspect.addEventListener("click", executeInspect);
    }

    // Step 2: /suggest 실행
    const btnSuggest = document.getElementById("btnSuggest");
    if (btnSuggest) {
        btnSuggest.addEventListener("click", executeSuggest);
    }

    // Step 3: /run 실행
    const btnRun = document.getElementById("btnRun");
    if (btnRun) {
        btnRun.addEventListener("click", executeRun);
    }

    // Step 4: /explain 실행
    const btnExplain = document.getElementById("btnExplain");
    if (btnExplain) {
        btnExplain.addEventListener("click", executeExplain);
    }
}

// -------------------------------------------------------------
// 4. Step 1: POST /inspect
// -------------------------------------------------------------
async function executeInspect() {
    if (!selectedCsvFile) {
        alert("먼저 CSV 파일을 선택해 주세요.");
        return;
    }

    const btnInspect = document.getElementById("btnInspect");
    setLoading(btnInspect, true, "데이터 진단 중...");
    console.log("[INSPECT] 진단 요청 시작");

    const formData = new FormData();
    formData.append("file", selectedCsvFile);

    try {
        const res = await fetch("/inspect", {
            method: "POST",
            body: formData
        });

        if (res.status === 401) {
            handleUnauthorized();
            throw new Error("접근 권한이 없습니다. 사이트 비밀번호를 입력해 주세요.");
        }

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "데이터 진단에 실패했습니다.");
        }

        lastInspectData = data;
        const warningsCount = data.warnings ? data.warnings.length : 0;
        console.log(`[INSPECT] 응답 수신 (행 수: ${data.rows}, 열 수: ${data.columns}, 경고 수: ${warningsCount})`);

        renderInspectResults(data);

        // Step 2 활성화
        const step2Card = document.getElementById("sectionStep2");
        const btnSuggest = document.getElementById("btnSuggest");
        if (step2Card) step2Card.classList.remove("disabled-card");
        if (btnSuggest) btnSuggest.disabled = false;

        // Step 3 폼의 기준연도 기본값 채우기
        const inputRef = document.getElementById("inputRefPeriod");
        if (inputRef && data.recommended_year) {
            inputRef.value = data.recommended_year;
        }

    } catch (err) {
        console.error("[ERROR] 진단 오류 발생:", err);
        alert("데이터 진단 오류: " + err.message);
    } finally {
        setLoading(btnInspect, false, "데이터 진단 시작 (/inspect)");
    }
}

function renderInspectResults(data) {
    const container = document.getElementById("inspectResultContainer");
    if (!container) return;

    document.getElementById("resFilename").textContent = `${data.filename} (${formatBytes(data.file_size_bytes)})`;
    document.getElementById("resEncoding").textContent = `${data.encoding} (헤더 건너뛴 줄: ${data.skipped_rows}줄)`;
    document.getElementById("resShape").textContent = `${data.rows.toLocaleString()}행 × ${data.columns.toLocaleString()}열`;
    document.getElementById("resRecYear").textContent = data.recommended_year ? `${data.recommended_year}년` : "전체";

    // 경고 목록
    const warnBox = document.getElementById("warningsContainer");
    const warnList = document.getElementById("warningsList");
    if (warnBox && warnList) {
        warnList.innerHTML = "";
        if (data.warnings && data.warnings.length > 0) {
            data.warnings.forEach(w => {
                const li = document.createElement("li");
                li.textContent = w;
                warnList.appendChild(li);
            });
            warnBox.classList.remove("hidden");
        } else {
            warnBox.classList.add("hidden");
        }
    }

    // 10행 미리보기 테이블
    renderPreviewTable(data.column_names, data.preview_10_rows);

    container.classList.remove("hidden");
}

function renderPreviewTable(columns, rows) {
    const thead = document.getElementById("previewTableHead");
    const tbody = document.getElementById("previewTableBody");
    if (!thead || !tbody) return;

    thead.innerHTML = "";
    tbody.innerHTML = "";

    if (!columns || !rows) return;

    // 최대 20개 열만 미리보기에 표시
    const displayCols = columns.slice(0, 20);

    displayCols.forEach(col => {
        const th = document.createElement("th");
        th.textContent = col;
        thead.appendChild(th);
    });

    rows.forEach(row => {
        const tr = document.createElement("tr");
        displayCols.forEach(col => {
            const td = document.createElement("td");
            const val = row[col];
            td.textContent = (val !== null && val !== undefined) ? String(val) : "-";
            tr.appendChild(td);
        });
        tbody.appendChild(tr);
    });
}

// -------------------------------------------------------------
// 5. Step 2: POST /suggest
// -------------------------------------------------------------
async function executeSuggest() {
    const btnSuggest = document.getElementById("btnSuggest");
    setLoading(btnSuggest, true, "Gemini 분석 제안 생성 중...");
    console.log("[SUGGEST] 제안 요청 시작");

    try {
        const res = await fetch("/suggest", {
            method: "POST",
            headers: { "Content-Type": "application/json" }
        });

        if (res.status === 401) {
            handleUnauthorized();
            throw new Error("접근 권한이 없습니다. 사이트 비밀번호를 입력해 주세요.");
        }

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "AI 분석 제안을 받아오지 못했습니다.");
        }

        const suggestions = data.suggestions || [];
        console.log(`[SUGGEST] 응답 수신 (제안 개수: ${suggestions.length})`);

        renderSuggestions(suggestions);

        // Step 3 활성화
        const step3Card = document.getElementById("sectionStep3");
        if (step3Card) step3Card.classList.remove("disabled-card");

    } catch (err) {
        console.error("[ERROR] 제안 오류 발생:", err);
        alert("AI 분석 제안 오류: " + err.message);
    } finally {
        setLoading(btnSuggest, false, "AI 분석 제안 받기");
    }
}

function renderSuggestions(suggestions) {
    const container = document.getElementById("suggestionsContainer");
    if (!container) return;

    container.innerHTML = "";

    if (!suggestions || suggestions.length === 0) {
        container.innerHTML = "<p>사용 가능한 분석 제안이 없습니다.</p>";
        container.classList.remove("hidden");
        return;
    }

    suggestions.forEach((s, idx) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";

        const def = TOOL_DEFINITIONS.find(t => t.name === s.tool);
        const title = def ? def.title : s.tool;
        const refPeriodText = s.reference_period ? `기준: ${s.reference_period}` : "전체 시계열";

        card.innerHTML = `
            <div>
                <div class="sugg-tool-name">${idx + 1}. ${title}</div>
                <div style="font-size:0.78rem; color:#64748b; margin-bottom:0.5rem;">[${s.tool}] • ${refPeriodText}</div>
                <div class="sugg-why">💡 ${s.why}</div>
                ${s.caution ? `<div class="sugg-caution">⚠️ ${s.caution}</div>` : ""}
            </div>
            <button type="button" class="btn btn-primary sugg-btn-select" data-index="${idx}">
                이 분석 선택 & 설정 적용
            </button>
        `;

        const btnSelect = card.querySelector(".sugg-btn-select");
        btnSelect.addEventListener("click", () => {
            applySuggestionToRunForm(s);
        });

        container.appendChild(card);
    });

    container.classList.remove("hidden");
}

function applySuggestionToRunForm(suggestion) {
    const selectTool = document.getElementById("selectTool");
    const inputRef = document.getElementById("inputRefPeriod");
    const inputTopN = document.getElementById("inputTopN");
    const chkExclude = document.getElementById("chkExcludeAggregates");

    if (selectTool && suggestion.tool) {
        selectTool.value = suggestion.tool;
    }

    const params = suggestion.params || {};

    if (inputRef) {
        inputRef.value = suggestion.reference_period || params.reference_period || (lastInspectData ? lastInspectData.recommended_year : "2020");
    }

    if (inputTopN && params.n) {
        inputTopN.value = params.n;
    }

    if (chkExclude && typeof params.exclude_aggregates === "boolean") {
        chkExclude.checked = params.exclude_aggregates;
    }

    // Step 3 영역으로 부드럽게 스크롤
    const step3 = document.getElementById("sectionStep3");
    if (step3) {
        step3.classList.remove("disabled-card");
        step3.scrollIntoView({ behavior: "smooth" });
    }
}

// -------------------------------------------------------------
// 6. Step 3: POST /run (Pandas 실행 및 Chart.js 렌더링)
// -------------------------------------------------------------
async function executeRun() {
    const selectTool = document.getElementById("selectTool");
    const inputRef = document.getElementById("inputRefPeriod");
    const inputTopN = document.getElementById("inputTopN");
    const chkExclude = document.getElementById("chkExcludeAggregates");

    const toolName = selectTool ? selectTool.value : "top_bottom_n";
    const refPeriod = inputRef ? inputRef.value.trim() : "";
    const n = inputTopN ? parseInt(inputTopN.value, 10) : 10;
    const excludeAgg = chkExclude ? chkExclude.checked : true;

    // 7~12번 도구 기준시점 필수 검사
    const def = TOOL_DEFINITIONS.find(t => t.name === toolName);
    if (def && def.requiresRef && !refPeriod) {
        alert(`'${def.title}' 도구는 기준 시점(연도 등)을 필수로 입력해야 합니다.`);
        if (inputRef) inputRef.focus();
        return;
    }

    const payload = {
        tool: toolName,
        params: {
            reference_period: refPeriod,
            n: n,
            exclude_aggregates: excludeAgg
        }
    };

    const btnRun = document.getElementById("btnRun");
    setLoading(btnRun, true, "Pandas 계산 중...");
    console.log(`[RUN] 도구 실행 시작 (도구명: ${toolName}, 기준시점: ${refPeriod || "전체"})`);

    try {
        const res = await fetch("/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (res.status === 401) {
            handleUnauthorized();
            throw new Error("접근 권한이 없습니다. 사이트 비밀번호를 입력해 주세요.");
        }

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "분석 계산 실행 중 오류가 발생했습니다.");
        }

        lastRunResult = data;
        console.log("[RUN] 결과 수신");

        renderRunResults(data);

        // Step 4 활성화
        const step4Card = document.getElementById("sectionStep4");
        if (step4Card) step4Card.classList.remove("disabled-card");

    } catch (err) {
        console.error("[ERROR] 실행 오류 발생:", err);
        alert("도구 실행 오류: " + err.message);
    } finally {
        setLoading(btnRun, false, "Pandas 분석 실행");
    }
}

function renderRunResults(data) {
    const container = document.getElementById("runResultContainer");
    if (!container) return;

    // 메타데이터 스트립 갱신
    document.getElementById("metaRefPeriod").textContent = data.reference_period || "전체";
    document.getElementById("metaUsedRows").textContent = `${data.used_rows_count}개`;
    document.getElementById("metaExcludedRows").textContent = `${data.excluded_rows_count}개`;
    document.getElementById("metaExcludedReason").textContent = data.excluded_reason || "없음";

    // Chart.js 시각화
    renderChart(data.chart_data);

    // 세부 수치 요약 테이블 갱신
    renderCalcSummaryTable(data);

    container.classList.remove("hidden");
}

function renderChart(chartData) {
    const canvas = document.getElementById("insightChart");
    if (!canvas) return;

    if (currentChart) {
        currentChart.destroy();
        currentChart = null;
    }

    if (!chartData || !chartData.datasets || chartData.datasets.length === 0) {
        canvas.style.display = "none";
        return;
    }

    canvas.style.display = "block";
    const ctx = canvas.getContext("2d");

    // Chart.js 옵션 및 색상 테마
    const chartType = chartData.type || "bar";

    const config = {
        type: chartType,
        data: {
            labels: chartData.labels || [],
            datasets: chartData.datasets.map((ds, idx) => ({
                label: ds.label || "값",
                data: ds.data || [],
                backgroundColor: chartData.indexAxis === "y" 
                    ? "rgba(37, 99, 235, 0.75)" 
                    : getChartColors(idx, chartData.datasets.length),
                borderColor: chartData.indexAxis === "y" 
                    ? "rgba(29, 78, 216, 1)" 
                    : getChartBorderColors(idx, chartData.datasets.length),
                borderWidth: 1.5,
                fill: chartType === "line" ? false : true,
                tension: 0.25
            }))
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: chartData.indexAxis || "x",
            plugins: {
                legend: {
                    display: chartData.datasets.length > 1 || chartType === "line",
                    position: "top"
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            if (chartType === "scatter") {
                                const pt = context.raw;
                                return `${pt.label || ""}: (${pt.x}, ${pt.y})`;
                            }
                            return `${context.dataset.label}: ${context.parsed.y !== undefined ? context.parsed.y : context.parsed.x}`;
                        }
                    }
                }
            },
            scales: {
                x: { grid: { color: "rgba(226, 232, 240, 0.6)" } },
                y: { grid: { color: "rgba(226, 232, 240, 0.6)" }, beginAtZero: false }
            }
        }
    };

    currentChart = new Chart(ctx, config);
    console.log(`[CHART] 차트 렌더 완료 (차트 종류: ${chartType})`);
}

function renderCalcSummaryTable(data) {
    const thead = document.getElementById("calcTableHead");
    const tbody = document.getElementById("calcTableBody");
    if (!thead || !tbody) return;

    thead.innerHTML = "";
    tbody.innerHTML = "";

    const resObj = data.result || {};

    // 1. top_bottom_n 결과 렌더링
    if (resObj.top) {
        thead.innerHTML = "<th>순위</th><th>항목(국가)</th><th>지표 수치</th><th>비고</th>";
        resObj.top.forEach(it => {
            const tr = document.createElement("tr");
            tr.innerHTML = `<td><strong>${it.rank}</strong></td><td>${it.label}</td><td>${it.value}</td><td>상위</td>`;
            tbody.appendChild(tr);
        });
        if (resObj.bottom) {
            resObj.bottom.forEach(it => {
                const tr = document.createElement("tr");
                tr.innerHTML = `<td><strong>하위</strong></td><td>${it.label}</td><td>${it.value}</td><td>하위</td>`;
                tbody.appendChild(tr);
            });
        }
        return;
    }

    // 2. describe_numeric 결과 렌더링
    if (resObj.statistics) {
        thead.innerHTML = "<th>통계 지표</th><th>계산값</th>";
        const statMap = {
            count: "표본 개수",
            mean: "평균값",
            std: "표준편차",
            median: "중앙값(50%)",
            min: "최솟값",
            q25: "1사분위수(25%)",
            q75: "3사분위수(75%)",
            max: "최댓값"
        };
        for (const [k, v] of Object.entries(resObj.statistics)) {
            const tr = document.createElement("tr");
            tr.innerHTML = `<td>${statMap[k] || k}</td><td><strong>${v}</strong></td>`;
            tbody.appendChild(tr);
        }
        return;
    }

    // 3. change_rate 결과 렌더링
    if (resObj.top_changers) {
        thead.innerHTML = "<th>순위</th><th>국가/항목</th><th>시작값</th><th>종료값</th><th>변화량(포인트)</th><th>변화율(%)</th>";
        resObj.top_changers.forEach(it => {
            const tr = document.createElement("tr");
            tr.innerHTML = `<td>${it.rank}</td><td>${it.label}</td><td>${it.start_val}</td><td>${it.end_val}</td><td>+${it.abs_change}</td><td>${it.pct_change ? it.pct_change + "%" : "-"}</td>`;
            tbody.appendChild(tr);
        });
        return;
    }

    // 4. distribution 결과 렌더링
    if (resObj.distribution_table) {
        thead.innerHTML = "<th>구간 범위</th><th>국가 수 (빈도)</th><th>비율 (%)</th>";
        resObj.distribution_table.forEach(it => {
            const tr = document.createElement("tr");
            tr.innerHTML = `<td>${it.bin_range}</td><td><strong>${it.count}개국</strong></td><td>${it.ratio_percent}%</td>`;
            tbody.appendChild(tr);
        });
        return;
    }

    // 기본 키-값 렌더링
    thead.innerHTML = "<th>항목</th><th>내용</th>";
    for (const [k, v] of Object.entries(resObj)) {
        if (typeof v === "object" && v !== null) continue;
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${k}</td><td>${v}</td>`;
        tbody.appendChild(tr);
    }
}

// -------------------------------------------------------------
// 7. Step 4: POST /explain (모드 A/B 해석)
// -------------------------------------------------------------
function initModeSelector() {
    const cardB = document.getElementById("modeCardB");
    const cardA = document.getElementById("modeCardA");
    const radios = document.querySelectorAll("input[name='explainMode']");

    radios.forEach(r => {
        r.addEventListener("change", (e) => {
            if (e.target.value === "B") {
                cardB.classList.add("active");
                cardA.classList.remove("active");
            } else {
                cardA.classList.add("active");
                cardB.classList.remove("active");
            }
        });
    });
}

async function executeExplain() {
    const selectedModeInput = document.querySelector("input[name='explainMode']:checked");
    const mode = selectedModeInput ? selectedModeInput.value : "B";

    const btnExplain = document.getElementById("btnExplain");
    setLoading(btnExplain, true, `AI 해석 생성 중 (모드 ${mode})...`);
    console.log(`[EXPLAIN] 해석 요청 시작 (모드: ${mode})`);

    const payload = {
        mode: mode,
        tool_result: lastRunResult
    };

    try {
        const res = await fetch("/explain", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (res.status === 401) {
            handleUnauthorized();
            throw new Error("접근 권한이 없습니다. 사이트 비밀번호를 입력해 주세요.");
        }

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "AI 해석 보고서를 생성하지 못했습니다.");
        }

        console.log("[EXPLAIN] 응답 수신");
        renderExplainReport(data);

    } catch (err) {
        console.error("[ERROR] 해석 오류 발생:", err);
        alert("AI 해석 오류: " + err.message);
    } finally {
        setLoading(btnExplain, false, "AI 해석 보고서 생성");
    }
}

function renderExplainReport(data) {
    const container = document.getElementById("explainResultContainer");
    const title = document.getElementById("reportTitle");
    const badge = document.getElementById("reportModeBadge");
    const content = document.getElementById("reportContent");

    if (!container || !content) return;

    if (title) title.textContent = data.mode_title || "📄 AI 분석 보고서";
    if (badge) {
        badge.textContent = data.mode === "B" ? "모드 B (정식)" : "모드 A (비교체험용)";
        badge.className = data.mode === "B" ? "badge badge-success" : "badge badge-info";
    }

    // 마크다운 문법을 HTML로 안전하게 변환
    content.innerHTML = parseMarkdownToHtml(data.report || "내용이 없습니다.");
    container.classList.remove("hidden");
    container.scrollIntoView({ behavior: "smooth" });
}

// -------------------------------------------------------------
// 8. 헬퍼 유틸리티 (단위 변환, 마크다운 파서, 색상 생성)
// -------------------------------------------------------------
function setLoading(button, isLoading, loadingText) {
    if (!button) return;
    button.disabled = isLoading;
    const txtSpan = button.querySelector(".btn-text");
    if (txtSpan) {
        txtSpan.textContent = loadingText;
    }
}

function formatBytes(bytes, decimals = 1) {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + " " + sizes[i];
}

function parseMarkdownToHtml(md) {
    if (!md) return "";
    let html = md
        // HTML 이스케이프
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        // 헤더
        .replace(/^### (.*$)/gim, "<h3>$1</h3>")
        .replace(/^## (.*$)/gim, "<h2>$1</h2>")
        .replace(/^# (.*$)/gim, "<h1>$1</h1>")
        // 강조
        .replace(/\*\*(.*?)\*\*/gim, "<strong>$1</strong>")
        .replace(/\*(.*?)\*/gim, "<em>$1</em>")
        // 리스트
        .replace(/^\s*\-\s(.*$)/gim, "<li>$1</li>")
        // 줄바꿈
        .replace(/\n\n+/g, "<br><br>");

    // <li> 들을 <ul>로 래핑
    html = html.replace(/(<li>.*?<\/li>)/gim, "<ul>$1</ul>");
    return html;
}

function getChartColors(index, total) {
    const palette = [
        "rgba(37, 99, 235, 0.7)",   // 파랑
        "rgba(13, 148, 136, 0.7)",  // 청록
        "rgba(217, 119, 6, 0.7)",   // 주황
        "rgba(147, 51, 234, 0.7)",  // 보라
        "rgba(225, 29, 72, 0.7)"    // 빨강
    ];
    return palette[index % palette.length];
}

function getChartBorderColors(index, total) {
    const palette = [
        "rgba(29, 78, 216, 1)",
        "rgba(15, 118, 110, 1)",
        "rgba(180, 83, 9, 1)",
        "rgba(126, 34, 206, 1)",
        "rgba(190, 18, 60, 1)"
    ];
    return palette[index % palette.length];
}

// -------------------------------------------------------------
// 8. 후속 질문 Q&A 대화창 이벤트 (연속 대화 & 인라인 차트)
// -------------------------------------------------------------
let qaChatHistory = [];

function initQaEvents() {
    const inputQuestion = document.getElementById("inputQaQuestion");
    const btnSendQa = document.getElementById("btnSendQa");
    const qaChatList = document.getElementById("qaChatList");
    const btnClearQa = document.getElementById("btnClearQa");
    const quickTags = document.querySelectorAll(".qa-tag");

    if (!inputQuestion || !btnSendQa || !qaChatList) return;

    function appendMessage(role, htmlContent) {
        const msgDiv = document.createElement("div");
        msgDiv.className = `qa-msg qa-msg-${role}`;
        
        const bubble = document.createElement("div");
        bubble.className = "qa-bubble";
        bubble.innerHTML = htmlContent;
        
        msgDiv.appendChild(bubble);
        qaChatList.appendChild(msgDiv);
        qaChatList.scrollTop = qaChatList.scrollHeight;
        return bubble;
    }

    function renderQaInlineChart(canvasId, chart) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) return;

        const ctx = canvas.getContext("2d");
        const chartType = chart.type || "line";

        new Chart(ctx, {
            type: chartType,
            data: {
                labels: chart.labels || [],
                datasets: (chart.datasets || []).map((ds, idx) => ({
                    label: ds.label || "지표",
                    data: ds.data || [],
                    borderColor: chartType === "line" ? "rgba(37, 99, 235, 1)" : undefined,
                    backgroundColor: chartType === "line" ? "rgba(37, 99, 235, 0.15)" : "rgba(37, 99, 235, 0.75)",
                    borderWidth: 2,
                    tension: 0.25,
                    fill: chartType === "line",
                    pointRadius: 4,
                    pointHoverRadius: 6
                }))
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { grid: { color: "rgba(226, 232, 240, 0.6)" } },
                    y: { 
                        grid: { color: "rgba(226, 232, 240, 0.6)" },
                        // 순위 그래프인 경우 1위가 위에 오도록 역순(reverse) 고려 가능하나 기본 오름차순 유지
                        beginAtZero: chartType === "bar"
                    }
                },
                plugins: {
                    legend: { position: "top" }
                }
            }
        });

        console.log(`[CHART] 대화형 인라인 차트 렌더 완료 (차트 종류: ${chartType})`);
    }

    async function submitQuestion() {
        const question = inputQuestion.value.trim();
        if (!question) {
            alert("질문 내용을 입력해 주세요.");
            inputQuestion.focus();
            return;
        }

        // 사용자 말풍선 추가
        appendMessage("user", question.replace(/</g, "&lt;").replace(/>/g, "&gt;"));
        inputQuestion.value = "";

        // 로딩 말풍선 추가
        const loadingBubble = appendMessage("ai", '<span class="qa-loading">AI가 데이터를 분석하여 답변을 정리하고 있습니다... ⏳</span>');
        const loadingMsgElem = loadingBubble.parentElement;

        setLoading(btnSendQa, true, "답변 중...");
        console.log(`[ASK] 대화 전송 (히스토리: ${qaChatHistory.length}턴): "${question}"`);

        try {
            const res = await fetch("/ask", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    question: question,
                    history: qaChatHistory
                })
            });

            if (res.status === 401) {
                if (loadingMsgElem && loadingMsgElem.parentElement) {
                    loadingMsgElem.remove();
                }
                handleUnauthorized();
                throw new Error("접근 권한이 없습니다. 사이트 비밀번호를 입력해 주세요.");
            }

            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.error || "답변 생성 중 오류가 발생했습니다.");
            }

            // 로딩 말풍선 제거
            if (loadingMsgElem && loadingMsgElem.parentElement) {
                loadingMsgElem.remove();
            }

            // AI 답변 말풍선 추가
            const aiBubble = appendMessage("ai", parseMarkdownToHtml(data.answer));

            // 대화 히스토리 누적
            qaChatHistory.push({ role: "user", content: question });
            qaChatHistory.push({ role: "model", content: data.answer });

            console.log(`[ASK] 답변 수신 완료 (차트 포함 여부: ${Boolean(data.chart)})`);

            // 그래프가 포함된 경우 인라인 차트 생성
            if (data.chart && data.chart.labels && data.chart.datasets) {
                const chartId = "inlineChart_" + Date.now();
                const chartWrap = document.createElement("div");
                chartWrap.className = "qa-inline-chart-wrap";
                chartWrap.innerHTML = `
                    <div class="qa-chart-header">
                        <strong>📊 ${data.chart.title || '분석 시각화 그래프'}</strong>
                    </div>
                    <div class="qa-chart-canvas-box">
                        <canvas id="${chartId}"></canvas>
                    </div>
                `;
                aiBubble.appendChild(chartWrap);
                qaChatList.scrollTop = qaChatList.scrollHeight;

                // Chart.js 렌더링
                renderQaInlineChart(chartId, data.chart);
            }

            qaChatList.scrollTop = qaChatList.scrollHeight;

        } catch (err) {
            console.error("[ERROR] Q&A 요청 실패:", err);
            if (loadingMsgElem && loadingMsgElem.parentElement) {
                loadingMsgElem.remove();
            }
            appendMessage("ai", `<span class="text-danger">오류 발생: ${err.message}</span>`);
        } finally {
            setLoading(btnSendQa, false, "전송");
        }
    }

    btnSendQa.addEventListener("click", submitQuestion);

    inputQuestion.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submitQuestion();
        }
    });

    if (btnClearQa) {
        btnClearQa.addEventListener("click", () => {
            qaChatHistory = [];
            qaChatList.innerHTML = `
                <div class="qa-msg qa-msg-ai">
                    <div class="qa-bubble">
                        대화 기록이 초기화되었습니다. 새로운 질문을 입력해 주세요! 😊
                    </div>
                </div>
            `;
            console.log("[ASK] 대화 기록 초기화 완료");
        });
    }

    quickTags.forEach(tag => {
        tag.addEventListener("click", () => {
            const q = tag.getAttribute("data-q");
            if (q) {
                inputQuestion.value = q;
                submitQuestion();
            }
        });
    });
}

