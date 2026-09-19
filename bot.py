import os, time, sqlite3, smtplib, requests
from email.message import EmailMessage
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

# ---------------- CONFIG ----------------
NETWORKS = [x.strip() for x in os.getenv("NETWORKS", "solana,base").split(",") if x.strip()]
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "60"))
MIN_WATCH_SCORE = int(os.getenv("MIN_WATCH_SCORE", "68"))
MIN_ENTRY_SCORE = int(os.getenv("MIN_ENTRY_SCORE", "82"))
CONFIRM_SCANS = int(os.getenv("CONFIRM_SCANS", "2"))

MAX_AGE_MIN = int(os.getenv("MAX_AGE_MIN", "120"))
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "15000"))
MAX_FDV_USD = float(os.getenv("MAX_FDV_USD", "2500000"))
MIN_FDV_USD = float(os.getenv("MIN_FDV_USD", "30000"))
MIN_VOLUME_5M = float(os.getenv("MIN_VOLUME_5M", "5000"))
MIN_BUYS_5M = int(os.getenv("MIN_BUYS_5M", "20"))
MIN_BUY_SELL_RATIO = float(os.getenv("MIN_BUY_SELL_RATIO", "1.25"))
MAX_PRICE_CHANGE_5M = float(os.getenv("MAX_PRICE_CHANGE_5M", "65"))
MAX_TOP10_PCT = float(os.getenv("MAX_TOP10_PCT", "35"))
MAX_CREATOR_PCT = float(os.getenv("MAX_CREATOR_PCT", "5"))
MAX_UNLOCKED_LP_PCT = float(os.getenv("MAX_UNLOCKED_LP_PCT", "25"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GOPLUS_ACCESS_TOKEN = os.getenv("GOPLUS_ACCESS_TOKEN", "")

EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
EMAIL_TO = os.getenv("EMAIL_TO", "")

DB_PATH = os.getenv("DB_PATH", "meme_hunter_v2.db")

GT = "https://api.geckoterminal.com/api/v2"
DS = "https://api.dexscreener.com"
GP = "https://api.gopluslabs.io/api/v1"

DS_CHAIN = {"solana": "solana", "base": "base", "eth": "ethereum", "bsc": "bsc"}
GP_CHAIN = {"base": "8453", "eth": "1", "bsc": "56"}

http = requests.Session()
http.headers.update({
    "User-Agent": "MemeHunterV2/2.0",
    "Accept": "application/json"
})

def f(v, d=0.0):
    try: return float(v) if v not in (None, "") else d
    except: return d

def i(v, d=0):
    try: return int(v)
    except: return d

def api_get(url, params=None, headers=None, timeout=15):
    r = http.get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()

# ---------------- DATABASE ----------------
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS candidates(
        network TEXT NOT NULL,
        token TEXT NOT NULL,
        pool TEXT,
        symbol TEXT,
        first_seen INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        scans INTEGER NOT NULL DEFAULT 1,
        confirms INTEGER NOT NULL DEFAULT 0,
        first_price REAL DEFAULT 0,
        last_price REAL DEFAULT 0,
        max_score INTEGER DEFAULT 0,
        watch_sent INTEGER DEFAULT 0,
        entry_sent INTEGER DEFAULT 0,
        PRIMARY KEY(network, token)
    )""")
    con.commit(); con.close()

def get_state(network, token):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM candidates WHERE network=? AND token=?", (network, token)).fetchone()
    con.close()
    return dict(row) if row else None

def upsert_state(network, token, pool, symbol, price, score, passed):
    now = int(time.time())
    old = get_state(network, token)
    con = sqlite3.connect(DB_PATH)
    if not old:
        con.execute("""INSERT INTO candidates(network,token,pool,symbol,first_seen,last_seen,scans,confirms,
                     first_price,last_price,max_score) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (network, token, pool, symbol, now, now, 1, 1 if passed else 0, price, price, score))
    else:
        confirms = old["confirms"] + 1 if passed else 0
        con.execute("""UPDATE candidates SET pool=?,symbol=?,last_seen=?,scans=scans+1,confirms=?,
                     last_price=?,max_score=? WHERE network=? AND token=?""",
                    (pool, symbol, now, confirms, price, max(old["max_score"], score), network, token))
    con.commit(); con.close()
    return get_state(network, token)

def set_sent(network, token, field):
    assert field in ("watch_sent", "entry_sent")
    con = sqlite3.connect(DB_PATH)
    con.execute(f"UPDATE candidates SET {field}=1 WHERE network=? AND token=?", (network, token))
    con.commit(); con.close()

# ---------------- DISCOVERY ----------------
def new_pools(network):
    data = api_get(f"{GT}/networks/{network}/new_pools",
                   params={"include": "base_token,quote_token,dex"})
    inc = {}
    for x in data.get("included", []):
        inc[(x.get("type"), x.get("id"))] = x

    out = []
    for p in data.get("data", []):
        a = p.get("attributes", {})
        rel = p.get("relationships", {})
        br = (rel.get("base_token", {}).get("data") or {})
        bi = inc.get((br.get("type"), br.get("id")), {})
        token = (bi.get("attributes") or {}).get("address")
        if token:
            out.append({
                "token": token,
                "pool": a.get("address"),
                "created": a.get("pool_created_at"),
                "name": a.get("name", "")
            })
    return out[:20]

def dex_pair(network, token):
    chain = DS_CHAIN.get(network, network)
    data = api_get(f"{DS}/token-pairs/v1/{chain}/{token}")
    if not isinstance(data, list) or not data: return None
    return max(data, key=lambda p: f((p.get("liquidity") or {}).get("usd")))

def age_minutes(pair, iso_created):
    ms = pair.get("pairCreatedAt")
    if ms:
        return max(0, (time.time()*1000 - int(ms))/60000)
    try:
        return max(0, (time.time()-datetime.fromisoformat(iso_created.replace("Z","+00:00")).timestamp())/60)
    except:
        return 999999

# ---------------- SECURITY ----------------
def goplus(network, token):
    headers = {}
    if GOPLUS_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {GOPLUS_ACCESS_TOKEN}"
    try:
        if network == "solana":
            d = api_get(f"{GP}/solana/token_security",
                        params={"contract_addresses": token}, headers=headers)
        elif network in GP_CHAIN:
            d = api_get(f"{GP}/token_security/{GP_CHAIN[network]}",
                        params={"contract_addresses": token}, headers=headers)
        else:
            return {}
        res = d.get("result") or {}
        return res.get(token) or res.get(token.lower()) or (next(iter(res.values())) if res else {})
    except Exception as e:
        print("GoPlus:", e)
        return {}

def flag_status(v):
    if isinstance(v, dict):
        return str(v.get("status", v.get("value", "0"))) in ("1","2")
    return str(v) == "1"

def pct(v):
    x = f(v)
    return x*100 if 0 <= x <= 1 else x

def analyze_security(network, s):
    hard = []
    soft = []
    penalty = 0

    if not s:
        return ["SECURITY_DATA_UNAVAILABLE"], ["manual-check-required"], 40, {}

    # Normalized critical controls for EVM + Solana response variants.
    dangerous_fields = {
        "is_honeypot": "HONEYPOT",
        "cannot_sell_all": "CANNOT_SELL_ALL",
        "is_blacklisted": "BLACKLIST",
        "owner_change_balance": "OWNER_CHANGE_BALANCE",
        "balance_mutable_authority": "BALANCE_MUTABLE",
        "transfer_pausable": "TRANSFER_PAUSABLE",
        "freezable": "FREEZABLE",
        "closable": "CLOSABLE",
        "non_transferable": "NON_TRANSFERABLE",
    }
    for k, label in dangerous_fields.items():
        if flag_status(s.get(k)):
            hard.append(label)

    if flag_status(s.get("is_mintable")) or flag_status(s.get("mintable")):
        hard.append("MINTABLE")
    if flag_status(s.get("is_proxy")):
        soft.append("PROXY"); penalty += 12
    if str(s.get("is_open_source", "1")) == "0":
        soft.append("NOT_OPEN_SOURCE"); penalty += 20

    # B20-style risk container (GoPlus newer field).
    b20 = s.get("b20_token") or {}
    if str(b20.get("is_b20", "0")) == "1":
        info = b20.get("b20_info") or {}
        for key in ("mintable","transfer_pausable","owner_change_balance","blacklist","cannot_sell"):
            if flag_status(info.get(key)):
                hard.append("B20_" + key.upper())

    creator_pct = pct(s.get("creator_percent"))
    owner_pct = pct(s.get("owner_percent"))
    if creator_pct > MAX_CREATOR_PCT:
        hard.append(f"CREATOR_{creator_pct:.1f}%")
    elif creator_pct > 2:
        soft.append(f"creator:{creator_pct:.1f}%"); penalty += 8

    if owner_pct > MAX_CREATOR_PCT:
        hard.append(f"OWNER_{owner_pct:.1f}%")

    holders = s.get("holders") or []
    top10 = 0.0
    whale_max = 0.0
    for h in holders[:10]:
        tag = str(h.get("tag","")).lower()
        hp = pct(h.get("percent"))
        if any(x in tag for x in ("burn","dead","lp","liquidity","locker")):
            continue
        top10 += hp
        whale_max = max(whale_max, hp)
    if top10 > MAX_TOP10_PCT:
        hard.append(f"TOP10_{top10:.1f}%")
    elif top10 > 25:
        soft.append(f"top10:{top10:.1f}%"); penalty += 8
    if whale_max > 15:
        hard.append(f"WHALE_{whale_max:.1f}%")

    # LP lock heuristic
    lp_holders = s.get("lp_holders") or []
    unlocked_lp = 0.0
    locked_lp = 0.0
    for h in lp_holders[:10]:
        hp = pct(h.get("percent"))
        tag = str(h.get("tag","")).lower()
        locked = str(h.get("is_locked","0")) == "1" or any(x in tag for x in ("burn","dead","lock"))
        if locked: locked_lp += hp
        else: unlocked_lp += hp
    if lp_holders and unlocked_lp > MAX_UNLOCKED_LP_PCT:
        hard.append(f"UNLOCKED_LP_{unlocked_lp:.1f}%")
    elif lp_holders and unlocked_lp > 10:
        soft.append(f"unlocked-lp:{unlocked_lp:.1f}%"); penalty += 8

    creator = s.get("creator_address") or s.get("creator")
    if isinstance(creator, dict):
        if str(creator.get("malicious_address","0")) == "1":
            hard.append("MALICIOUS_CREATOR")
        creator = creator.get("address")

    buy_tax = f(s.get("buy_tax"))
    sell_tax = f(s.get("sell_tax"))
    if buy_tax > .05: soft.append(f"buy-tax:{buy_tax:.1%}"); penalty += 15
    if sell_tax > .05: hard.append(f"SELL_TAX_{sell_tax:.1%}")

    metrics = {
        "creator": creator or "",
        "creator_pct": round(creator_pct,2),
        "owner_pct": round(owner_pct,2),
        "top10_pct": round(top10,2),
        "max_holder_pct": round(whale_max,2),
        "locked_lp_pct": round(locked_lp,2),
        "unlocked_lp_pct": round(unlocked_lp,2),
    }
    # Remove duplicates preserving order
    hard = list(dict.fromkeys(hard))
    soft = list(dict.fromkeys(soft))
    return hard, soft, penalty, metrics

# ---------------- MARKET SCORE ----------------
def analyze_market(pair, created):
    liq = f((pair.get("liquidity") or {}).get("usd"))
    fdv = f(pair.get("fdv") or pair.get("marketCap"))
    vol5 = f((pair.get("volume") or {}).get("m5"))
    vol1h = f((pair.get("volume") or {}).get("h1"))
    tx = (pair.get("txns") or {}).get("m5") or {}
    buys, sells = i(tx.get("buys")), i(tx.get("sells"))
    ratio = buys/max(sells,1)
    ch5 = f((pair.get("priceChange") or {}).get("m5"))
    ch1 = f((pair.get("priceChange") or {}).get("h1"))
    age = age_minutes(pair, created)
    price = f(pair.get("priceUsd"))

    reject = []
    if age > MAX_AGE_MIN: reject.append("TOO_OLD")
    if liq < MIN_LIQUIDITY_USD: reject.append("LOW_LIQUIDITY")
    if fdv and fdv > MAX_FDV_USD: reject.append("FDV_TOO_HIGH")
    if vol5 < MIN_VOLUME_5M: reject.append("WEAK_VOLUME")
    if buys < MIN_BUYS_5M: reject.append("TOO_FEW_BUYS")
    if ratio < MIN_BUY_SELL_RATIO: reject.append("WEAK_BUY_SELL")
    if ch5 < -22: reject.append("DUMPING")
    if ch5 > MAX_PRICE_CHANGE_5M: reject.append("CHASE_TOO_LATE")

    score = 0
    score += 18 if age <= 10 else 14 if age <= 30 else 9 if age <= 60 else 4
    score += 18 if 20000 <= liq <= 150000 else 12 if liq >= 15000 else 0
    score += 18 if vol5 >= 25000 else 14 if vol5 >= 10000 else 8
    score += 18 if ratio >= 2.5 else 14 if ratio >= 1.7 else 8 if ratio >= 1.3 else 3
    score += 12 if 5 <= ch5 <= 30 else 7 if 0 < ch5 < 5 else 4 if 30 < ch5 <= 65 else 0
    if fdv:
        score += 10 if 50000 <= fdv <= 500000 else 7 if fdv <= 1200000 else 3

    return score, reject, {
        "age": round(age,1), "liq": liq, "fdv": fdv, "vol5": vol5, "vol1h": vol1h,
        "buys": buys, "sells": sells, "ratio": round(ratio,2), "ch5": round(ch5,2),
        "ch1": round(ch1,2), "price": price
    }

def confirmation_ok(state, market):
    if not state: return False, []
    reasons = []
    first = f(state.get("first_price"))
    price = market["price"]
    if first > 0:
        since = (price/first - 1)*100
    else:
        since = 0
    # We want continuation but not a vertical late chase.
    if market["ratio"] < MIN_BUY_SELL_RATIO:
        reasons.append("buy pressure faded")
    if market["ch5"] < -8:
        reasons.append("5m momentum reversed")
    if since > 90:
        reasons.append("price already too extended since discovery")
    if since < -20:
        reasons.append("price broke down since discovery")
    if market["liq"] < MIN_LIQUIDITY_USD:
        reasons.append("liquidity fell")
    return len(reasons) == 0, reasons

# ---------------- ALERTS ----------------
def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    r = http.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                  json={"chat_id":TELEGRAM_CHAT_ID,"text":text,"disable_web_page_preview":True},
                  timeout=15)
    r.raise_for_status()

def send_email(subject, body):
    if not EMAIL_ENABLED or not EMAIL_TO: return
    m = EmailMessage(); m["Subject"]=subject; m["From"]=EMAIL_FROM; m["To"]=EMAIL_TO; m.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        s.starttls(); s.login(SMTP_USER, SMTP_PASSWORD); s.send_message(m)

def alert_text(stage, network, token, pair, score, m, secm, soft, state):
    b = pair.get("baseToken") or {}
    symbol = b.get("symbol","?")
    name = b.get("name","?")
    first = f(state.get("first_price")) if state else 0
    move = ((m["price"]/first)-1)*100 if first and m["price"] else 0
    return f"""{"🟢" if stage=="ENTRY READY" else "🟡"} MEME HUNTER V2 — {stage}

{name} ({symbol}) | {network}
Score: {score}/100
Confirmations: {state.get("confirms",0)}/{CONFIRM_SCANS}

Age: {m["age"]} min
Liquidity: ${m["liq"]:,.0f}
FDV/MC: ${m["fdv"]:,.0f}
5m volume: ${m["vol5"]:,.0f}
5m buys/sells: {m["buys"]}/{m["sells"]}  ratio {m["ratio"]}
5m change: {m["ch5"]}%
Move since discovery: {move:+.1f}%

Top10: {secm.get("top10_pct",0)}%
Largest holder: {secm.get("max_holder_pct",0)}%
Creator holding: {secm.get("creator_pct",0)}%
Unlocked LP: {secm.get("unlocked_lp_pct",0)}%
Soft flags: {", ".join(soft) if soft else "none"}

Contract:
{token}

{pair.get("url","")}

⚠️ EARLY-CANDIDATE SIGNAL ONLY. 20–100x is never guaranteed.
Do not buy without manually confirming contract, sellability and liquidity."""

# ---------------- ENGINE ----------------
def scan_once():
    for network in NETWORKS:
        try:
            pools = new_pools(network)
        except Exception as e:
            print(network, "discovery error", e); continue

        for p in pools:
            token = p["token"]
            try:
                pair = dex_pair(network, token)
                if not pair: continue
                market_score, rejected, m = analyze_market(pair, p["created"])
                if rejected:
                    continue

                sec = goplus(network, token)
                hard, soft, sec_penalty, secm = analyze_security(network, sec)
                if hard:
                    print(network, token[:10], "HARD REJECT", ",".join(hard))
                    continue

                score = max(0, min(100, round(market_score - sec_penalty)))
                passed = score >= MIN_WATCH_SCORE
                symbol = (pair.get("baseToken") or {}).get("symbol","?")
                state = upsert_state(network, token, p["pool"], symbol, m["price"], score, passed)

                # Stage 1: WATCH — early notice
                if score >= MIN_WATCH_SCORE and not state["watch_sent"]:
                    txt = alert_text("WATCH", network, token, pair, score, m, secm, soft, state)
                    send_telegram(txt); send_email(f"Meme Hunter WATCH {symbol} {score}/100", txt)
                    set_sent(network, token, "watch_sent")
                    print(txt)

                # Stage 2: ENTRY READY — must survive repeated scans.
                ok, why_not = confirmation_ok(state, m)
                state = get_state(network, token)
                if (score >= MIN_ENTRY_SCORE and state["confirms"] >= CONFIRM_SCANS
                    and ok and not state["entry_sent"]):
                    txt = alert_text("ENTRY READY", network, token, pair, score, m, secm, soft, state)
                    send_telegram(txt); send_email(f"Meme Hunter ENTRY READY {symbol} {score}/100", txt)
                    set_sent(network, token, "entry_sent")
                    print(txt)

                time.sleep(0.3)
            except Exception as e:
                print(network, token[:10], "error", e)

def main():
    init_db()
    print("Meme Hunter V2 started:", NETWORKS)
    while True:
        started = time.time()
        try:
            scan_once()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print("cycle error", e)
        time.sleep(max(5, POLL_SECONDS - (time.time()-started)))

if __name__ == "__main__":
    main()
