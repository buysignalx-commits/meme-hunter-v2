# Meme Hunter V2 — early memecoin scanner

Goal: discover very new Solana/Base memecoins, reject obvious risk, then require repeated confirmation before an `ENTRY READY` alert.

## Signal pipeline

NEW POOL
→ market gate
→ GoPlus security gate
→ WATCH
→ next scan(s)
→ momentum/liquidity still healthy
→ ENTRY READY

### Hard reject examples

- honeypot / cannot sell
- mint authority / dangerous mintability
- balance manipulation
- transfer pause / freeze
- non-transferable token
- malicious creator
- creator/owner concentration above threshold
- top-10 concentration above threshold
- excessive unlocked LP
- dumping / weak buyers / weak liquidity
- already vertically extended

### Two-stage signal

`WATCH` is discovery.  
`ENTRY READY` requires a higher score **and consecutive clean scans**.

This avoids the common mistake of buying a token only because its first 1–2 minutes look strong.

## Install

Python 3.10+.

```bash
python -m venv .venv
```

Activate it, then:

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add Telegram credentials.

Run:

```bash
python bot.py
```

## Telegram setup

1. Open `@BotFather`
2. `/newbot`
3. copy token → `TELEGRAM_BOT_TOKEN`
4. send a message to your new bot
5. visit `https://api.telegram.org/bot<TOKEN>/getUpdates`
6. copy `chat.id` → `TELEGRAM_CHAT_ID`

## GoPlus

Add `GOPLUS_ACCESS_TOKEN` for more reliable security API access.

## Important limitations

This scanner does **not** predict 20x–100x. It identifies early candidates under a rule set.

"Sniper/bundle" detection is not claimed unless actual wallet-level transaction data proves it. V2 instead uses holder concentration, creator/owner holdings, LP state, sell restrictions, permissions, liquidity and repeated order-flow confirmation.

For real sniper/bundle/creator-history analytics, the next layer should use a dedicated Solana/EVM transaction/indexing provider (for example Helius on Solana) and trace the creator plus first-block wallets.

## Recommended validation

Run in paper mode for 7–14 days before trading:
- alert price
- +25%, +50%, 2x, 5x
- max drawdown
- time-to-peak
- rug/failure rate
- performance by score band

Then change thresholds from evidence rather than intuition.
