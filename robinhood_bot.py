import base64
import datetime
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
import requests
import pandas as pd
import ta
import logging
from typing import Optional
from typing import Any, Dict, Optional, Tuple, List
from nacl.signing import SigningKey
import signal
import tkinter as tk
from tkinter import ttk, messagebox

# Configure logging: logs are printed to the console and saved to a file.
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

## NEW: Helper function to convert chart_type to days and granularity.
def get_chart_parameters(chart_type: str) -> Tuple[int, int]:
    """
    Returns a tuple (days, granularity) based on chart_type.
      - "5min": Use 1 day of 5-minute candles (300 seconds) ~ 288 candles.
      - "15min": Use 2 days of 15-minute candles (900 seconds) ~ 192 candles.
      - "1day": Use 50 days of daily candles (86400 seconds).
    """
    if chart_type == "5min":
        return (1, 300)       # 1 day * 24 * (60/5) = 288 candles
    elif chart_type == "15min":
        return (1, 900)       # 2 days * 24 * (60/15) = 192 candles
    elif chart_type == "1day":
        return (1, 86400)
    else:
        return (1, 86400)    # default

class CryptoAPITrading:
    def __init__(self) -> None:
        """
        Initialize the Robinhood Trading Bot with authentication, a persistent HTTP session,
        and an internal state for open trades.
        open_trades stores: symbol -> (buy_price, quantity, stop_loss, stop_order_id)
        """
        self.api_key = "rh-api-e861379b-6647-4b9a-8568-f0223c785d3d"  # Replace with your API key
        base64_key = "4SEZokUELmIXu/9JUxn7LDVH8Aiw79NtkSgZyVXjdyI="    # Replace with your base64-encoded key
        private_key_seed = base64.b64decode(base64_key)
        self.private_key = SigningKey(private_key_seed)
        self.base_url = "https://trading.robinhood.com"
        self.session = requests.Session()
        self.open_trades: Dict[str, Tuple[float, float, float, Optional[str]]] = {}
        self.load_open_trades()
        logging.info("Initialized CryptoAPITrading client.")

    def load_open_trades(self) -> None:
        filename = "open_trades.json"
        if os.path.exists(filename):
            try:
                with open(filename, "r") as f:
                    data = json.load(f)
                self.open_trades = {k: (float(v[0]), float(v[1]), float(v[2]), v[3] if v[3] != "" else None)
                                     for k, v in data.items()}
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
                data = {k: [v[0], v[1], v[2], v[3] if v[3] is not None else ""] for k, v in self.open_trades.items()}
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
            "x-timestamp": str(timestamp),
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

    # CHANGED: get_historical_prices now accepts a chart_type parameter.
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

    # CHANGED: calculate_indicators now accepts a chart_type parameter.
    def calculate_indicators(self, symbol: str, chart_type: str = "1day") -> Tuple[Optional[float], Optional[float], Optional[bool]]:
        """
        Calculates MACD using historical prices fetched according to chart_type (using MACD parameters 12,26,9)
        and computes a volume condition: whether the current candle's volume is at least 125% of the average volume
        of the previous 10 candles.
        
        Returns:
          - Latest MACD value,
          - Latest MACD signal,
          - Volume condition (True if current volume >= 125% of the 10-day average, else False).
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
            # Compute volume condition using the volume column.
            # Ensure we have at least 11 candles (last candle + previous 10).
            if len(df) >= 11:
                avg_volume = df["volume"].iloc[-11:-1].astype(float).mean()
            else:
                avg_volume = df["volume"].astype(float).mean()
            current_volume = float(df["volume"].iloc[-1])
            volume_condition = current_volume >= 1.25 * avg_volume
            logging.info(f"Indicators for {symbol} ({chart_type}) | MACD: {latest_macd:.4f}, Signal: {latest_signal:.4f}, Volume Condition: {volume_condition} (Current: {current_volume}, Avg10: {avg_volume:.2f})")
            return latest_macd, latest_signal, volume_condition
        except Exception as e:
            logging.error(f"Error calculating indicators for {symbol}: {e}")
            return None, None, None

    def confirm_trade(self, action: str, symbol: str, quantity: float, total_usd: float, profit_loss: Optional[float] = None) -> bool:
        """
        This method will be overridden by the GUI version.
        """
        # Fallback text-based confirmation.
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

    def place_order(self, side: str, symbol: str, amount: float) -> Optional[Any]:
        asset_quantity_str = self.format_quantity(amount)
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

    ## NEW: Place a formal stop loss order.
    def place_stop_loss_order(self, side: str, symbol: str, amount: float, stop_price: float) -> Optional[str]:
        asset_quantity_str = self.format_quantity(amount)
        stop_price_str = f"{stop_price:.2f}"
        body = {
            "client_order_id": str(uuid.uuid4()),
            "side": side,
            "type": "stop",
            "symbol": symbol,
            "stop_order_config": {
                "asset_quantity": asset_quantity_str,
                "stop_price": stop_price_str
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

    ## NEW: Cancel an order by its ID.
    def cancel_order(self, order_id: str) -> Optional[Any]:
        path = f"/api/v1/crypto/trading/orders/{order_id}/cancel/"
        return self.make_api_request("POST", path)

    def trade_crypto(self, symbol: str, chart_type: str = "1day") -> None:
        buying_power = self.get_buying_power()
        if buying_power < 10:
            logging.error(f"Not enough buying power for trading: ${buying_power}")
            return
        ticker = self.get_best_bid_ask(symbol)
        if not ticker:
            logging.error(f"Unable to fetch ticker for {symbol}")
            return
        try:
            current_price = float(ticker["results"][0]["ask_inclusive_of_buy_spread"])
        except Exception as e:
            logging.error(f"Error parsing ask price for {symbol}: {e}")
            return
        macd, macd_signal, vol_cond = self.calculate_indicators(symbol, chart_type)
        if macd is None or macd_signal is None or vol_cond is None:
            logging.error(f"Could not fetch complete indicators for {symbol}")
            return
        logging.info(f"{symbol} ({chart_type}) | MACD: {macd:.4f}, Signal: {macd_signal:.4f}, Volume Condition: {vol_cond}, Price: ${current_price}")
        # Buy condition: bullish MACD crossover AND volume condition met.
        if macd > macd_signal and vol_cond:
            logging.info(f"\033[92mBullish condition met for {symbol}.\033[0m")
            trade_usd = min(buying_power, 50)
            quantity = trade_usd / current_price
            if self.confirm_trade("buy", symbol, quantity, trade_usd):
                logging.info(f"\033[92mBuying {symbol} at ${current_price} using ${trade_usd} (Quantity: {quantity:.8f})\033[0m")
                order = self.place_order("buy", symbol, quantity)
                if order:
                    # For stop loss we can keep the old logic or remove it; here, we'll remove stop loss orders for simplicity.
                    self.open_trades[symbol] = (current_price, quantity, None, None)
                    self.save_open_trades()
                    # For exit, we will monitor with a separate method.
                    self.monitor_trade(symbol, current_price, quantity)
            else:
                logging.info("\033[91mBuy order canceled by user.\033[0m")
        else:
            logging.info("\033[91mBullish MACD crossover or volume condition not met for {0}. No trade executed.\033[0m".format(symbol))

    def monitor_trade(self, symbol: str, buy_price: float, quantity: float) -> None:
        logging.info(f"Monitoring {symbol} for exit using bearish MACD crossover.")
        while True:
            # For exit, we use daily data.
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
            # Calculate daily volume condition
            try:
                if len(daily_df) >= 11:
                    avg_vol = daily_df["volume"].iloc[-11:-1].astype(float).mean()
                else:
                    avg_vol = daily_df["volume"].astype(float).mean()
                current_vol = float(daily_df["volume"].iloc[-1])
                vol_cond_daily = current_vol >= 1.25 * avg_vol
            except Exception as e:
                logging.error(f"Error calculating volume condition for {symbol}: {e}")
                vol_cond_daily = False
            logging.info(f"{symbol} Monitoring: prev MACD {prev_macd:.4f} vs prev signal {prev_signal:.4f}; current MACD {current_macd:.4f} vs current signal {current_signal:.4f}; Volume condition: {vol_cond_daily}")
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
            logging.info(f"{symbol} current price: ${current_price:.2f}")
            # Exit condition: bearish crossover and volume condition met.
            if prev_macd >= prev_signal and current_macd < current_signal and vol_cond_daily:
                exit_condition = True
            else:
                exit_condition = False

            if exit_condition:
                total_value = current_price * quantity
                profit_loss = (current_price - buy_price) * quantity
                if self.confirm_trade("sell", symbol, quantity, total_value, profit_loss):
                    logging.info(f"Executing sell order for {symbol} at ${current_price:.2f}")
                    self.place_order("sell", symbol, quantity)
                    if symbol in self.open_trades:
                        del self.open_trades[symbol]
                        self.save_open_trades()
                    break
                else:
                    logging.info("Sell order canceled by user.")
                    break
            time.sleep(30)

    def cancel_order(self, order_id: str) -> Optional[Any]:
        path = f"/api/v1/crypto/trading/orders/{order_id}/cancel/"
        return self.make_api_request("POST", path)

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
            logging.info(f"Processing holding (type {type(holding)}): {holding}")
            if isinstance(holding, str):
                try:
                    holding = json.loads(holding)
                    logging.info(f"Parsed holding into dict: {holding}")
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
                if symbol in self.open_trades:
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
            df = self.calculate_indicators(symbol, "1day")
            if df is not None and len(df) > 0:
                latest_macd = df["macd"].iloc[-1]
                latest_signal = df["macd_signal"].iloc[-1]
                logging.info(f"{symbol} | Latest MACD: {latest_macd:.4f}, Signal: {latest_signal:.4f}")
            logging.info(f"Selling price for {symbol}: ${current_price:.2f}, Total Value: ${total_value:.2f}, Profit/Loss: ${profit_loss:.2f} ({profit_pct:.2f}%)")
            if profit_pct >= 5 or profit_pct <= -10:
                if self.confirm_trade("sell", symbol, quantity, total_value, profit_loss):
                    logging.info(f"Selling {symbol} from portfolio at ${current_price}")
                    self.place_order("sell", symbol, quantity)
                else:
                    logging.info(f"Sell order for {symbol} canceled by user.")
            else:
                logging.info(f"{symbol} profit {profit_pct:.2f}% does not meet sell thresholds.")

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

## GUI Section
class TradingBotGUI(tk.Tk):
    def __init__(self, bot: CryptoAPITrading):
        super().__init__()
        self.bot = bot
        self.title("Trading Bot GUI")
        self.geometry("800x600")
        self.chart_type_var = tk.StringVar(value="5min")
        self.running = False
        self.trading_thread = None

        # Controls
        control_frame = tk.Frame(self)
        control_frame.pack(pady=10)

        tk.Label(control_frame, text="Select Chart Type:").grid(row=0, column=0, padx=5)
        self.chart_type_menu = ttk.Combobox(control_frame, textvariable=self.chart_type_var, values=["5min", "15min", "1day"])
        self.chart_type_menu.grid(row=0, column=1, padx=5)

        self.start_button = tk.Button(control_frame, text="Start Trading", command=self.start_trading)
        self.start_button.grid(row=0, column=2, padx=5)
        self.stop_button = tk.Button(control_frame, text="Stop Trading", command=self.stop_trading)
        self.stop_button.grid(row=0, column=3, padx=5)

        # Log display
        self.log_text = tk.Text(self, state='disabled', height=25)
        self.log_text.pack(fill=tk.BOTH, padx=10, pady=10)

    def log(self, message: str):
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def start_trading(self):
        if not self.running:
            self.running = True
            self.trading_thread = threading.Thread(target=self.run_trading_loop, daemon=True)
            self.trading_thread.start()
            self.log("Trading started.")

    def stop_trading(self):
        self.running = False
        self.log("Trading stopped.")

    def run_trading_loop(self):
        chart_type = self.chart_type_var.get()
        symbols = get_available_usd_products()
        filtered_symbols = [sym for sym in symbols if sym in {"BTC-USD", "ETH-USD", "LTC-USD", "DOGE-USD", "TRUMP-USD", "SOL-USD", "ADA-USD"}]
        while self.running:
            for symbol in filtered_symbols:
                if not self.running:
                    break
                self.log(f"Checking {symbol} for buy conditions using {chart_type} chart...")
                self.bot.trade_crypto(symbol, chart_type)
            self.bot.check_portfolio()
            self.log("Cycle complete. Waiting 30 seconds...")
            for _ in range(30):
                if not self.running:
                    break
                time.sleep(1)

def gui_confirm_trade(action: str, symbol: str, quantity: float, total_usd: float, profit_loss: Optional[float] = None, indicator_info: str = "") -> bool:
    result = {"confirmed": False}
    win = tk.Toplevel()
    win.title(f"Confirm {action.capitalize()} for {symbol}")
    info = f"Action: {action.upper()} for {symbol}\nQuantity: {quantity:.8f}\nTotal USD: ${total_usd:.2f}\n"
    if profit_loss is not None:
        info += f"Profit/Loss: ${profit_loss:.2f}\n"
    if indicator_info:
        info += f"{indicator_info}\n"
    tk.Label(win, text=info, padx=10, pady=10).pack()
    def on_confirm():
        result["confirmed"] = True
        win.destroy()
    def on_cancel():
        win.destroy()
    btn_frame = tk.Frame(win)
    btn_frame.pack(pady=10)
    tk.Button(btn_frame, text="Confirm", command=on_confirm).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side="right", padx=5)
    win.grab_set()
    win.wait_window()
    return result["confirmed"]

def main():
    bot = CryptoAPITrading()
    # Override bot's confirm_trade with GUI version that shows indicator values.
    # For demonstration, we pass a dummy indicator_info; you can customize further.
    bot.confirm_trade = lambda a, s, q, t, p=None: gui_confirm_trade(a, s, q, t, p, indicator_info="(Indicator details go here)")
    app = TradingBotGUI(bot)
    app.mainloop()

if __name__ == "__main__":
    main()