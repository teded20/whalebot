import { getDb } from "@/lib/db";
import type {
  Signal,
  ThresholdBucket,
  AgeBucket,
  ScoreBucket,
} from "@/lib/types";
import { Suspense } from "react";
import Filters from "./filters";

export const dynamic = "force-dynamic";

const THRESHOLDS = [500, 1000, 2500, 5000, 10000, 25000, 50000, 100000];
const AGE_BUCKETS = [
  { label: "0-1d", min: 0, max: 1 },
  { label: "1-3d", min: 1, max: 3 },
  { label: "3-7d", min: 3, max: 7 },
  { label: "7d+", min: 7, max: 9999 },
];
const SCORE_BUCKETS = [
  { label: "HIGH (60-100)", min: 60, max: 101 },
  { label: "MEDIUM (30-59)", min: 30, max: 60 },
  { label: "LOW (0-29)", min: 0, max: 30 },
];

interface FilterParams {
  score?: string;
  size?: string;
  age?: string;
  status?: string;
  side?: string;
}

function buildWhereClause(filters: FilterParams): {
  clause: string;
  description: string;
} {
  const conditions: string[] = [];
  const parts: string[] = [];

  if (filters.score) {
    const tier = filters.score.toUpperCase();
    if (tier === "HIGH") {
      conditions.push("s.suspicion_score >= 60");
      parts.push("HIGH score");
    } else if (tier === "MEDIUM") {
      conditions.push("s.suspicion_score >= 30 AND s.suspicion_score < 60");
      parts.push("MEDIUM score");
    } else if (tier === "LOW") {
      conditions.push("s.suspicion_score < 30");
      parts.push("LOW score");
    }
  }

  if (filters.size) {
    const min = parseInt(filters.size, 10);
    if (min > 0) {
      conditions.push(`s.trade_size_usdc >= ${min}`);
      parts.push(`≥$${(min / 1000).toFixed(0)}K`);
    }
  }

  if (filters.age) {
    const [minAge, maxAge] = filters.age.split("-").map(Number);
    if (!isNaN(minAge) && !isNaN(maxAge)) {
      conditions.push(
        `s.account_age_days >= ${minAge} AND s.account_age_days < ${maxAge}`,
      );
      parts.push(`${minAge}-${maxAge}d age`);
    }
  }

  if (filters.status) {
    if (filters.status === "win") {
      conditions.push("s.resolved AND s.won");
      parts.push("wins");
    } else if (filters.status === "loss") {
      conditions.push("s.resolved AND NOT s.won");
      parts.push("losses");
    } else if (filters.status === "pending") {
      conditions.push("NOT s.resolved");
      parts.push("pending");
    }
  }

  if (filters.side) {
    const side = filters.side.toUpperCase();
    if (side === "BUY" || side === "SELL") {
      conditions.push(`s.side = '${side}'`);
      parts.push(side);
    }
  }

  return {
    clause: conditions.length > 0 ? "WHERE " + conditions.join(" AND ") : "",
    description: parts.length > 0 ? parts.join(", ") : "all signals",
  };
}

async function getStats(filters: FilterParams) {
  const sql = getDb();
  const { clause } = buildWhereClause(filters);

  // Totals (filtered)
  const totals = await sql`SELECT COUNT(*)::int as total,
      COUNT(*) FILTER (WHERE s.resolved AND s.won)::int as wins,
      COUNT(*) FILTER (WHERE s.resolved AND NOT s.won)::int as losses,
      COUNT(*) FILTER (WHERE NOT s.resolved)::int as pending
    FROM signals s ${sql.unsafe(clause)}` as Record<string, number>[];

  // By threshold (unfiltered — always show full breakdown)
  const byThreshold: ThresholdBucket[] = [];
  for (const threshold of THRESHOLDS) {
    const rows = await sql`
      SELECT
        COUNT(*)::int as signals,
        COUNT(*) FILTER (WHERE resolved AND won)::int as wins,
        COUNT(*) FILTER (WHERE resolved AND NOT won)::int as losses,
        COUNT(*) FILTER (WHERE NOT resolved)::int as pending
      FROM signals
      WHERE trade_size_usdc >= ${threshold}
    `;
    const r = rows[0];
    const resolved = r.wins + r.losses;
    byThreshold.push({
      threshold,
      signals: r.signals,
      wins: r.wins,
      losses: r.losses,
      pending: r.pending,
      win_rate: resolved > 0 ? r.wins / resolved : null,
    });
  }

  // By age (unfiltered)
  const byAge: AgeBucket[] = [];
  for (const { label, min, max } of AGE_BUCKETS) {
    const rows = await sql`
      SELECT
        COUNT(*)::int as signals,
        COUNT(*) FILTER (WHERE resolved AND won)::int as wins,
        COUNT(*) FILTER (WHERE resolved AND NOT won)::int as losses,
        COUNT(*) FILTER (WHERE NOT resolved)::int as pending
      FROM signals
      WHERE account_age_days >= ${min} AND account_age_days < ${max}
    `;
    const r = rows[0];
    const resolved = r.wins + r.losses;
    byAge.push({
      bucket: label,
      signals: r.signals,
      wins: r.wins,
      losses: r.losses,
      pending: r.pending,
      win_rate: resolved > 0 ? r.wins / resolved : null,
    });
  }

  // By score (unfiltered)
  const byScore: ScoreBucket[] = [];
  for (const { label, min, max } of SCORE_BUCKETS) {
    const rows = await sql`
      SELECT
        COUNT(*)::int as signals,
        COUNT(*) FILTER (WHERE resolved AND won)::int as wins,
        COUNT(*) FILTER (WHERE resolved AND NOT won)::int as losses,
        COUNT(*) FILTER (WHERE NOT resolved)::int as pending
      FROM signals
      WHERE suspicion_score >= ${min} AND suspicion_score < ${max}
    `;
    const r = rows[0];
    const resolved = r.wins + r.losses;
    byScore.push({
      bucket: label,
      min,
      max,
      signals: r.signals,
      wins: r.wins,
      losses: r.losses,
      pending: r.pending,
      win_rate: resolved > 0 ? r.wins / resolved : null,
    });
  }

  // Recent signals (filtered)
  const recent = await sql`
    SELECT s.id, s.created_at, s.wallet, s.trade_size_usdc, s.side,
           s.market_title, s.outcome, s.account_age_days, s.total_trades,
           s.entry_price, s.resolved, s.won, s.winning_outcome, s.market_slug,
           s.suspicion_score, s.score_tier, s.score_breakdown, s.unique_markets,
           s.hours_to_resolution,
           wr.total_signals as wallet_signal_count,
           wr.suspicion_streak as wallet_win_streak,
           CASE WHEN wr.total_resolved > 0
                THEN wr.total_wins::float / wr.total_resolved
                ELSE NULL END as wallet_win_rate
    FROM signals s
    LEFT JOIN wallet_reputation wr ON LOWER(s.wallet) = wr.wallet
    ${sql.unsafe(clause)}
    ORDER BY s.created_at DESC
    LIMIT 50
  ` as unknown as Signal[];

  const waves = await sql`
    SELECT w.*
    FROM wave_events w
    ORDER BY w.detected_at DESC
    LIMIT 10
  ` as unknown as any[];

  return {
    total_signals: totals[0].total,
    total_wins: totals[0].wins,
    total_losses: totals[0].losses,
    total_pending: totals[0].pending,
    total_resolved: totals[0].wins + totals[0].losses,
    by_threshold: byThreshold,
    by_age: byAge,
    by_score: byScore,
    recent_signals: recent,
    waves,
  };
}

function formatUsd(n: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(n);
}

function WinRate({ rate }: { rate: number | null }) {
  if (rate === null) return <span className="text-zinc-500">N/A</span>;
  const pct = (rate * 100).toFixed(0);
  const color =
    rate >= 0.6
      ? "text-green-400"
      : rate >= 0.5
        ? "text-yellow-400"
        : "text-red-400";
  return <span className={color}>{pct}%</span>;
}

function ScoreBadge({ score, tier }: { score: number; tier: string }) {
  const color =
    tier === "HIGH"
      ? "bg-red-500/20 text-red-400 border-red-500/30"
      : tier === "MEDIUM"
        ? "bg-yellow-500/20 text-yellow-400 border-yellow-500/30"
        : "bg-zinc-500/20 text-zinc-400 border-zinc-500/30";
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-mono border ${color}`}
    >
      {score}
    </span>
  );
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.min(score, 100);
  const color =
    pct >= 60
      ? "bg-red-500"
      : pct >= 30
        ? "bg-yellow-500"
        : "bg-zinc-500";
  return (
    <div className="w-16 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
      <div
        className={`h-full rounded-full ${color}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

const FACTOR_LABELS: Record<string, string> = {
  age: "Account Age",
  low_prob: "Low Probability",
  size: "Trade Size",
  concentration: "Concentrated",
  size_ratio: "Size vs History",
  cluster: "Cluster Activity",
};

function ScoreTooltip({ breakdown }: { breakdown: string }) {
  try {
    const factors = JSON.parse(breakdown) as Record<string, number>;
    const entries = Object.entries(factors).filter(([, v]) => v > 0);
    if (entries.length === 0) return null;
    return (
      <div className="flex flex-wrap gap-1 mt-1">
        {entries.map(([key, pts]) => (
          <span
            key={key}
            className="text-[10px] font-mono bg-zinc-800 text-zinc-400 px-1 rounded"
          >
            {FACTOR_LABELS[key] || key} +{pts}
          </span>
        ))}
      </div>
    );
  } catch {
    return null;
  }
}

export default async function Dashboard({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const filters: FilterParams = {
    score: typeof params.score === "string" ? params.score : undefined,
    size: typeof params.size === "string" ? params.size : undefined,
    age: typeof params.age === "string" ? params.age : undefined,
    status: typeof params.status === "string" ? params.status : undefined,
    side: typeof params.side === "string" ? params.side : undefined,
  };

  const hasFilters = Object.values(filters).some(Boolean);
  const { description } = buildWhereClause(filters);
  const stats = await getStats(filters);

  return (
    <div className="space-y-6">
      {/* Filters */}
      <Suspense>
        <Filters />
      </Suspense>

      {/* Active filter indicator */}
      {hasFilters && (
        <div className="text-xs text-zinc-500">
          Showing <span className="text-zinc-300">{description}</span>{" "}
          &middot; {stats.total_signals} signals
        </div>
      )}

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        {[
          { label: "Total Signals", value: stats.total_signals },
          { label: "Resolved", value: stats.total_resolved },
          {
            label: "Wins",
            value: stats.total_wins,
            color: "text-green-400",
          },
          {
            label: "Losses",
            value: stats.total_losses,
            color: "text-red-400",
          },
          {
            label: "Win Rate",
            value:
              stats.total_resolved > 0
                ? `${((stats.total_wins / stats.total_resolved) * 100).toFixed(0)}%`
                : "N/A",
            color:
              stats.total_resolved > 0 &&
              stats.total_wins / stats.total_resolved >= 0.5
                ? "text-green-400"
                : "text-zinc-400",
          },
        ].map((card) => (
          <div
            key={card.label}
            className="rounded-lg border border-zinc-800 bg-zinc-900 p-4"
          >
            <div className="text-xs text-zinc-500 mb-1">{card.label}</div>
            <div
              className={`text-2xl font-mono font-semibold ${card.color || "text-zinc-100"}`}
            >
              {card.value}
            </div>
          </div>
        ))}
      </div>

      {/* Analytics Tables */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* By Threshold */}
        <div className="rounded-lg border border-zinc-800 bg-zinc-900">
          <div className="px-4 py-3 border-b border-zinc-800">
            <h2 className="text-sm font-medium text-zinc-300">
              By Trade Size
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-zinc-500 text-xs">
                  <th className="px-3 py-2 text-left">Min</th>
                  <th className="px-3 py-2 text-right">Sig</th>
                  <th className="px-3 py-2 text-right">W</th>
                  <th className="px-3 py-2 text-right">L</th>
                  <th className="px-3 py-2 text-right">Win%</th>
                </tr>
              </thead>
              <tbody>
                {stats.by_threshold.map((b) => (
                  <tr
                    key={b.threshold}
                    className="border-t border-zinc-800/50"
                  >
                    <td className="px-3 py-1.5 font-mono text-xs">
                      {formatUsd(b.threshold)}+
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {b.signals}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs text-green-400">
                      {b.wins}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs text-red-400">
                      {b.losses}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      <WinRate rate={b.win_rate} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* By Account Age */}
        <div className="rounded-lg border border-zinc-800 bg-zinc-900">
          <div className="px-4 py-3 border-b border-zinc-800">
            <h2 className="text-sm font-medium text-zinc-300">
              By Account Age
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-zinc-500 text-xs">
                  <th className="px-3 py-2 text-left">Age</th>
                  <th className="px-3 py-2 text-right">Sig</th>
                  <th className="px-3 py-2 text-right">W</th>
                  <th className="px-3 py-2 text-right">L</th>
                  <th className="px-3 py-2 text-right">Win%</th>
                </tr>
              </thead>
              <tbody>
                {stats.by_age.map((b) => (
                  <tr
                    key={b.bucket}
                    className="border-t border-zinc-800/50"
                  >
                    <td className="px-3 py-1.5 font-mono text-xs">
                      {b.bucket}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      {b.signals}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs text-green-400">
                      {b.wins}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs text-red-400">
                      {b.losses}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs">
                      <WinRate rate={b.win_rate} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* By Suspicion Score */}
        <div className="rounded-lg border border-zinc-800 bg-zinc-900">
          <div className="px-4 py-3 border-b border-zinc-800 flex items-center justify-between">
            <h2 className="text-sm font-medium text-zinc-300">
              By Suspicion Score
            </h2>
            <a
              href="/scoring"
              className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
            >
              How it works &rarr;
            </a>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-zinc-500 text-xs">
                  <th className="px-3 py-2 text-left">Tier</th>
                  <th className="px-3 py-2 text-right">Sig</th>
                  <th className="px-3 py-2 text-right">W</th>
                  <th className="px-3 py-2 text-right">L</th>
                  <th className="px-3 py-2 text-right">Win%</th>
                </tr>
              </thead>
              <tbody>
                {stats.by_score.map((b) => {
                  const tierColor =
                    b.min >= 60
                      ? "text-red-400"
                      : b.min >= 30
                        ? "text-yellow-400"
                        : "text-zinc-400";
                  return (
                    <tr
                      key={b.bucket}
                      className="border-t border-zinc-800/50"
                    >
                      <td
                        className={`px-3 py-1.5 font-mono text-xs ${tierColor}`}
                      >
                        {b.bucket}
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono text-xs">
                        {b.signals}
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono text-xs text-green-400">
                        {b.wins}
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono text-xs text-red-400">
                        {b.losses}
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono text-xs">
                        <WinRate rate={b.win_rate} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Wave Activity */}
      {stats.waves && stats.waves.length > 0 && (
        <div className="rounded-lg border border-orange-800/50 bg-orange-950/20">
          <div className="px-4 py-3 border-b border-orange-800/50">
            <h2 className="text-sm font-medium text-orange-300">
              🌊 Recent Wave Activity
            </h2>
          </div>
          <div className="divide-y divide-orange-800/30">
            {stats.waves.map((w: any) => (
              <div key={w.id} className="px-4 py-2 text-sm flex items-center gap-2 flex-wrap">
                <span className="text-orange-300 font-mono">
                  {w.wallet_count} wallets
                </span>
                <span className="text-zinc-600">·</span>
                <span className="font-mono text-zinc-300">
                  ${Number(w.total_volume_usdc).toLocaleString()}
                </span>
                <span className="text-zinc-600">·</span>
                <span className="text-zinc-400 truncate max-w-xs">
                  {w.outcome}
                </span>
                {w.shared_funding_source && (
                  <span className="text-xs px-1.5 py-0.5 rounded bg-red-900/50 text-red-300">
                    shared funding
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent Signals */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900">
        <div className="px-4 py-3 border-b border-zinc-800">
          <h2 className="text-sm font-medium text-zinc-300">
            {hasFilters ? "Filtered Signals" : "Recent Signals"}
            <span className="text-zinc-500 font-normal ml-2">
              ({stats.recent_signals.length}
              {stats.recent_signals.length === 50 ? "+" : ""})
            </span>
          </h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-zinc-500 text-xs">
                <th className="px-4 py-2 text-left">Time</th>
                <th className="px-4 py-2 text-left">Market</th>
                <th className="px-4 py-2 text-left">Side</th>
                <th className="px-4 py-2 text-right">Size</th>
                <th className="px-4 py-2 text-right">Price / Prob</th>
                <th className="px-4 py-2 text-right">Age</th>
                <th className="px-4 py-2 text-center">Score</th>
                <th className="px-4 py-2 text-left">Status</th>
              </tr>
            </thead>
            <tbody>
              {stats.recent_signals.length === 0 ? (
                <tr>
                  <td
                    colSpan={8}
                    className="px-4 py-8 text-center text-zinc-500"
                  >
                    {hasFilters
                      ? "No signals match these filters."
                      : "No signals yet. Start the bot to begin collecting data."}
                  </td>
                </tr>
              ) : (
                stats.recent_signals.map((s) => {
                  const status = s.resolved
                    ? s.won
                      ? "WIN"
                      : "LOSS"
                    : "PENDING";
                  const statusColor = s.resolved
                    ? s.won
                      ? "text-green-400"
                      : "text-red-400"
                    : "text-zinc-500";
                  const sideColor =
                    s.side === "BUY" ? "text-green-400" : "text-red-400";

                  return (
                    <tr
                      key={s.id}
                      className="border-t border-zinc-800/50 hover:bg-zinc-800/30 transition-colors"
                    >
                      <td className="px-4 py-2 font-mono text-zinc-400 text-xs whitespace-nowrap">
                        {new Date(s.created_at).toLocaleDateString("en-US", {
                          month: "short",
                          day: "numeric",
                        })}
                        {s.hours_to_resolution != null && s.hours_to_resolution <= 72 && (
                          <span className={`ml-1 text-xs px-1 py-0.5 rounded ${
                            s.hours_to_resolution <= 6
                              ? "bg-red-900/50 text-red-300"
                              : s.hours_to_resolution <= 24
                                ? "bg-orange-900/50 text-orange-300"
                                : "bg-yellow-900/50 text-yellow-300"
                          }`}>
                            {s.hours_to_resolution <= 1
                              ? "<1h"
                              : s.hours_to_resolution <= 24
                                ? `${Math.round(s.hours_to_resolution)}h`
                                : `${Math.round(s.hours_to_resolution / 24)}d`}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2 max-w-xs truncate">
                        {s.market_slug ? (
                          <a
                            href={`https://polymarket.com/event/${s.market_slug}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="hover:text-blue-400 transition-colors"
                          >
                            {s.market_title}
                          </a>
                        ) : (
                          s.market_title
                        )}
                        <span className="text-zinc-500 ml-1">
                          ({s.outcome})
                        </span>
                      </td>
                      <td className={`px-4 py-2 font-mono ${sideColor}`}>
                        {s.side}
                      </td>
                      <td className="px-4 py-2 text-right font-mono">
                        {formatUsd(s.trade_size_usdc)}
                      </td>
                      <td className="px-4 py-2 text-right font-mono text-zinc-400">
                        {s.entry_price != null
                          ? <>
                              ${s.entry_price.toFixed(2)}{" "}
                              <span className="text-zinc-500 text-xs">
                                ({(s.entry_price * 100).toFixed(0)}%)
                              </span>
                            </>
                          : "\u2014"}
                      </td>
                      <td className="px-4 py-2 text-right font-mono text-zinc-400">
                        {s.account_age_days}d
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex flex-col items-center gap-1">
                          <ScoreBadge
                            score={s.suspicion_score}
                            tier={s.score_tier}
                          />
                          <ScoreBar score={s.suspicion_score} />
                          <ScoreTooltip breakdown={s.score_breakdown} />
                          {s.wallet_signal_count != null && s.wallet_signal_count > 1 && (
                            <span className="text-xs px-1 py-0.5 rounded bg-purple-900/50 text-purple-300">
                              {s.wallet_signal_count}x
                              {s.wallet_win_streak != null && s.wallet_win_streak >= 2
                                ? ` \uD83D\uDD25${s.wallet_win_streak}`
                                : ""}
                            </span>
                          )}
                        </div>
                      </td>
                      <td
                        className={`px-4 py-2 font-mono font-semibold ${statusColor}`}
                      >
                        {status}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
