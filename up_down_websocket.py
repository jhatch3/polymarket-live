import requests
import json
import time
import threading
import os
from datetime import datetime
from websocket import WebSocketApp

# Sources Claude Sonnet 4.5 // GPT-5.1-Codex-Max 


# ── Config ────────────────────────────────────────────────────────────────────
WS_URL      = "wss://ws-subscriptions-clob.polymarket.com"
GAMMA_URL   = "https://gamma-api.polymarket.com"
MARKET_CHANNEL = "market"

# ── Fetch current BTC Up/Down token IDs ───────────────────────────────────────
def _as_list(x):
    """Gamma sometimes returns lists as JSON strings. Normalize to a Python list."""
    if x is None:
        return []
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        try:
            v = json.loads(x)
            return v if isinstance(v, list) else []
        except Exception:
            return []
    return []

def get_btc_updown_tokens():
    """
    Queries Gamma API for active BTC Up/Down markets.
    Returns list of dicts: {question, yes_token, no_token}
    NOTE: yes_token == UP, no_token == DOWN (printing stays the same).
    """
    print("🔍 Fetching active BTC Up/Down markets...")
    try:
        resp = requests.get(
            f"{GAMMA_URL}/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": 200,
                "tag_slug": "bitcoin",
            },
            timeout=10,
        )
        resp.raise_for_status()
        markets = resp.json()
    except Exception as e:
        print(f"  Failed to fetch from Gamma API: {e}")
        return []

    results = []
    for m in markets:
        question = (m.get("question") or "")
        slug = (m.get("slug") or "")

        if "up or down" not in question.lower() and "updown" not in slug.lower():
            continue

        outcomes = _as_list(m.get("outcomes"))                  # e.g. ["Up","Down"] or ["Yes","No"]
        outcome_token_ids = _as_list(m.get("outcomeTokenIds"))  # sometimes present
        clob_token_ids = _as_list(m.get("clobTokenIds"))        # often present

        up_token = None
        down_token = None

        def map_by_outcomes(tokens):
            nonlocal up_token, down_token
            if not outcomes or not tokens or len(outcomes) != len(tokens):
                return
            for name, tid in zip(outcomes, tokens):
                nl = str(name).strip().lower()
                if nl in ("up", "yes"):     # treat YES as UP
                    up_token = tid
                elif nl in ("down", "no"):  # treat NO as DOWN
                    down_token = tid

        # 1) Best: outcomes aligned with outcomeTokenIds
        map_by_outcomes(outcome_token_ids)

        # 2) Next: outcomes aligned with clobTokenIds
        if up_token is None or down_token is None:
            map_by_outcomes(clob_token_ids)

        # 3) Fallback: just assume binary order if we only have clobTokenIds
        if (up_token is None or down_token is None) and len(clob_token_ids) >= 2:
            up_token = up_token or clob_token_ids[0]
            down_token = down_token or clob_token_ids[1]

        if up_token is None or down_token is None:
            continue

        results.append({
            "question": question,
            "yes_token": up_token,    # keep your existing key names
            "no_token":  down_token,
        })

        # KEEP YOUR PRINTING NORMAL
        print(f"  ✅ Found: {question}")
        print(f"     YES token: {up_token}")
        print(f"     NO  token: {down_token}")

    return results


# ── Display helpers ───────────────────────────────────────────────────────────
def clear():
    os.system("cls" if os.name == "nt" else "clear")

def bar(value, width=20, char="█"):
    filled = int(round(value * width))
    return char * filled + "░" * (width - filled)

def sentiment_label(mid):
    if mid > 0.65: return "🟢 STRONGLY UP"
    if mid > 0.55: return "🟢 UP"
    if mid > 0.45: return "⚪ NEUTRAL"
    if mid > 0.35: return "🔴 DOWN"
    return "🔴 STRONGLY DOWN"

def imbalance_label(imb):
    if imb >  0.3: return "🟢 BUY PRESSURE"
    if imb < -0.3: return "🔴 SELL PRESSURE"
    return "⚪ BALANCED"


# ── Order Book ────────────────────────────────────────────────────────────────
class BookState:
    def __init__(self, label):
        self.label   = label
        self.bids    = {}   # price -> size
        self.asks    = {}
        self.history = []   # (timestamp, mid)

    def best_bid(self): return max(self.bids) if self.bids else None
    def best_ask(self): return min(self.asks) if self.asks else None

    def mid(self):
        bb, ba = self.best_bid(), self.best_ask()
        if bb and ba:
            return (bb + ba) / 2
        return None

    def spread(self):
        bb, ba = self.best_bid(), self.best_ask()
        if bb and ba:
            return ba - bb
        return None

    def imbalance(self, levels=5):
        bids_sorted = sorted(self.bids.items(), reverse=True)[:levels]
        asks_sorted = sorted(self.asks.items())[:levels]
        bv = sum(s for _, s in bids_sorted)
        av = sum(s for _, s in asks_sorted)
        total = bv + av
        return (bv - av) / total if total > 0 else 0

    def depth(self, levels=5):
        bids_sorted = sorted(self.bids.items(), reverse=True)[:levels]
        asks_sorted = sorted(self.asks.items())[:levels]
        return bids_sorted, asks_sorted

    def record_history(self):
        m = self.mid()
        if m:
            self.history.append((datetime.now(), m))
            if len(self.history) > 60:
                self.history.pop(0)

    def mini_chart(self, width=30):
        if len(self.history) < 2:
            return "  [building history...]"
        vals = [v for _, v in self.history]
        lo, hi = min(vals), max(vals)
        rng = hi - lo or 0.001
        rows = 4
        grid = [[" "] * width for _ in range(rows)]
        step = max(1, len(vals) // width)
        for i in range(width):
            idx = min(i * step, len(vals) - 1)
            v = vals[idx]
            row = rows - 1 - int((v - lo) / rng * (rows - 1))
            grid[row][i] = "·"
        lines = ["  " + "".join(row) for row in grid]
        lines.append(f"  {lo:.3f}" + " " * (width - 10) + f"{hi:.3f}")
        return "\n".join(lines)


# ── WebSocket ─────────────────────────────────────────────────────────────────
class BTCUpDownWatcher:
    def __init__(self, markets):
        """
        markets: list of dicts with keys: question, yes_token, no_token
        """
        # Map token_id -> BookState
        self.books = {}
        self.token_to_market = {}   # token_id -> (question, side)

        all_tokens = []
        for m in markets:
            yes_token = str(m["yes_token"])
            no_token  = str(m["no_token"])
            label_yes = f"{m['question']} [YES/UP]"
            label_no  = f"{m['question']} [NO/DOWN]"
            self.books[yes_token] = BookState(label_yes)
            self.books[no_token]  = BookState(label_no)
            self.token_to_market[yes_token] = (m["question"], "UP")
            self.token_to_market[no_token]  = (m["question"], "DOWN")
            all_tokens.append(yes_token)
            all_tokens.append(no_token)

        self.all_tokens   = all_tokens
        self._stop        = threading.Event()
        self._last_render = 0

        self.ws = WebSocketApp(
            f"{WS_URL}/ws/{MARKET_CHANNEL}",
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

    # ── WS callbacks ─────────────────────────────────────────────────────────
    def _on_open(self, ws):
        print("✅ Connected to Polymarket WebSocket")
        ws.send(json.dumps({
            "assets_ids": self.all_tokens,
            "type": "MARKET",  # official channel id (USER or MARKET)
        }))
        t = threading.Thread(target=self._ping, args=(ws,), daemon=True)
        t.start()

    def _on_message(self, ws, raw):
        if raw == "PONG":
            return
        try:
            data = json.loads(raw)
        except Exception:
            return

        messages = data if isinstance(data, list) else [data]
        for msg in messages:
            try:
                self._handle_msg(msg)
            except Exception as e:
                # Defensive: never let handler exceptions kill the socket
                import traceback
                print(f"\n⚠️  Handler error: {e!r}")
                traceback.print_exc()

        # Throttle rendering to ~4 fps
        now = time.time()
        if now - self._last_render > 0.25:
            self._last_render = now
            self._render()

    def _handle_msg(self, data):
        """Process a single websocket message. Ignores non-dict payloads."""
        if not isinstance(data, dict):
            return

        event_type = data.get("event_type")
        asset_id   = str(data.get("asset_id"))

        if asset_id not in self.books:
            return

        book = self.books[asset_id]

        def _parse_levels(levels):
            parsed = {}
            for lvl in levels:
                if isinstance(lvl, dict):
                    try:
                        price = float(lvl.get("price"))
                        size  = float(lvl.get("size"))
                    except Exception:
                        continue
                elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                    try:
                        size  = float(lvl[0])
                        price = float(lvl[1])
                    except Exception:
                        continue
                else:
                    continue
                parsed[price] = size
            return parsed

        if event_type == "book":
            bids_raw = data.get("bids") or data.get("buys") or []
            asks_raw = data.get("asks") or data.get("sells") or []
            book.bids = _parse_levels(bids_raw)
            book.asks = _parse_levels(asks_raw)
            book.record_history()

        elif event_type == "price_change":
            changes = data.get("changes") or data.get("price_changes") or []
            for change in changes:
                side  = "bids" if change["side"] == "BUY" else "asks"
                price = float(change["price"])
                size  = float(change["size"])
                if size == 0:
                    getattr(book, side).pop(price, None)
                else:
                    getattr(book, side)[price] = size
            book.record_history()
        else:
            #print(f"\nℹ️  Skipped event_type={event_type}: {data}")
            pass

    def _on_error(self, ws, error):
        # Show repr so empty-string errors are still visible
        print(f"\n⚠️  WS Error: {error!r}")

    def _on_close(self, ws, code, msg):
        print(f"\n🔌 Connection closed ({code}: {msg})")
        self._stop.set()

    def _ping(self, ws):
        while not self._stop.is_set():
            try:
                ws.send("PING")
            except Exception:
                break
            time.sleep(10)

    # ── Render ────────────────────────────────────────────────────────────────
    def _render(self):
        clear()
        now = datetime.now().strftime("%H:%M:%S")
        print(f"{'─'*60}")
        print(f"  🟠 BTC Up/Down — Polymarket Live Feed       {now}")
        print(f"{'─'*60}")

        # Group YES/NO pairs by question
        seen = set()
        for token_id, (question, side) in self.token_to_market.items():
            if question in seen:
                continue
            seen.add(question)

            # Find YES and NO books for this question
            yes_book = next(
                (b for tid, b in self.books.items()
                 if self.token_to_market[tid] == (question, "UP")), None
            )
            no_book = next(
                (b for tid, b in self.books.items()
                 if self.token_to_market[tid] == (question, "DOWN")), None
            )

            print(f"\n  📊 {question}")
            print(f"  {'─'*56}")

            self._render_book_pair(yes_book, no_book)

        print(f"\n{'─'*60}")
        print("  Press Ctrl+C to exit")

    def _render_book_pair(self, yes_book, no_book):
        if not yes_book or not no_book:
            print("  [waiting for data...]")
            return

        yes_mid = yes_book.mid()
        if yes_mid is None:
            print("  [waiting for data...]")
            return

        yes_spread  = yes_book.spread() or 0
        yes_imb     = yes_book.imbalance()
        no_mid      = no_book.mid()
        no_spread   = no_book.spread() or 0

        # Probability bars
        up_prob   = yes_mid
        down_prob = 1 - yes_mid  # complement (matches YES=UP)

        print(f"\n  UP   {bar(up_prob, 24)} {up_prob*100:5.1f}%  {sentiment_label(up_prob)}")
        print(f"  DOWN {bar(down_prob, 24)} {down_prob*100:5.1f}%")

        # Key metrics
        print(f"\n  YES token │ bid={yes_book.best_bid():.4f}  ask={yes_book.best_ask():.4f}  "
              f"mid={yes_mid:.4f}  spread={yes_spread:.4f}")
        if no_mid:
            print(f"  NO  token │ bid={no_book.best_bid():.4f}  ask={no_book.best_ask():.4f}  "
                  f"mid={no_mid:.4f}  spread={no_spread:.4f}")

        # Sanity check: YES mid + NO mid should ≈ 1.0
        if no_mid:
            total = yes_mid + no_mid
            edge = 1 - total          # positive => underround (cheap), negative => overround (rich)
            label = "🚀 ARB WINDOW" if abs(edge) >= 0.01 else "⚖️ Fair"
            print(f"  YES+NO    │ {total:.4f}  edge={edge:+.3f}  {label}")

        # Order book imbalance
        print(f"\n  Order flow: {imbalance_label(yes_imb)}  ({yes_imb:+.2f})")
        print(f"  {bar(max(0, yes_imb), 12, '▶')}|{bar(max(0, -yes_imb), 12, '◀')}")

        # Depth ladder (top 3 levels)
        bids, asks = yes_book.depth(3)
        print(f"\n  ── YES Order Book (top 3) ──")
        for p, s in asks[::-1]:
            print(f"     ASK  {p:.4f}  {s:>8.2f} USDC")
        print(f"     {'─'*30}")
        for p, s in bids:
            print(f"     BID  {p:.4f}  {s:>8.2f} USDC")

        # Mini sparkline
        print(f"\n  Mid price history (YES token):")
        print(yes_book.mini_chart())

    # ── Run ───────────────────────────────────────────────────────────────────
    def run(self):
        self.ws.run_forever()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":

    #  ==========================================================
    #           Set You Market Values Here !!!
    #  ==========================================================

    markets = [{
    "question": " ", 
    "yes_token": " ",  # Up
    "no_token":  " ",  # Down
    }]

    #  ==========================================================
    #  ==========================================================


    if (markets[0]["question"] == " ") or (markets[0]["yes_token"] == " ") or (markets[0]["no_token"] == " "):
        print("\n⚠️  No active BTC Up/Down markets found.")
        print("   You can manually set token IDs like this:")
        print("""
   markets = [{
       "question": "BTC Up or Down - Feb 18, 2:00PM-2:15PM ET",
       "yes_token": "YOUR_YES_TOKEN_ID_HERE",
       "no_token":  "YOUR_NO_TOKEN_ID_HERE",
   }]
""")
    else:
        # Optionally limit to most recent market (last in list)clear

        markets = markets[-1:]  # watch just the current one; remove slice for all

    # If all market values are not nothing then we can start 
    if not (markets[0]["question"] == " ") or not (markets[0]["yes_token"] == " ") or not (markets[0]["no_token"] == " "):
        watcher = BTCUpDownWatcher(markets)
        try:
            watcher.run()
        except KeyboardInterrupt:
            print("\n👋 Stopped.")
