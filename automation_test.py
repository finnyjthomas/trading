#!/usr/bin/env python3
"""
robinhood_bot.py

This module implements the CryptoAPITrading class that:
  - Fetches historical data from Coinbase with configurable chart type.
  - Calculates MACD (12,26,9) and volume condition (current volume >= avg volume of previous 10 candles).
  - Buys if a bullish MACD crossover is detected and volume condition is met (only if not already active).
    • Buy is a LIMIT order at ask + $0.01.
    • Immediately after buy, a STOP LOSS order is sent at 5% below the buy price.
  - Sells if either a bearish MACD crossover is detected (on daily data) or profit reaches 10%.
    • Before selling, any stop loss order is cancelled.
  - Open trades are stored in a JSON file with details: (buy_price, quantity, stop_loss, stop_order_id, status).
  - A transaction_callback is called when trades occur so the GUI can display messages.
  - All actions are logged.
  - The bot runs in a loop (every 30 sec) scanning coins.
"""

import base64
import datetime
import json
import os
import time
import uuid
import requests
import pandas as pd
import ta
import logging
from typing import Any, Dict, Optional, Tuple, List
from nacl.signing import SigningKey
import signal

# Define color functions for console logging.
def red_text(msg: str) -> str:
    return f"\033[91m{msg}\033[0m"

def green_text(msg: str) -> str:
    return f"\033[92m{msg}\033[0m"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("trade_log.log", mode='a')
    ]
)

def timeout_handler(signum, frame):
    raise TimeoutError

def get_chart_parameters(chart_type: str) -> Tuple[int, int]:
    """
    Returns a tuple (days, granularity) based on chart_type.
      - "5min": 1 day of 5-minute candles (300 sec) ~288 candles.
      - "15min": 1 day of 15-minute candles (900 sec) ~96 candles.
      - "1day": 50 days of daily candles (86400 sec).
    """
    if chart_type == "5min":
        return (1, 300)
    elif chart_type == "15min":
        return (1, 900)
    elif chart_type == "1day":
        return (50, 86400)
    else:
        return (50, 86400)

class CryptoAPITrading:
    def __init__(self) -> None:
        """
        Initialize the bot.
        open_trades: symbol -> (buy_price, quantity, stop_loss, stop_order_id, status)
          Status is "active" when bought, "sold" after exit.
        Also, transaction_callback (if set) is called with a message after a trade.
        """
        self.api_key = "rh-api-e861379b-6647-4b9a-8568-f0223c785d3d"  # Replace with your key
        base64_key = "4SEZokUELmIXu/9JUxn7LDVH8Aiw79NtkSgZyVXjdyI="    # Replace with your key
        private_key_seed = base64.b64decode(base64_key)
        self.private_key = SigningKey(private_key_seed)
        self.base_url = "https://trading.robinhood.com"
        self.session = requests.Session()
        self.open_trades: Dict[str, Tuple[float, float, float, Optional[str], str]] = {}
        self.load_open_trades()
        self.transaction_callback = None  # NEW: Callback for transactions
        logging.info("Initialized CryptoAPITrading client.")

    def load_open_trades(self) -> None:
        filename = "open_trades.json"
        if os.path.exists(filename):
            try:
                with open(filename, "r") as f:
                    data = json.load(f)
                new_trades = {}
                for k, v in data.items():
                    if isinstance(v, list) and len(v) >= 5:
                        buy_price = float(v[0])
                        quantity = float(v[1])
                        stop_loss = float(v[2])
                        stop_order_id = v[3] if v[3] != "" else None
                        status = v[4]
                        new_trades[k] = (buy_price, quantity, stop_loss, stop_order_id, status)
                    else:
                        logging.error(f"Invalid format for trade {k}: {v}")
                self.open_trades = new_trades
                logging.info("Open trades loaded from file.")
            except Exception as e:
                logging.error(f"Error loading open trades: {e}")
                self.open_trades = {}
        else:
            logging.info("No open trades file found. Starting fresh.")
            self.open_trades = {}

    def save_open_trades(self) -> None:
        filename = "open_trades.json"
        try:
            with open(filename, "w") as f:
                data = {k: [v[0], v[1], v[2], v[3] if v[3] is not None else "", v[4]] for k, v in self.open_trades.items()}
                json.dump(data, f)
            logging.info("Open trades saved to file.")
        except Exception as e:
            logging.error(f"Error saving open trades: {e}")

    def get_query_params(self, key: str, *args: Optional[str]) -> str:
        if not args:
            return ""
        params = [f"{key}={arg}" for arg in args]
        return "?" + "&".join(params)

    def get_signed_headers(self, path: str, method: str, body: str = "") -> Dict[str, str]:
        timestamp = int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp())
        message_to_sign = f"{self.api_key}{timestamp}{path}{method}{body}"
        signed = self.private_key.sign(message_to_sign.encode("utf-8"))
        headers = {
            "x-api-key": self.api_key,
            "x-signature": base64.b64encode(signed.signature).decode("utf-8"),
            "x-timestamp": str(timestamp)
        }
        logging.debug(f"Generated headers: {headers}")
        return headers

    def make_api_request(self, method: str, path: str, body: str = "") -> Optional[Any]:
        url = self.base_url + path
        headers = self.get_signed_headers(path, method, body)
        try:
            if method.upper() == "GET":
                response = self.session.get(url, headers=headers, timeout=10)
            else:
                response = self.session.post(url, headers=headers, json=json.loads(body), timeout=10)
            logging.info(f"API Response ({method} {url}): {response.status_code} - {response.text}")
            if response.status_code not in (200, 201):
                logging.error(f"API Error: {response.status_code} - {response.text}")
                return None
            time.sleep(0.5)
            return response.json()
        except requests.RequestException as e:
            logging.error(f"Error making API request: {e}")
            return None

    def get_account_info(self) -> Optional[Any]:
        path = "/api/v1/crypto/trading/accounts/"
        return self.make_api_request("GET", path)

    def get_buying_power(self) -> float:
        account_info = self.get_account_info()
        if account_info and "buying_power" in account_info:
            try:
                bp = float(account_info["buying_power"])
                logging.info(f"Buying Power Available: ${bp}")
                return bp
            except (ValueError, TypeError):
                logging.error("Error converting buying power to float.")
        logging.error("Could not fetch buying power.")
        return 0.0

    def get_best_bid_ask(self, symbol: str) -> Optional[Any]:
        path = f"/api/v1/crypto/marketdata/best_bid_ask/?symbol={symbol}"
        return self.make_api_request("GET", path)

    def get_historical_prices(self, symbol: str, chart_type: str = "1day") -> Optional[pd.DataFrame]:
        try:
            days, granularity = get_chart_parameters(chart_type)
            end = datetime.datetime.utcnow()
            start = end - datetime.timedelta(days=days)
            url = f"https://api.exchange.coinbase.com/products/{symbol}/candles"
            params = {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "granularity": granularity
            }
            response = requests.get(url, params=params)
            if response.status_code != 200:
                logging.error(f"Error: Coinbase Exchange API returned {response.status_code}: {response.text}")
                return None
            data = response.json()
            if not data:
                logging.error("No data returned from Coinbase Exchange API")
                return None
            df = pd.DataFrame(data, columns=["time", "low", "high", "open", "close", "volume"])
            df["time"] = pd.to_datetime(df["time"], unit='s')
            df = df.sort_values("time")
            return df
        except Exception as e:
            logging.error(f"Exception in get_historical_prices for {symbol}: {e}")
            return None

    def calculate_indicators(self, symbol: str, chart_type: str = "1day") -> Tuple[Optional[float], Optional[float], Optional[bool]]:
        """
        Calculates MACD (12,26,9) and computes volume condition.
        Volume condition: current volume >= average volume of previous 10 candles.
        Returns:
          - Latest MACD value,
          - Latest MACD signal,
          - Volume condition (True if met, else False).
        """
        df = self.get_historical_prices(symbol, chart_type)
        if df is None or df.empty:
            return None, None, None
        try:
            df["close"] = df["close"].astype(float)
        except Exception as e:
            logging.error(f"Error converting close prices to float for {symbol}: {e}")
            return None, None, None
        try:
            macd_indicator = ta.trend.MACD(df["close"], window_fast=12, window_slow=26, window_sign=9)
            df["macd"] = macd_indicator.macd()
            df["macd_signal"] = macd_indicator.macd_signal()
            latest_macd = df["macd"].iloc[-1]
            latest_signal = df["macd_signal"].iloc[-1]
            if len(df) >= 11:
                avg_volume = df["volume"].iloc[-11:-1].astype(float).mean()
            else:
                avg_volume = df["volume"].astype(float).mean()
            current_volume = float(df["volume"].iloc[-1])
            volume_condition = current_volume >= avg_volume
            logging.info(f"Indicators for {symbol} ({chart_type}) | MACD: {latest_macd:.4f}, Signal: {latest_signal:.4f}, Volume Condition: {volume_condition} (Current: {current_volume}, Avg10: {avg_volume:.2f})")
            return latest_macd, latest_signal, volume_condition
        except Exception as e:
            logging.error(f"Error calculating indicators for {symbol}: {e}")
            return None, None, None

    def confirm_trade(self, action: str, symbol: str, quantity: float, total_usd: float, profit_loss: Optional[float] = None) -> bool:
        """
        Text-based confirmation (will be overridden by GUI).
        """
        print("\n----------------------------------")
        print(f"Trade Action: {action.upper()} for {symbol}")
        print(f"Quantity: {quantity:.8f}")
        print(f"Total USD: ${total_usd:.2f}")
        if profit_loss is not None:
            print(f"Estimated Profit/Loss: ${profit_loss:.2f}")
        print("----------------------------------")
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(30)
        try:
            user_input = input("Proceed with this trade? (Y/N): ")
            signal.alarm(0)
            return user_input.strip().lower() == "y"
        except TimeoutError:
            logging.info("No response within 30 seconds; defaulting to skip trade.")
            return False

    def format_quantity(self, q: float) -> str:
        if q < 1:
            rounded = int(q / 1e-7) * 1e-7
            return f"{rounded:.8f}"
        else:
            rounded = int(q / 0.1) * 0.1
            return f"{rounded:.2f}"

    def place_order(self, side: str, symbol: str, amount: float, limit_price: Optional[float] = None) -> Optional[Any]:
        """
        Places a LIMIT order for buying (if side=="buy" and limit_price provided) or a market order for selling.
        """
        asset_quantity_str = self.format_quantity(amount)
        if side.lower() == "buy" and limit_price is not None:
            body = {
                "client_order_id": str(uuid.uuid4()),
                "side": side,
                "type": "limit",
                "symbol": symbol,
                "limit_order_config": {
                    "asset_quantity": asset_quantity_str,
                    "limit_price": f"{limit_price:.2f}"
                }
            }
        else:
            body = {
                "client_order_id": str(uuid.uuid4()),
                "side": side,
                "type": "market",
                "symbol": symbol,
                "market_order_config": {"asset_quantity": asset_quantity_str}
            }
        path = "/api/v1/crypto/trading/orders/"
        order_response = self.make_api_request("POST", path, json.dumps(body))
        if order_response:
            logging.info(f"Order placed: {side.upper()} {symbol} | Amount: {asset_quantity_str}")
        else:
            logging.error("Order placement failed.")
        return order_response

    def place_stop_loss_order(self, side: str, symbol: str, amount: float, stop_price: float) -> Optional[str]:
        asset_quantity_str = self.format_quantity(amount)
        stop_price_str = f"{stop_price:.2f}"
        body = {
            "client_order_id": str(uuid.uuid4()),
            "side": side,
            "type": "stop_limit",
            "symbol": symbol,
            "stop_limit_order_config": {
                "asset_quantity": asset_quantity_str,
                "stop_price": stop_price_str,
                "limit_price": stop_price_str,  # Using the same value for limit price
                "time_in_force": "gtc"          # Good 'Til Canceled – order remains active until cancelled
            }
        }
        path = "/api/v1/crypto/trading/orders/"
        order_response = self.make_api_request("POST", path, json.dumps(body))
        if order_response:
            logging.info(f"Stop loss order placed for {symbol} | Amount: {asset_quantity_str}, Stop Price: {stop_price_str}")
            return order_response.get("id")
        else:
            logging.error("Stop loss order placement failed.")
            return None
    def cancel_order(self, order_id: str) -> Optional[Any]:
        path = f"/api/v1/crypto/trading/orders/{order_id}/cancel/"
        return self.make_api_request("POST", path)

    def trade_crypto(self, symbol: str, chart_type: str = "1day") -> None:
        buying_power = self.get_buying_power()
        if buying_power < 10:
            logging.error(red_text(f"Not enough buying power: ${buying_power}"))
            return
        if symbol in self.open_trades and self.open_trades[symbol][4] == "active":
            logging.info(f"{symbol} is already active. Skipping buy.")
            return
        ticker = self.get_best_bid_ask(symbol)
        if not ticker:
            logging.error(red_text(f"Unable to fetch ticker for {symbol}"))
            return
        try:
            ask_price = float(ticker["results"][0]["ask_inclusive_of_buy_spread"])
        except Exception as e:
            logging.error(red_text(f"Error parsing ask price for {symbol}: {e}"))
            return
        macd, macd_signal, vol_cond = self.calculate_indicators(symbol, chart_type)
        if macd is None or macd_signal is None or vol_cond is None:
            logging.error(red_text(f"Could not fetch complete indicators for {symbol}"))
            return
        logging.info(f"{symbol} ({chart_type}) | MACD: {macd:.4f}, Signal: {macd_signal:.4f}, Volume Condition: {vol_cond}, Ask Price: ${ask_price}")
        if macd > macd_signal and vol_cond:
            logging.info(green_text(f"Bullish condition met for {symbol}."))
            trade_usd = min(buying_power, 50)
            limit_price = ask_price + 0.01
            quantity = trade_usd / limit_price
            if self.confirm_trade("buy", symbol, quantity, trade_usd):
                logging.info(green_text(f"Placing limit buy order for {symbol} at ${limit_price:.2f} (Quantity: {quantity:.8f})"))
                order = self.place_order("buy", symbol, quantity, limit_price=limit_price)
                if order:
                    buy_price = limit_price
                    stop_loss = buy_price * 0.95
                    logging.info(green_text(f"Placing stop loss order for {symbol} at ${stop_loss:.2f}"))
                    stop_order_id = self.place_stop_loss_order("sell", symbol, quantity, stop_loss)
                    self.open_trades[symbol] = (buy_price, quantity, stop_loss, stop_order_id, "active")
                    self.save_open_trades()
                    if self.transaction_callback:
                        self.transaction_callback(f"Bought {symbol} at ${buy_price:.2f} (Qty: {quantity:.8f})")
                    self.monitor_trade(symbol, buy_price, quantity)
            else:
                logging.info(red_text("Buy order canceled by user."))
        else:
            logging.info(red_text(f"Bullish MACD crossover or volume condition not met for {symbol}. No trade executed."))

    def verify_stop_loss_orders(self) -> None:
        """
        Checks active trades and reissues a stop loss order if one is missing.
        """
        for symbol, trade in self.open_trades.items():
            buy_price, quantity, stop_loss, stop_order_id, status = trade
            if status == "active" and stop_order_id is None:
                new_stop_loss = buy_price * 0.95
                logging.info(f"Stop loss missing for {symbol}. Reissuing stop loss order at {new_stop_loss:.2f}")
                new_stop_order_id = self.place_stop_loss_order("sell", symbol, quantity, new_stop_loss)
                if new_stop_order_id:
                    self.open_trades[symbol] = (buy_price, quantity, new_stop_loss, new_stop_order_id, "active")
                    logging.info(f"Reissued stop loss order for {symbol} with order ID: {new_stop_order_id}")
                else:
                    logging.error(f"Failed to reissue stop loss order for {symbol}")
        self.save_open_trades()

    def monitor_trade(self, symbol: str, buy_price: float, quantity: float) -> None:
        logging.info(f"Monitoring {symbol} for exit conditions.")
        while True:
            daily_df = self.get_historical_prices(symbol, "1day")
            if daily_df is None or daily_df.empty or len(daily_df) < 2:
                time.sleep(10)
                continue
            try:
                daily_df["close"] = daily_df["close"].astype(float)
                macd_indicator = ta.trend.MACD(daily_df["close"], window_fast=12, window_slow=26, window_sign=9)
                daily_df["macd"] = macd_indicator.macd()
                daily_df["macd_signal"] = macd_indicator.macd_signal()
                prev_macd = daily_df["macd"].iloc[-2]
                prev_signal = daily_df["macd_signal"].iloc[-2]
                current_macd = daily_df["macd"].iloc[-1]
                current_signal = daily_df["macd_signal"].iloc[-1]
            except Exception as e:
                logging.error(f"Error calculating daily MACD for {symbol}: {e}")
                time.sleep(10)
                continue
            try:
                if len(daily_df) >= 11:
                    avg_vol = daily_df["volume"].iloc[-11:-1].astype(float).mean()
                else:
                    avg_vol = daily_df["volume"].astype(float).mean()
                current_vol = float(daily_df["volume"].iloc[-1])
                vol_cond_daily = current_vol >= avg_vol
            except Exception as e:
                logging.error(f"Error calculating volume condition for {symbol}: {e}")
                vol_cond_daily = False
            logging.info(f"{symbol} Monitoring: prev MACD {prev_macd:.4f} vs prev signal {prev_signal:.4f}; current MACD {current_macd:.4f} vs current signal {current_signal:.4f}; Daily Volume Condition: {vol_cond_daily}")
            ticker = self.get_best_bid_ask(symbol)
            if not ticker:
                time.sleep(10)
                continue
            try:
                current_price = float(ticker["results"][0]["bid_inclusive_of_sell_spread"])
            except Exception as e:
                logging.error(f"Error parsing ticker for {symbol}: {e}")
                time.sleep(10)
                continue
            logging.info(f"{symbol} current sell price: ${current_price:.2f}")
            profit_pct = ((current_price - buy_price) / buy_price) * 100
            if profit_pct >= 10 or (prev_macd >= prev_signal and current_macd < current_signal and vol_cond_daily):
                exit_condition = True
            else:
                exit_condition = False
            if exit_condition:
                total_value = current_price * quantity
                profit_loss = (current_price - buy_price) * quantity
                if symbol in self.open_trades:
                    _, _, _, stop_order_id, _ = self.open_trades[symbol]
                    if stop_order_id:
                        cancel_resp = self.cancel_order(stop_order_id)
                        logging.info(f"Canceled stop loss order for {symbol}: {cancel_resp}")
                if self.confirm_trade("sell", symbol, quantity, total_value, profit_loss):
                    logging.info(f"Executing sell order for {symbol} at ${current_price:.2f}")
                    self.place_order("sell", symbol, quantity)
                    if symbol in self.open_trades:
                        bp, qty, sl, so_id, _ = self.open_trades[symbol]
                        self.open_trades[symbol] = (bp, qty, sl, so_id, "sold")
                        self.save_open_trades()
                    if self.transaction_callback:
                        self.transaction_callback(f"Sold {symbol} at ${current_price:.2f} (Profit: ${profit_loss:.2f})")
                    break
                else:
                    logging.info("Sell order canceled by user.")
                    break
            time.sleep(30)
    def verify_stop_loss_orders(self) -> None:
        """
        For each active trade, check if a stop loss order exists.
        If not, reissue a stop loss order at 5% below the buy price and update the trade.
        """
        for symbol, trade in self.open_trades.items():
            buy_price, quantity, stop_loss, stop_order_id, status = trade
            if status == "active" and stop_order_id is None:
                new_stop_loss = buy_price * 0.95
                logging.info(f"Stop loss missing for {symbol}. Reissuing stop loss order at {new_stop_loss:.2f}")
                new_stop_order_id = self.place_stop_loss_order("sell", symbol, quantity, new_stop_loss)
                if new_stop_order_id:
                    self.open_trades[symbol] = (buy_price, quantity, new_stop_loss, new_stop_order_id, "active")
                    logging.info(f"Reissued stop loss order for {symbol} with order ID: {new_stop_order_id}")
                else:
                    logging.error(f"Failed to reissue stop loss order for {symbol}")
        self.save_open_trades()
    def get_holdings(self) -> Optional[List[Dict[str, Any]]]:
        path = "/api/v1/crypto/trading/holdings/"
        holdings = self.make_api_request("GET", path)
        logging.info(f"Raw holdings response: {holdings}")
        if isinstance(holdings, str):
            try:
                holdings = json.loads(holdings)
            except Exception as e:
                logging.error(f"Error parsing holdings string: {e} - {holdings}")
                return None
        if isinstance(holdings, dict) and "results" in holdings:
            holdings = holdings["results"]
        logging.info(f"Parsed holdings: {holdings}")
        return holdings

    def check_portfolio(self) -> None:
        holdings = self.get_holdings()
        if not holdings:
            logging.info("No holdings found in portfolio.")
            return
        for holding in holdings:
            logging.info(f"Processing holding: {holding}")
            if isinstance(holding, str):
                try:
                    holding = json.loads(holding)
                except Exception as e:
                    logging.error(f"Error parsing holding string: {e} - {holding}")
                    continue
            if not isinstance(holding, dict):
                logging.error(f"Holding is not a dictionary; skipping: {holding}")
                continue
            raw_asset = holding.get("asset_code")
            if raw_asset is None:
                logging.error(f"No asset_code found in holding: {holding}")
                continue
            symbol = raw_asset if "-" in raw_asset else f"{raw_asset}-USD"
            avg_buy = None
            if "average_buy_price" in holding:
                try:
                    avg_buy = float(holding.get("average_buy_price", 0))
                except Exception as e:
                    logging.error(f"Error parsing average_buy_price for {symbol}: {e}")
                    continue
            if avg_buy is None or avg_buy == 0:
                if symbol in self.open_trades and self.open_trades[symbol][4] == "active":
                    avg_buy = self.open_trades[symbol][0]
                else:
                    logging.error(f"No average buy price for {symbol}; skipping.")
                    continue
            try:
                quantity = float(holding.get("total_quantity", 0))
            except Exception as e:
                logging.error(f"Error parsing quantity for {symbol}: {e}")
                continue
            if quantity <= 0 or avg_buy == 0:
                continue
            ticker = self.get_best_bid_ask(symbol)
            if not ticker:
                logging.error(f"Market data not available for {symbol}; skipping holding.")
                continue
            try:
                current_price = float(ticker["results"][0]["bid_inclusive_of_sell_spread"])
            except Exception as e:
                logging.error(f"Error parsing ticker for {symbol}: {e}")
                continue
            profit_pct = ((current_price - avg_buy) / avg_buy) * 100
            total_value = current_price * quantity
            profit_loss = (current_price - avg_buy) * quantity
            _, latest_signal, _ = self.calculate_indicators(symbol, "1day")
            logging.info(f"{symbol} | Avg Buy: ${avg_buy:.2f}, Current Price: ${current_price:.2f}, Profit/Loss: ${profit_loss:.2f} ({profit_pct:.2f}%)")
            if profit_pct >= 10 or (latest_signal is not None and profit_pct <= -5):
                if self.confirm_trade("sell", symbol, quantity, total_value, profit_loss):
                    logging.info(f"Selling {symbol} from portfolio at ${current_price}")
                    self.place_order("sell", symbol, quantity)
                else:
                    logging.info(f"Sell order for {symbol} canceled by user.")
            else:
                logging.info(f"{symbol} profit {profit_pct:.2f}% does not meet sell thresholds.")
def plot_macd_volume(bot, symbol: str, chart_type: str = "1day"):
    """
    Fetches historical data for the given symbol using the specified chart_type,
    calculates MACD (12,26,9) and volume, then plots the MACD, its signal, and volume on a chart.
    """
    df = bot.get_historical_prices(symbol, chart_type)
    if df is None or df.empty:
        from tkinter import messagebox
        messagebox.showerror("Plot Error", f"No historical data for {symbol} ({chart_type})")
        return
    try:
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        macd_indicator = ta.trend.MACD(df["close"], window_fast=12, window_slow=26, window_sign=9)
        df["macd"] = macd_indicator.macd()
        df["macd_signal"] = macd_indicator.macd_signal()
    except Exception as e:
        from tkinter import messagebox
        messagebox.showerror("Plot Error", f"Error calculating indicators for {symbol}: {e}")
        return

    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    ax1.plot(df["time"], df["macd"], label="MACD", marker='o')
    ax1.plot(df["time"], df["macd_signal"], label="Signal", marker='o')
    ax1.set_title(f"{symbol} MACD ({chart_type})")
    ax1.legend()
    ax2.bar(df["time"], df["volume"], label="Volume", color="gray")
    ax2.set_title(f"{symbol} Volume ({chart_type})")
    plt.tight_layout()
    plt.show()

def get_available_usd_products() -> List[str]:
    url = "https://api.robinhood.com/crypto/trading/pairs/"
    try:
        response = requests.get(url)
        if response.status_code != 200:
            logging.error(f"Error fetching products: {response.status_code} - {response.text}")
            fallback_symbols = ["BTC-USD", "ETH-USD", "LTC-USD", "DOGE-USD", "TRUMP-USD", "SOL-USD", "ADA-USD"]
            logging.info(f"Falling back to default symbols: {fallback_symbols}")
            return fallback_symbols
        products = response.json()
        if isinstance(products, dict) and "results" in products:
            products = products["results"]
        symbols = [prod.get("symbol") for prod in products if prod.get("quote_currency") == "USD" and prod.get("symbol")]
        logging.info(f"Found {len(symbols)} USD trading pairs.")
        return symbols
    except Exception as e:
        logging.error(f"Error in get_available_usd_products: {e}")
        return ["BTC-USD", "ETH-USD", "LTC-USD", "DOGE-USD", "TRUMP-USD", "SOL-USD", "ADA-USD"]