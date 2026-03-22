import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Suspicion Scoring - Whalebot Dashboard",
  description: "How the whalebot suspicion scoring system works",
};

const FACTORS = [
  {
    name: "Account Age",
    max: 25,
    description:
      "The single strongest signal across every documented insider trading case. Brand-new accounts created days or hours before a major bet are the hallmark of insider activity.",
    tiers: [
      { condition: "0-1 days", points: 25 },
      { condition: "1-3 days", points: 20 },
      { condition: "3-7 days", points: 15 },
      { condition: "7-14 days", points: 5 },
    ],
    cases:
      "Venezuela/Maduro: account <1 week old. Iran strikes: wallets funded within 24h. OpenAI: 13 zero-history wallets opened within 40h of the event.",
  },
  {
    name: "Low Probability Bet",
    max: 25,
    description:
      "Insiders bet on outcomes the market prices at very low odds, because they know the outcome. Buying at 5% implied probability when you know it will resolve to 100% is the classic pattern.",
    tiers: [
      { condition: "Implied probability \u226410%", points: 25 },
      { condition: "Implied probability \u226420%", points: 20 },
      { condition: "Implied probability \u226430%", points: 10 },
    ],
    cases:
      "Venezuela/Maduro: bet at 5.5% odds. Google/AlphaRaccoon: bet on 0.2% outcome. ZachXBT/Axiom: entry at $0.14/share.",
  },
  {
    name: "Trade Size",
    max: 15,
    description:
      "Insiders go big. When you know the outcome, you maximize your position. Documented cases range from $20K to $300K+ on single outcomes.",
    tiers: [
      { condition: "\u2265$100K", points: 15 },
      { condition: "\u2265$50K", points: 12 },
      { condition: "\u2265$25K", points: 10 },
      { condition: "\u2265$10K", points: 7 },
      { condition: "\u2265$5K", points: 3 },
    ],
    cases:
      "Venezuela: $30K bet \u2192 $436K profit. Iran: six wallets totaling $1.2M. Google: $3M deposited for immediate trading.",
  },
  {
    name: "Market Concentration",
    max: 15,
    description:
      "Insiders only trade in the domain where they have privileged access. They don't diversify \u2014 they bet exclusively on 1-4 outcomes in a single area.",
    tiers: [
      { condition: "1 market only", points: 15 },
      { condition: "2-3 markets", points: 12 },
      { condition: "4-5 markets", points: 7 },
    ],
    cases:
      "Venezuela: only 4 Venezuela-related outcomes ever. OpenAI employee: only product launch dates. Israeli IDF: only military strike timing.",
  },
  {
    name: "Size vs History Ratio",
    max: 10,
    description:
      "A wallet's first-ever trade being a $50K+ bomb is very different from an established trader with $500K in historical volume making a similar-sized bet. This measures how outsized the current trade is relative to the wallet's history.",
    tiers: [
      { condition: "First trade ever (no history)", points: 10 },
      { condition: "Trade \u22655x total prior volume", points: 10 },
      { condition: "Trade \u22652x total prior volume", points: 7 },
      { condition: "Trade \u22651x total prior volume", points: 3 },
    ],
    cases:
      "In nearly every case, the suspicious wallet had zero or minimal prior trading history before dropping a massive position.",
  },
  {
    name: "Cluster Activity",
    max: 10,
    description:
      "Multiple new wallets piling into the same outcome within a short window is a coordinated insider signal. This checks how many other new wallets bet on the same outcome in the last 24 hours.",
    tiers: [
      { condition: "5+ other new wallets", points: 10 },
      { condition: "3-4 other new wallets", points: 7 },
      { condition: "1-2 other new wallets", points: 4 },
    ],
    cases:
      "Iran strikes: 6 coordinated wallets. OpenAI launches: 13 wallets. ZachXBT/Axiom: 12 wallets, 8 of top 10 earners were insiders.",
  },
];

const TIERS = [
  {
    name: "HIGH",
    range: "60-100",
    color: "text-red-400 bg-red-500/10 border-red-500/20",
    description:
      "Strong insider signal. Multiple high-confidence factors firing together. These are the trades most worth tailing.",
  },
  {
    name: "MEDIUM",
    range: "30-59",
    color: "text-yellow-400 bg-yellow-500/10 border-yellow-500/20",
    description:
      "Worth watching. Some suspicious signals but could also be a well-informed public trader making a conviction bet.",
  },
  {
    name: "LOW",
    range: "0-29",
    color: "text-zinc-400 bg-zinc-500/10 border-zinc-500/20",
    description:
      "Likely a normal whale trade. New account making a bet but without the combination of signals that indicate insider knowledge.",
  },
];

const CASES = [
  {
    name: "Venezuela / Maduro Capture",
    date: "Jan 2026",
    profit: "$436K",
    signals: "Account <1 week, 5.5% odds, only Venezuela bets, $30K size",
    score: "~90",
  },
  {
    name: "U.S./Israel Strikes on Iran",
    date: "Feb 2026",
    profit: "$1.2M (6 wallets)",
    signals: "All funded <24h prior, same date bet, cluster of 6",
    score: "~85",
  },
  {
    name: "Israeli Military Intel Leak",
    date: "Jun 2025",
    profit: "$150K",
    signals: "Exact strike dates, concentrated military bets",
    score: "~80",
  },
  {
    name: "ZachXBT / Axiom",
    date: "Feb 2026",
    profit: "$1.2M (12 wallets)",
    signals: "New wallets, $0.14 entry, 12-wallet cluster, 3h before reveal",
    score: "~95",
  },
  {
    name: "Google Year in Search",
    date: "Dec 2025",
    profit: "$1M",
    signals: "22/23 win rate, 0.2% probability bets, $3M fresh deposit",
    score: "~75",
  },
  {
    name: "OpenAI Product Launches",
    date: "Oct 2025 - Feb 2026",
    profit: "$309K+ (60 wallets)",
    signals:
      "13 zero-history wallets within 40h, specific launch date bets, employee fired",
    score: "~90",
  },
];

export default function ScoringPage() {
  return (
    <div className="space-y-10">
      <div>
        <Link
          href="/"
          className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
        >
          &larr; Back to dashboard
        </Link>
        <h1 className="text-xl font-semibold mt-3">
          Suspicion Scoring System
        </h1>
        <p className="text-sm text-zinc-400 mt-2 max-w-2xl">
          Each whale trade is scored 0-100 based on how closely it matches
          patterns from documented insider trading cases on Polymarket. The
          score is computed from six weighted factors derived from real
          incidents.
        </p>
      </div>

      {/* Tiers */}
      <div>
        <h2 className="text-sm font-medium text-zinc-300 mb-3">Score Tiers</h2>
        <div className="grid gap-3 md:grid-cols-3">
          {TIERS.map((tier) => (
            <div
              key={tier.name}
              className={`rounded-lg border p-4 ${tier.color}`}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="font-mono font-semibold">{tier.name}</span>
                <span className="text-xs font-mono opacity-70">
                  {tier.range}
                </span>
              </div>
              <p className="text-xs opacity-80">{tier.description}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Factors */}
      <div>
        <h2 className="text-sm font-medium text-zinc-300 mb-3">
          Scoring Factors
        </h2>
        <div className="space-y-4">
          {FACTORS.map((factor) => (
            <div
              key={factor.name}
              className="rounded-lg border border-zinc-800 bg-zinc-900 p-4"
            >
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-medium">{factor.name}</h3>
                <span className="text-xs font-mono text-zinc-500">
                  max {factor.max} pts
                </span>
              </div>
              <p className="text-xs text-zinc-400 mb-3">
                {factor.description}
              </p>
              <div className="flex flex-wrap gap-2 mb-3">
                {factor.tiers.map((t) => (
                  <span
                    key={t.condition}
                    className="text-xs font-mono bg-zinc-800 text-zinc-300 px-2 py-0.5 rounded"
                  >
                    {t.condition} → +{t.points}
                  </span>
                ))}
              </div>
              <p className="text-[11px] text-zinc-500 italic">
                {factor.cases}
              </p>
            </div>
          ))}
        </div>
      </div>

      {/* Real Cases */}
      <div>
        <h2 className="text-sm font-medium text-zinc-300 mb-3">
          Real-World Cases
        </h2>
        <p className="text-xs text-zinc-500 mb-3">
          These documented incidents informed the scoring weights. Estimated
          scores show what the system would have assigned.
        </p>
        <div className="rounded-lg border border-zinc-800 bg-zinc-900 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-zinc-500 text-xs">
                <th className="px-4 py-2 text-left">Case</th>
                <th className="px-4 py-2 text-left">Date</th>
                <th className="px-4 py-2 text-right">Profit</th>
                <th className="px-4 py-2 text-left">Key Signals</th>
                <th className="px-4 py-2 text-center">Est. Score</th>
              </tr>
            </thead>
            <tbody>
              {CASES.map((c) => (
                <tr
                  key={c.name}
                  className="border-t border-zinc-800/50"
                >
                  <td className="px-4 py-2 font-medium whitespace-nowrap">
                    {c.name}
                  </td>
                  <td className="px-4 py-2 text-zinc-400 font-mono text-xs whitespace-nowrap">
                    {c.date}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-green-400 whitespace-nowrap">
                    {c.profit}
                  </td>
                  <td className="px-4 py-2 text-xs text-zinc-400">
                    {c.signals}
                  </td>
                  <td className="px-4 py-2 text-center">
                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-mono border bg-red-500/20 text-red-400 border-red-500/30">
                      {c.score}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Formula */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
        <h2 className="text-sm font-medium text-zinc-300 mb-3">
          How It Works
        </h2>
        <div className="text-xs text-zinc-400 space-y-2">
          <p>
            When the bot detects a large trade from a new account, it
            calculates a suspicion score by summing points across all six
            factors. The score is capped at 100.
          </p>
          <p>
            <strong className="text-zinc-300">Data sources:</strong> Account
            age, trade count, and market concentration come from the
            Polymarket Data API (same call used for the newness check — no
            extra API calls). Entry price comes from the CLOB API. Cluster
            count is computed from recent signals in our database.
          </p>
          <p>
            <strong className="text-zinc-300">False positive reduction:</strong>{" "}
            Established wallets with long histories, diversified bets, and
            gradual position building will naturally score low. The scoring
            system is additive — a trade needs multiple signals firing
            together to reach HIGH tier.
          </p>
          <p>
            <strong className="text-zinc-300">Validation:</strong> The "Win
            Rate by Suspicion Score" table on the dashboard shows whether
            higher-scored signals actually win more often. Over time this data
            will validate or refine the scoring weights.
          </p>
        </div>
      </div>
    </div>
  );
}
