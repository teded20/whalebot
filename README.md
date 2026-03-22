# Polymarket New Whale Detector Bot 🐋

A Telegram bot that monitors Polymarket's on-chain activity on Polygon and alerts you when **brand-new accounts** make large trades — a pattern historically associated with insider trading.

## Architecture

```
Polygon RPC (WebSocket)
    │
    ▼
┌─────────────────────────┐
│  Event Listener          │  Subscribes to OrderFilled events on both
│  (CTF + NegRisk Exchange)│  exchange contracts via WebSocket
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  Wallet Analyzer         │  For each maker/taker address:
│                          │  1. Check local cache (seen wallets DB)
│                          │  2. Query Polymarket Data API for trade history
│                          │  3. Determine if "new" (< N prior trades)
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  Alert Engine            │  If new wallet + trade > threshold:
│                          │  - Enrich with market name from Gamma API
│                          │  - Format alert with trade details
│                          │  - Send Telegram notification
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│  Telegram Bot            │  Commands: /start, /status, /threshold,
│                          │  /watchlist, /pause, /resume
└─────────────────────────┘
```

## Key Contracts Monitored

| Contract | Address | Purpose |
|---|---|---|
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` | Binary YES/NO markets |
| NegRisk CTF Exchange | `0xC5d563A36AE78145C45a50134d48A1215220f80a` | Multi-outcome markets |

## Setup

### Prerequisites
- Python 3.11+
- A Polygon RPC WebSocket URL (Alchemy, Infura, or QuickNode recommended)
- A Telegram bot token (from @BotFather)
- Your Telegram chat ID

### 1. Clone & Install

```bash
git clone <your-repo>
cd polymarket-whale-bot
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your values
```

### 3. Run

```bash
python -m bot.main
```

### 4. Deploy (VPS)

```bash
sudo cp polymarket-bot.service /etc/systemd/system/
sudo systemctl enable polymarket-bot
sudo systemctl start polymarket-bot
```

## Configuration

| Env Variable | Description | Default |
|---|---|---|
| `POLYGON_WS_URL` | Polygon WebSocket RPC URL | (required) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather | (required) |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID | (required) |
| `TRADE_THRESHOLD_USDC` | Min USDC trade size to flag | `1000` |
| `MAX_PRIOR_TRADES` | Max prior trades to consider "new" | `5` |
| `LOOKBACK_DAYS` | Days to look back for prior activity | `30` |

## Telegram Commands

- `/start` — Initialize bot and start receiving alerts
- `/status` — Show bot status, uptime, and stats
- `/threshold <amount>` — Set minimum USDC threshold
- `/maxtrades <count>` — Set max prior trades for "new" classification
- `/watchlist` — Show wallets currently being tracked
- `/pause` / `/resume` — Pause/resume alerts
- `/stats` — Show detection statistics
