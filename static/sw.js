/**
 * AI Data Insight Builder - Service Worker
 * PWA 오프라인 셸 캐싱 및 네트워크 프록시
 * 보안 원칙: 모든 AI/데이터 분석 API 요청은 절대 캐시하지 않고 항상 네트워크를 경유합니다.
 */

const CACHE_NAME = "data-insight-builder-v1";
const STATIC_ASSETS = [
    "/",
    "/static/style.css",
    "/static/app.js",
    "/static/manifest.json",
    "/static/icons/icon.svg",
    "/static/icons/icon-192.png",
    "/static/icons/icon-512.png"
];

// 1. 서비스 워커 설치: 정적 자산 사전 캐싱
self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(STATIC_ASSETS).catch((err) => {
                console.warn("[SW] 사전 캐싱 일부 실패 (네트워크 상태 확인):", err);
            });
        }).then(() => self.skipWaiting())
    );
});

// 2. 서비스 워커 활성화: 이전 버전 캐시 정리
self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.map((key) => {
                    if (key !== CACHE_NAME) {
                        console.log("[SW] 구버전 캐시 삭제:", key);
                        return caches.delete(key);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

// 3. 네트워크 요청 처리: API는 항상 네트워크 직접 요청, 정적 자산은 캐시 활용
self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);

    // API 및 보안 엔드포인트는 캐시를 타지 않고 항상 네트워크로 직접 전송
    const isApiRequest = 
        url.pathname.startsWith("/api/") ||
        url.pathname === "/inspect" ||
        url.pathname === "/suggest" ||
        url.pathname === "/run" ||
        url.pathname === "/explain" ||
        url.pathname === "/ask" ||
        url.pathname === "/health";

    if (isApiRequest || event.request.method !== "GET") {
        event.respondWith(fetch(event.request));
        return;
    }

    // 정적 자원 및 HTML: 네트워크 우선, 실패 시 캐시 제공
    event.respondWith(
        fetch(event.request)
            .then((networkResponse) => {
                if (networkResponse && networkResponse.status === 200) {
                    const responseClone = networkResponse.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(event.request, responseClone);
                    });
                }
                return networkResponse;
            })
            .catch(() => {
                return caches.match(event.request);
            })
    );
});
