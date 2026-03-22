import { getDb } from "@/lib/db";
import type { Signal, ThresholdBucket, AgeBucket } from "@/lib/types";

export const dynamic = "force-dynamic";

const THRESHOLDS = [500, 1000, 2500, 5000, 10000, 25000, 50000, 100000];
const AGE_BUCKETS = [
  { label: "0-1d", min: 0, max: 1 },
  { label: "1-3d", min: 1, max: 3 },
  { label: "3-7d", min: 3, max: 7 },
  { label: "7d+", min: 7, max: 9999 },
];

async function getStats() {
  const sql = getDb();

  const totals = await sql`
    SELECT
      COUNT(*)::int as total,
      COUNT(*) FILTER (WHERE resolved AND won)::int as wins,
      COUNT(*) FILTER (WHERE resolved AND NOT won)::int as losses,
      COUNT(*) FILTER (WHERE NOT resolved)::int as pending
    FROM signals
  `;

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

  const recent = await sql`
    SELECT id, created_at, wallet, trade_size_usdc, side,
           market_title, outcome, account_age_days, total_trades,
           entry_price, resolved, won, winning_outcome, market_slug
    FROM signals
    ORDER BY created_at DESC
    LIMIT 30
  `;

  return {
    total_signals: totals[0].total,
    total_wins: totals[0].wins,
    total_losses: totals[0].losses,
    total_pending: totals[0].pending,
    total_resolved: totals[0].wins + totals[0].losses,
    by_threshold: byThreshold,
    by_age: byAge,
    recent_signals: recent as unknown as Signal[],
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

export default async function Dashboard() {
  const stats = await getStats();

  return (
    <div className="space-y-8">
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

      {/* By Threshold */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900">
        <div className="px-4 py-3 border-b border-zinc-800">
          <h2 className="text-sm font-medium text-zinc-300">
            Win Rate by Trade Size
          </h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-zinc-500 text-xs">
                <th className="px-4 py-2 text-left">Threshold</th>
                <th className="px-4 py-2 text-right">Signals</th>
                <th className="px-4 py-2 text-right">Wins</th>
                <th className="px-4 py-2 text-right">Losses</th>
                <th className="px-4 py-2 text-right">Pending</th>
                <th className="px-4 py-2 text-right">Win Rate</th>
              </tr>
            </thead>
            <tbody>
              {stats.by_threshold.map((b) => (
                <tr
                  key={b.threshold}
                  className="border-t border-zinc-800/50"
                >
                  <td className="px-4 py-2 font-mono">
                    {formatUsd(b.threshold)}+
                  </td>
                  <td className="px-4 py-2 text-right font-mono">
                    {b.signals}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-green-400">
                    {b.wins}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-red-400">
                    {b.losses}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-zinc-500">
                    {b.pending}
                  </td>
                  <td className="px-4 py-2 text-right font-mono">
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
            Win Rate by Account Age
          </h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-zinc-500 text-xs">
                <th className="px-4 py-2 text-left">Age</th>
                <th className="px-4 py-2 text-right">Signals</th>
                <th className="px-4 py-2 text-right">Wins</th>
                <th className="px-4 py-2 text-right">Losses</th>
                <th className="px-4 py-2 text-right">Pending</th>
                <th className="px-4 py-2 text-right">Win Rate</th>
              </tr>
            </thead>
            <tbody>
              {stats.by_age.map((b) => (
                <tr
                  key={b.bucket}
                  className="border-t border-zinc-800/50"
                >
                  <td className="px-4 py-2 font-mono">{b.bucket}</td>
                  <td className="px-4 py-2 text-right font-mono">
                    {b.signals}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-green-400">
                    {b.wins}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-red-400">
                    {b.losses}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-zinc-500">
                    {b.pending}
                  </td>
                  <td className="px-4 py-2 text-right font-mono">
                    <WinRate rate={b.win_rate} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent Signals */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900">
        <div className="px-4 py-3 border-b border-zinc-800">
          <h2 className="text-sm font-medium text-zinc-300">
            Recent Signals
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
                <th className="px-4 py-2 text-right">Entry</th>
                <th className="px-4 py-2 text-right">Age</th>
                <th className="px-4 py-2 text-left">Status</th>
              </tr>
            </thead>
            <tbody>
              {stats.recent_signals.length === 0 ? (
                <tr>
                  <td
                    colSpan={7}
                    className="px-4 py-8 text-center text-zinc-500"
                  >
                    No signals yet. Start the bot to begin collecting data.
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
                      className="border-t border-zinc-800/50"
                    >
                      <td className="px-4 py-2 font-mono text-zinc-400 text-xs whitespace-nowrap">
                        {new Date(s.created_at).toLocaleDateString(
                          "en-US",
                          { month: "short", day: "numeric" },
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
                        {s.entry_price
                          ? `$${s.entry_price.toFixed(2)}`
                          : "\u2014"}
                      </td>
                      <td className="px-4 py-2 text-right font-mono text-zinc-400">
                        {s.account_age_days}d
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
