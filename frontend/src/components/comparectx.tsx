import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

/**
 * 多票对比的全局选择状态：任意列表（今日/机会/跟踪）都能加入对比，
 * Compare 页读取并渲染。上限 8 只，重复添加去重。状态存 localStorage 防刷新丢失。
 */

interface CompareCtx {
  codes: string[];
  add: (code: string) => boolean; // false=满了或重复
  remove: (code: string) => void;
  clear: () => void;
  has: (code: string) => boolean;
}

const Ctx = createContext<CompareCtx | null>(null);

const LIMIT = 8;
const LS_KEY = "zhuang.compare.codes";

function loadCodes(): string[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x) => typeof x === "string").slice(0, LIMIT) : [];
  } catch {
    return [];
  }
}

function saveCodes(codes: string[]) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(codes));
  } catch {
    /* 本地存储不可用时忽略 */
  }
}

export function CompareProvider({ children }: { children: ReactNode }) {
  const [codes, setCodes] = useState<string[]>(loadCodes);
  const add = useCallback((code: string) => {
    let ok = false;
    setCodes((prev) => {
      if (prev.includes(code)) return prev;
      if (prev.length >= LIMIT) return prev;
      ok = true;
      const next = [...prev, code];
      saveCodes(next);
      return next;
    });
    return ok;
  }, []);
  const remove = useCallback((code: string) => {
    setCodes((prev) => {
      const next = prev.filter((c) => c !== code);
      saveCodes(next);
      return next;
    });
  }, []);
  const clear = useCallback(() => {
    setCodes([]);
    saveCodes([]);
  }, []);
  const has = useCallback((code: string) => codes.includes(code), [codes]);
  return <Ctx.Provider value={{ codes, add, remove, clear, has }}>{children}</Ctx.Provider>;
}

export function useCompare(): CompareCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useCompare must be used within CompareProvider");
  return v;
}
