import time
import logging
import requests
import threading
from binance.client import Client
from binance.exceptions import BinanceAPIException
import pandas as pd
import ta
from flask import Flask, jsonify, render_template_string

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
API_KEY = "zgt7ldFsw85bGo3H6tENLSDfCqEWZiKE4Anq0tPpTfFyMZO5zSjp9oqzwbnHNRBc"
API_SECRET = "VKSbu4Y8GE6KGYfE9zlWyRxQ3ASj6Vo8CAa1mWlnwBnrSN1X6m7A1CGpBw9DaKiq"

TELEGRAM_TOKEN = "8805796967:AAFGcbGIfYZaVwsurVLMsMb27NIvcoeyzIQ"
TELEGRAM_CHAT_ID = "6381216252"

SYMBOL = "BTCUSDT"
TRADE_USDT = 20
STOP_LOSS_PCT = 0.02
TAKE_PROFIT_PCT = 0.04

EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# SHARED STATE (for dashboard)
# ─────────────────────────────────────────────
state = {
    "price": 0,
    "signal": "HOLD",
    "in_position": False,
    "entry_price": None,
    "trades": [],
    "pnl_total": 0.0
}

# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"})
    except Exception as e:
        log.error(f"Telegram error: {e}")

# ─────────────────────────────────────────────
# FLASK DASHBOARD
# ─────────────────────────────────────────────
app = Flask(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Trading Bot Dashboard</title>
    <meta http-equiv="refresh" content="10">
    <style>
        body { font-family: Arial, sans-serif; background: #0d1117; color: #e6edf3; padding: 30px; }
        h1 { color: #58a6ff; }
        .card { background: #161b22; border-radius: 10px; padding: 20px; margin: 15px 0; border: 1px solid #30363d; }
        .green { color: #3fb950; } .red { color: #f85149; } .yellow { color: #d29922; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #30363d; }
        th { color: #58a6ff; }
        .big { font-size: 2em; font-weight: bold; }
    </style>
</head>
<body>
    <h1>🤖 Trading Bot Dashboard</h1>
    <div class="card">
        <p>💰 BTC Price: <span class="big">${{ state.price | int }}</span></p>
        <p>📊 Signal: <span class="{{ 'green' if state.signal == 'BUY' else 'red' if state.signal == 'SELL' else 'yellow' }} big">{{ state.signal }}</span></p>
        <p>📈 In Position: <span class="{{ 'green' if state.in_position else 'red' }}">{{ 'YES' if state.in_position else 'NO' }}</span></p>
        {% if state.entry_price %}
        <p>🎯 Entry Price: ${{ state.entry_price | int }}</p>
        {% endif %}
        <p>💵 Total PnL: <span class="{{ 'green' if state.pnl_total >= 0 else 'red' }}">${{ "%.2f" | format(state.pnl_total) }}</span></p>
    </div>
    <div class="card">
        <h2>📋 Trade History</h2>
        <table>
            <tr><th>Type</th><th>Price</th><th>PnL</th><th>Time</th></tr>
            {% for t in state.trades | reverse %}
            <tr>
                <td class="{{ 'green' if t.type == 'BUY' else 'red' }}">{{ t.type }}</td>
                <td>${{ t.price | int }}</td>
                <td class="{{ 'green' if t.pnl >= 0 else 'red' }}">{{ "$%.2f" | format(t.pnl) if t.pnl else '-' }}</td>
                <td>{{ t.time }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
    <p style="color:#666">Auto-refreshes every 10 seconds</p>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML, state=state)

@app.route("/api/state")
def api_state():
    return jsonify(state)

def run_dashboard():
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)

# ─────────────────────────────────────────────
# BOT
# ─────────────────────────────────────────────
class TradingBot:
    def __init__(self):
        self.client = Client(API_KEY, API_SECRET)
        self.in_position = False
        self.entry_price = None
        self.quantity = None
        log.info(f"Bot started — trading {SYMBOL}")
        send_telegram(f"🤖 <b>Trading Bot Started</b>\nPair: {SYMBOL}\nTrade size: ${TRADE_USDT} USDT")

    def get_candles(self, interval="15m", limit=100):
        candles = self.client.get_klines(symbol=SYMBOL, interval=interval, limit=limit)
        df = pd.DataFrame(candles, columns=[
            "time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ])
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        return df

    def get_signal(self, df):
        df["ema_fast"] = ta.trend.ema_indicator(df["close"], window=EMA_FAST)
        df["ema_slow"] = ta.trend.ema_indicator(df["close"], window=EMA_SLOW)
        df["rsi"] = ta.momentum.rsi(df["close"], window=RSI_PERIOD)
        last = df.iloc[-1]
        prev = df.iloc[-2]
        ema_cross_up = prev["ema_fast"] < prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
        ema_cross_down = prev["ema_fast"] > prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]
        if ema_cross_up and last["rsi"] < RSI_OVERBOUGHT:
            return "BUY"
        elif ema_cross_down and last["rsi"] > RSI_OVERSOLD:
            return "SELL"
        return "HOLD"

    def get_price(self):
        ticker = self.client.get_symbol_ticker(symbol=SYMBOL)
        return float(ticker["price"])

    def get_quantity(self, usdt_amount, price):
        info = self.client.get_symbol_info(SYMBOL)
        step_size = float(next(
            f["stepSize"] for f in info["filters"] if f["filterType"] == "LOT_SIZE"
        ))
        qty = usdt_amount / price
        precision = len(str(step_size).rstrip("0").split(".")[-1])
        return round(qty, precision)

    def buy(self):
        try:
            price = self.get_price()
            qty = self.get_quantity(TRADE_USDT, price)
            order = self.client.order_market_buy(symbol=SYMBOL, quantity=qty)
            self.in_position = True
            self.entry_price = price
            self.quantity = qty
            log.info(f"BUY {qty} {SYMBOL} @ ${price:.2f}")
            state["in_position"] = True
            state["entry_price"] = price
            state["trades"].append({"type": "BUY", "price": price, "pnl": None, "time": time.strftime("%H:%M:%S")})
            send_telegram(
                f"🟢 <b>BUY</b> {SYMBOL}\n"
                f"💰 Price: ${price:.2f}\n"
                f"📦 Qty: {qty}\n"
                f"🎯 Take Profit: ${price * (1 + TAKE_PROFIT_PCT):.2f}\n"
                f"🛑 Stop Loss: ${price * (1 - STOP_LOSS_PCT):.2f}"
            )
        except BinanceAPIException as e:
            log.error(f"Buy failed: {e}")
            send_telegram(f"❌ Buy failed: {e}")

    def sell(self, reason="Signal"):
        try:
            price = self.get_price()
            order = self.client.order_market_sell(symbol=SYMBOL, quantity=self.quantity)
            pnl = (price - self.entry_price) * self.quantity
            state["pnl_total"] += pnl
            log.info(f"SELL {self.quantity} {SYMBOL} @ ${price:.2f} | PnL: ${pnl:.2f}")
            state["in_position"] = False
            state["entry_price"] = None
            state["trades"].append({"type": "SELL", "price": price, "pnl": pnl, "time": time.strftime("%H:%M:%S")})
            send_telegram(
                f"{'🔴' if pnl < 0 else '✅'} <b>SELL</b> {SYMBOL} ({reason})\n"
                f"💰 Price: ${price:.2f}\n"
                f"{'📉' if pnl < 0 else '📈'} PnL: ${pnl:.2f}\n"
                f"💵 Total PnL: ${state['pnl_total']:.2f}"
            )
            self.in_position = False
            self.entry_price = None
            self.quantity = None
        except BinanceAPIException as e:
            log.error(f"Sell failed: {e}")
            send_telegram(f"❌ Sell failed: {e}")

    def check_stop_loss_take_profit(self):
        if not self.in_position:
            return
        price = self.get_price()
        if price <= self.entry_price * (1 - STOP_LOSS_PCT):
            log.warning(f"STOP LOSS hit @ ${price:.2f}")
            self.sell(reason="Stop Loss")
        elif price >= self.entry_price * (1 + TAKE_PROFIT_PCT):
            log.info(f"TAKE PROFIT hit @ ${price:.2f}")
            self.sell(reason="Take Profit")

    def run(self, interval_seconds=60):
        log.info("Bot is running. Press Ctrl+C to stop.")
        while True:
            try:
                df = self.get_candles()
                signal = self.get_signal(df)
                price = self.get_price()
                state["price"] = price
                state["signal"] = signal
                state["in_position"] = self.in_position
                log.info(f"Price: ${price:.2f} | Signal: {signal} | In position: {self.in_position}")

                if signal == "BUY" and not self.in_position:
                    self.buy()
                elif signal == "SELL" and self.in_position:
                    self.sell(reason="Signal")
                elif self.in_position:
                    self.check_stop_loss_take_profit()

                time.sleep(interval_seconds)

            except KeyboardInterrupt:
                log.info("Bot stopped.")
                send_telegram("🛑 Bot stopped by user.")
                break
            except Exception as e:
                log.error(f"Error: {e}")
                time.sleep(30)


if __name__ == "__main__":
    # Start dashboard in background thread
    t = threading.Thread(target=run_dashboard, daemon=True)
    t.start()
    log.info("Dashboard running at http://localhost:5000")

    # Start bot
    bot = TradingBot()
    bot.run()
