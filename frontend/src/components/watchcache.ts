import { useCallback, useEffect, useState } from "react";

/**
 * 我的跟踪名单的轻量共享缓存：跨页面显示「已跟踪」状态，加入了即时生效。
 * 数据源：GET /api/watchlist（只取 active），module 级缓存避免重复网络请求。
 */

let cache: Set<string> | null = null;
const listeners = new Set<() => void>();

function setCache(v: Set<string>) {
  cache = v;
  listeners.forEach((l) => l());
}

export function refreshWatchCache() {
  cache = null;
  loadWatchCodes();
}

export function loadWatchCodes() {
  fetch("/api/watchlist?limit=500")
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((d: { rows?: { code: string; status: string }[] }) => {
      const next = new Set((d.rows ?? []).filter((x) => x.status === "active").map((x) => x.code));
      if (!cache || cache.size !== next.size || [...cache].some((c) => !next.has(c))) setCache(next);
    })
    .catch(() => undefined);
}

export function useWatchCodes(): { codes: Set<string>; ready: boolean; refresh: () => void } {
  const [codes, setCodes] = useState<Set<string>>(cache ?? new Set());
  const [ready, setReady] = useState(cache !== null);

  useEffect(() => {
    loadWatchCodes();
    const l = () => {
      setCodes(new Set(cache ?? []));
      setReady(true);
    };
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);

  return {
    codes,
    ready,
    refresh: useCallback(() => loadWatchCodes(), []),
  };
}
