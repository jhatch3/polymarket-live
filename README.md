# Polymarket BTC Up/Down Live Monitor

A tiny CLI that fetches Polymarket clob token IDs for BTC 5-minute Up/Down markets and streams their order books via WebSocket.

## Setup
1. Install Python 3.10+ & Git
2. Clone this repo onto your machine 
   ```powershell
   git clone https://github.com/jhatch3/polymarket-live.git
   ```
3. cd in the folder, and create a virtualenv:
   ```powershell
   cd polymarket-live
   python -m venv .venv
   .\.venv\Scripts\activate
   ```
4. Install deps:
   ```powershell
   pip install -r requirements.txt
   ```
## Get Running 

There are two main things needed to get this app running:

   1. Get YES/NO tokens for a 5m market
   2. Run the live order-book monitor

## Get YES/NO tokens for a 5m market
Use the helper:
```powershell
python fetch_tokens.py https://polymarket.com/event/btc-updown-5m-1771442700
```
To get a valid url, go to 

> https://polymarket.com/event/btc-updown-5m-1771448700 

And go to the 5 min window after the current live one, and copy the url. For example if the live time is 4:25pm go to the 4:30pm window and grab that url. I recomenddoing this so you get the full 5 min visualization. You can do the same for any Bitcoin Up or Down market.

Output shows the question plus the two `clobTokenIds` in outcome order (Up, Down). You can also pass the slug alone.

## Run the live order-book monitor
1. Open `up_down_websocket.py` and set the `markets` list near the bottom using the tokens from `fetch_tokens.py`.

   Here (Line 428): 
   ```     
   #  ==========================================================
   #           Set You Market Values Here !!!
   #  ==========================================================

   markets = [{
   "question": " ", 
   "yes_token": " ",  # Up
   "no_token":  " ",  # Down
   }]
   ```
   Example:
   ```python
   markets = [{
       "question": "BTC Up or Down - Feb 18, 2:25-2:30pm ET",
       "yes_token": "<Up token>",
       "no_token":  "<Down token>",
   }]
   ```
2. Start the feed:
   ```powershell
   python up_down_websocket.py
   ```

The dashboard shows bids/asks, mid, spread, order-imbalance, sparkline, and a YES+NO sum with an arb signal ("🚀 ARB WINDOW" when |edge| ≥ 0.01).

## Notes
- If your terminal chokes on emojis, set `PYTHONIOENCODING=utf-8` or strip them.
- The WS feed only renders when book/price updates arrive; quiet markets may appear idle even though the connection is live.
