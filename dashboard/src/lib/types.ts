export interface Signal {
  id: number;
  created_at: string;
  wallet: string;
  trade_size_usdc: number;
  side: "BUY" | "SELL";
  ctf_token_id: string;
  market_title: string;
  outcome: string;
  exchange: string;
  tx_hash: string;
  account_age_days: number;
  total_trades: number;
  total_volume_usdc: number;
  entry_price: number | null;
  pseudonym: string | null;
  condition_id: string;
  market_slug: string;
  resolved: boolean;
  won: boolean | null;
  winning_outcome: string | null;
  resolved_at: string | null;
}

export interface ThresholdBucket {
  threshold: number;
  signals: number;
  wins: number;
  losses: number;
  pending: number;
  win_rate: number | null;
}

export interface AgeBucket {
  bucket: string;
  signals: number;
  wins: number;
  losses: number;
  pending: number;
  win_rate: number | null;
}

export interface Stats {
  total_signals: number;
  total_resolved: number;
  total_wins: number;
  total_losses: number;
  total_pending: number;
  by_threshold: ThresholdBucket[];
  by_age: AgeBucket[];
}
