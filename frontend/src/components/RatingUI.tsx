import { message, Tooltip } from "antd";
import { StarFilled, StarOutlined } from "@ant-design/icons";
import { refreshRatingCache } from "./ratingcache";

export const RATING_META: Record<string, { label: string; color: string; desc: string }> = {
  high: { label: "高", color: "#cf1322", desc: "重点关注：自动放入我的跟踪" },
  medium: { label: "中", color: "#fa8c16", desc: "观察对象：自动放入待观察" },
  low: { label: "低", color: "#8c99a6", desc: "不看好：自动放入待观察" },
};

export interface RatingControlsProps {
  code: string;
  current?: string | null;
  onChanged?: () => void;
}

const RATING_KEYS = ["high", "medium", "low"] as const;

async function setRating(code: string, rating: string | null): Promise<void> {
  const op =
    rating == null
      ? fetch(`/api/rating/${encodeURIComponent(code)}`, { method: "DELETE" })
      : fetch(`/api/rating/${encodeURIComponent(code)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rating }),
        });
  try {
    const r = await op;
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    refreshRatingCache();
    refreshWatchCodesSafe();
    message.success(
      rating == null
        ? "已清除评级（重新变为未看过）"
        : `已评级：${RATING_META[rating]?.label ?? rating}`,
    );
  } catch (e: unknown) {
    message.error(e instanceof Error ? e.message : String(e));
    throw e;
  }
}

// watchlist 缓存刷新（高评级自动加入跟踪后同步"已跟踪"状态）
function refreshWatchCodesSafe() {
  // 动态 import 避免循环依赖
  void import("./watchcache").then((m) => m.refreshWatchCache());
}

export function RatingTag({ rating }: { rating?: string | null }) {
  const meta = RATING_META[rating ?? ""];
  if (!meta || !rating) return null;
  const icon = rating === "high" ? <StarFilled /> : <StarOutlined />;
  return (
    <span className={`rating-tag rating-tag--${rating}`}>
      <span className="rating-tag__icon" aria-hidden="true">
        {icon}
      </span>
      {meta.label}
    </span>
  );
}

export function RatingMutator({ code, current, onChanged }: RatingControlsProps) {
  return (
    <div className="rating-control" role="group" aria-label="人工评级">
      {RATING_KEYS.map((k) => {
        const m = RATING_META[k];
        const active = current === k;
        return (
          <Tooltip key={k} title={active ? `${m.label}：点击清除评级` : m.desc}>
            <button
              type="button"
              className={`rating-option rating-option--${k}${active ? " is-active" : ""}`}
              aria-label={`${m.label}评级${active ? "（当前，点击清除）" : ""}`}
              aria-pressed={active}
              onClick={(event) => {
                event.stopPropagation();
                void setRating(code, active ? null : k)
                  .then(() => onChanged?.())
                  .catch(() => undefined);
              }}
            >
              <span className="rating-option__dot" aria-hidden="true" />
              {m.label}
            </button>
          </Tooltip>
        );
      })}
    </div>
  );
}
