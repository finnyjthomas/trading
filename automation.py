import os
import time
from datetime import datetime
import requests
import pandas as pd
import ta
import smtplib
from email.mime.text import MIMEText
#from numpy import nan as npNaN
# === Configuration ===
TEST_MODE = True  # Set to False to run live

TRADE_AMOUNT_USD = 50.0      # Maximum USD to trade per coin
STOP_LOSS_PERCENT = 0.01     # 1% stop-loss
TAKE_PROFIT_PERCENT = 0.03   # 3% take-profit

# List of coins (symbols as used by Robinhood)
COIN_LIST = ["BTC", "ETH", "ADA", "XRP", "DOGE"]
  # Replace with your API key

# Robinhood credentials (set these as environment variables)
ROBINHOOD_TOKEN = "rh-api-e861379b-6647-4b9a-8568-f0223c785d3d" #os.environ.get("ROBINHOOD_TOKEN")
ROBINHOOD_ACCOUNT_ID ="4SEZokUELmIXu/9JUxn7LDVH8Aiw79NtkSgZyVXjdyI="# os.environ.get("ROBINHOOD_ACCOUNT_ID")  # Your crypto account ID

# Base URL for official Robinhood Crypto endpoints
BASE_URL = "https://api.robinhood.com"

# === Email (Alert) Configuration ===
# The following environment variables must be set:
# EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT, EMAIL_USERNAME, EMAIL_PASSWORD, EMAIL_FROM, EMAIL_TO
positions = {}
def send_email(subject, message):
    """Send an email alert using SMTP."""
    smtp_server = os.environ.get("EMAIL_SMTP_SERVER")
    smtp_port = int(os.environ.get("EMAIL_SMTP_PORT", 587))
    email_username = os.environ.get("EMAIL_USERNAME")
    email_password = os.environ.get("EMAIL_PASSWORD")
    email_from = os.environ.get("EMAIL_FROM")
    email_to = os.environ.get("EMAIL_TO")

    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()  # Secure the connection
        server.login(email_username, email_password)
        server.sendmail(email_from, [email_to], msg.as_string())
        server.quit()
        print("Email sent successfully.")
    except Exception as e:
        print(f"Failed to send email: {e}")

def log_trade(message):
    """Append trade details to a text log."""
    with open("trade_log.txt", "a") as f:
        f.write(f"{datetime.now()} - {message}\n")

# === HTTP Headers for Robinhood API ===
headers = {
    "Authorization": f"Bearer {ROBINHOOD_TOKEN}",
    "Content-Type": "application/json"
}

def get_recent_candles(symbol, interval="1minute", span="day", limit=100):
    """
    Fetch recent historical candle data for a given symbol from Robinhood.
    Endpoint: GET /crypto/historicals/{symbol}/
    """
    url = f"{BASE_URL}/crypto/historicals/{symbol}/"
    params = {
        "interval": interval,
        "span": span
    }
    try:
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        if "results" not in data:
            print(f"No candle data for {symbol}: {data}")
            return None
        df = pd.DataFrame(data["results"])
        # Convert price columns to numeric
        for col in ['open_price', 'close_price', 'high_price', 'low_price']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.sort_values("begins_at").tail(limit)
        return df
    except Exception as e:
        print(f"Error fetching candles for {symbol}: {e}")
        return None

def calculate_indicators(df):
    """Calculate RSI and MACD on 'close_price' and return latest values."""
    df['RSI'] = ta.rsi(df['close_price'], length=14)
    macd_df = ta.macd(df['close_price'], fast=12, slow=26, signal=9)
    df['MACD'] = macd_df['MACD_12_26_9']
    df['MACD_signal'] = macd_df['MACDs_12_26_9']
    rsi = df['RSI'].dropna().iloc[-1]
    macd = df['MACD'].dropna().iloc[-1]
    macd_signal = df['MACD_signal'].dropna().iloc[-1]
    return rsi, macd, macd_signal

def check_signal(symbol):
    """
    Check indicators for the coin.
      - Returns "BUY" if RSI < 30 and MACD > MACD_signal.
      - Returns "SELL" if RSI > 70 and MACD < MACD_signal.
      - Returns None otherwise.
    """
    df = get_recent_candles(symbol)
    if df is None or len(df) < 30:
        return None
    try:
        rsi, macd, macd_signal = calculate_indicators(df)
        print(f"{symbol}: RSI={rsi:.2f}, MACD={macd:.4f}, Signal={macd_signal:.4f}")
        if rsi < 30 and macd > macd_signal:
            return "BUY"
        elif rsi > 70 and macd < macd_signal:
            return "SELL"
    except Exception as e:
        print(f"Error calculating indicators for {symbol}: {e}")
    return None

def get_current_price(symbol):
    """
    Get the current price using the official quote endpoint:
      GET /crypto/quotes/{symbol}/
    """
    url = f"{BASE_URL}/crypto/quotes/{symbol}/"
    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        price = data.get("mark_price") or data.get("last_trade_price")
        return float(price)
    except Exception as e:
        print(f"Error fetching price for {symbol}: {e}")
        return None

def place_order(symbol, side, order_type="market", funds=None, size=None):
    """
    Place an order via the official endpoint:
      POST /crypto/orders/
    For a BUY order, specify 'funds' (USD amount). For a SELL, specify 'size' (quantity).
    """
    url = f"{BASE_URL}/crypto/orders/"
    payload = {
        "account_id": ROBINHOOD_ACCOUNT_ID,
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "time_in_force": "gtc"  # Good 'til canceled
    }
    if side.lower() == "buy" and funds:
        payload["funds"] = str(funds)
    elif side.lower() == "sell" and size:
        payload["size"] = str(size)
    try:
        response = requests.post(url, headers=headers, json=payload)
        order = response.json()
        if response.status_code not in [200, 201]:
            print(f"Order error for {symbol}: {order}")
            return None
        return order
    except Exception as e:
        print(f"Error placing order for {symbol}: {e}")
        return None

# === Main Trading Loop ===
while True:
    for coin in COIN_LIST:
        signal = check_signal(coin)
        current_price = get_current_price(coin)
        if current_price is None:
            continue
        print(f"{datetime.now()} - {coin} current price: ${current_price:.4f} | Signal: {signal}")

        # If already holding a position, check if stop-loss or take-profit conditions are met.
        if coin in positions:
            entry_price = positions[coin]['entry_price']
            if current_price <= entry_price * (1 - STOP_LOSS_PERCENT) or current_price >= entry_price * (1 + TAKE_PROFIT_PERCENT):
                effective_signal = "SELL"
                print(f"{coin}: TP/SL condition met (Entry: ${entry_price:.4f}, Current: ${current_price:.4f}).")
            else:
                effective_signal = signal
        else:
            effective_signal = signal

        # --- BUY Condition ---
        if effective_signal == "BUY" and coin not in positions:
            print(f"Strong BUY signal for {coin}. Attempting to buy ${TRADE_AMOUNT_USD} worth at ${current_price:.4f}")
            if TEST_MODE:
                confirm = input(f"Confirm BUY {coin} for ${TRADE_AMOUNT_USD}? (y/n): ")
                if confirm.lower() != 'y':
                    print("Buy canceled by user.")
                    continue
            order = place_order(coin, side="buy", order_type="market", funds=TRADE_AMOUNT_USD)
            if order:
                print(f"BUY order placed for {coin}: {order}")
                log_trade(f"BUY {coin} @ ${current_price:.4f}, order: {order}")
                send_email("Scalping Bot BUY Alert", f"BUY {coin} @ ${current_price:.4f}")
                filled_size = float(order.get("quantity", 0))
                positions[coin] = {"size": filled_size, "entry_price": current_price}

        # --- SELL Condition ---
        elif effective_signal == "SELL" and coin in positions:
            size_to_sell = positions[coin]['size']
            print(f"Strong SELL signal for {coin}. Attempting to sell {size_to_sell:.6f} units at ${current_price:.4f}")
            if TEST_MODE:
                confirm = input(f"Confirm SELL {coin}? (y/n): ")
                if confirm.lower() != 'y':
                    print("Sell canceled by user.")
                    continue
            order = place_order(coin, side="sell", order_type="market", size=size_to_sell)
            if order:
                print(f"SELL order placed for {coin}: {order}")
                entry_price = positions[coin]['entry_price']
                pnl = ((current_price - entry_price) / entry_price) * 100.0
                log_trade(f"SELL {coin} @ ${current_price:.4f}, P/L = {pnl:.2f}%, order: {order}")
                send_email("Scalping Bot SELL Alert", f"SELL {coin} @ ${current_price:.4f}, P/L = {pnl:.2f}%")
                positions.pop(coin)
        # Else: No action for this coin.
    # Wait 30 seconds before the next cycle.
    time.sleep(30)