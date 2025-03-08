# gui.py
import threading
import time
from typing import Optional
import tkinter as tk
from tkinter import ttk, messagebox
from robinhood_bot import CryptoAPITrading, get_available_usd_products  # Import your bot and any helper functions
import ta
import matplotlib.pyplot as plt

class TradingBotGUI(tk.Tk):
    def __init__(self, bot: CryptoAPITrading):
        super().__init__()
        self.bot = bot
        self.title("Trading Bot GUI")
        self.geometry("600x400")
        self.chart_type_var = tk.StringVar(value="5min")
        self.running = False
        self.trading_thread = None

        label = tk.Label(self, text="Select Chart Type:")
        label.pack(pady=5)
        self.chart_type_menu = ttk.Combobox(self, textvariable=self.chart_type_var, values=["5min", "15min", "1day"])
        self.chart_type_menu.pack(pady=5)

        self.start_button = tk.Button(self, text="Start Trading", command=self.start_trading)
        self.start_button.pack(pady=10)
        self.stop_button = tk.Button(self, text="Stop Trading", command=self.stop_trading)
        self.stop_button.pack(pady=10)

        self.log_text = tk.Text(self, state='disabled', height=10)
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
        # Optionally filter symbols for testing:
        filtered_symbols = [sym for sym in symbols if sym in {"BTC-USD", "ETH-USD", "LTC-USD", "DOGE-USD", "TRUMP-USD"}]
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

def plot_macd_volume(bot, symbol: str, chart_type: str = "1day"):
    """
    Fetches historical data for the given symbol using the specified chart_type,
    calculates MACD (12,26,9) and plots the MACD, its signal, and volume on a chart.
    """
    df = bot.get_historical_prices(symbol, chart_type)
    if df is None or df.empty:
        messagebox.showerror("Plot Error", f"No historical data for {symbol}")
        return
    try:
        df["close"] = df["close"].astype(float)
        macd_indicator = ta.trend.MACD(df["close"], window_fast=12, window_slow=26, window_sign=9)
        df["macd"] = macd_indicator.macd()
        df["macd_signal"] = macd_indicator.macd_signal()
        df["volume"] = df["volume"].astype(float)
    except Exception as e:
        messagebox.showerror("Plot Error", f"Error calculating indicators for {symbol}: {e}")
        return
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    ax1.plot(df["time"], df["macd"], label="MACD")
    ax1.plot(df["time"], df["macd_signal"], label="Signal")
    ax1.set_title(f"{symbol} MACD ({chart_type})")
    ax1.legend()
    ax2.bar(df["time"], df["volume"], label="Volume", color="gray")
    ax2.set_title(f"{symbol} Volume ({chart_type})")
    plt.tight_layout()
    plt.show()

def gui_confirm_trade(action: str, symbol: str, quantity: float, total_usd: float, profit_loss: Optional[float] = None, indicator_info: str = "", plot_callback: Optional[callable] = None) -> bool:
    """
    Displays a confirmation popup with trade details and includes a "Show Chart" button if a plot_callback is provided.
    """
    profit_loss_str = f"{profit_loss:.2f}" if profit_loss is not None else "N/A"
    result = {"confirmed": False}
    win = tk.Toplevel()
    win.title(f"Confirm {action.capitalize()} for {symbol}")
    info = f"Action: {action.upper()} for {symbol}?\nQuantity: {quantity:.8f}\nTotal USD: ${total_usd:.2f}\nProfit/Loss: ${profit_loss_str}\n{indicator_info}"
    lbl = tk.Label(win, text=info, padx=10, pady=10)
    lbl.pack()

    btn_frame = tk.Frame(win)
    btn_frame.pack(pady=10)
    
    def on_confirm():
        result["confirmed"] = True
        win.destroy()
    
    def on_cancel():
        win.destroy()
    
    def on_show_chart():
        if plot_callback:
            plot_callback()  # call the provided plot callback function

    tk.Button(btn_frame, text="Confirm", command=on_confirm).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side="left", padx=5)
    # NEW: Add Show Chart button if a plot_callback is provided.
    if plot_callback:
        tk.Button(btn_frame, text="Show Chart", command=on_show_chart).pack(side="left", padx=5)
    
    win.grab_set()
    win.wait_window()
    return result["confirmed"]

def main():
    bot = CryptoAPITrading()
    # Override the bot's confirm_trade with a GUI version:
    bot.confirm_trade = lambda a, s, q, t, p=None: gui_confirm_trade(
    a, s, q, t, p,
    indicator_info="MACD indicators are favorable.",
    plot_callback=lambda: plot_macd_volume(bot, s, "1day")
)
    app = TradingBotGUI(bot)
    app.mainloop()

if __name__ == "__main__":
    main()