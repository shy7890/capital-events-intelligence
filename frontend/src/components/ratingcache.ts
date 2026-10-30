// 评级 / 已看过 的轻量共享缓存：跨页面显示「已评级」标签与看过状态过滤，
// 评级写入后立即刷新。数据源：GET /api/ratings。
import { useEffect, useState } from "react";

export interface RatingEntry {
  code: string;
  rating: string; // high/medium/low
  reviewed_at: string | null;
  updated_at: string | null;
  note: string | null;
  name: string | null;
  board: string | null;
}

let cache: Map<string, RatingEntry> | null = null;
const listeners = new Set<() => void>();

function setCache(v: Map<string, RatingEntry>) {
  cache = v;
  listeners.forEach((l) => l());
}

export function refreshRatingCache() {
  cache = null;
  loadRatings();
}

export function loadRatings() {
  fetch("/api/ratings?limit=500")
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((d: { rows?: RatingEntry[] }) => {
      const next = new Map((d.rows ?? []).map((x) => [x.code, x]));
      if (!cache || cache.size !== next.size || [...cache.keys()].some((c) => !next.has(c))) {
        setCache(next);
      }
    })
    .catch(() => undefined);
}

export function useRatings(): {
  ratings: Map<string, RatingEntry>;
  get: (code: string) => RatingEntry | null;
  ready: boolean;
  refresh: () => void;
} {
  const [ratings, setRatings] = useState<Map<string, RatingEntry>>(cache ?? new Map());
  const [ready, setReady] = useState(cache !== null);

  useEffect(() => {
    loadRatings();
    const l = () => {
      setRatings(new Map(cache ?? []));
      setReady(true);
    };
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);

  return {
    ratings,
    get: (code: string) => ratings.get(code) ?? null,
    ready,
    refresh: () => loadRatings(),
  };
}
