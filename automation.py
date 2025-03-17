#!/usr/bin/env python3
"""
gui.py

A Tkinter GUI to control the trading bot.
Features:
  - Chart type drop-down ("5min", "15min", "1day")
  - Symbol drop-down for charting.
  - Start Trading, Stop Trading, and Exit buttons.
  - A log panel (left) and a recent transactions list (right).
  - A "Show Chart" button on the main screen.
  - Confirmation popups with three buttons: Buy, Cancel, and Show Chart.
"""

import threading
import time
from typing import Optional
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from automation_test import CryptoAPITrading, get_available_usd_products, plot_macd_volume
import matplotlib.pyplot as plt

def gui_confirm_trade(action: str, symbol: str, quantity: float, total_usd: float, profit_loss: Optional[float] = None) -> bool:
    profit_str = f"{profit_loss:.2f}" if profit_loss is not None else "N/A"
    result = {"confirmed": False}
    win = tk.Toplevel()
    win.title(f"Confirm {action.capitalize()} for {symbol}")
    info = f"Action: {action.upper()} for {symbol}?\nQuantity: {quantity:.8f}\nTotal USD: ${total_usd:.2f}\nProfit/Loss: ${profit_str}"
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
        # Show chart using 5min data
        plot_macd_volume(bot, symbol, "5min")
    
    tk.Button(btn_frame, text="Buy", command=on_confirm).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Show Chart", command=on_show_chart).pack(side="left", padx=5)
    
    win.grab_set()
    win.wait_window()
    return result["confirmed"]

class TradingBotGUI(tk.Tk):
    def __init__(self, bot: CryptoAPITrading):
        super().__init__()
        self.bot = bot
        self.title("Trading Bot GUI")
        self.geometry("900x600")
        self.chart_type_var = tk.StringVar(value="5min")
        self.symbol_var = tk.StringVar(value="BTC-USD")
        self.running = False
        self.trading_thread = None

        # Main frame with left (log) and right (transactions) panels.
        main_frame = tk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)

        left_frame = tk.Frame(main_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        right_frame = tk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, padx=10, pady=10)

        # Left Frame Controls
        control_frame = tk.Frame(left_frame)
        control_frame.pack(pady=10)

        tk.Label(control_frame, text="Select Chart Type:").grid(row=0, column=0, padx=5)
        self.chart_type_menu = ttk.Combobox(control_frame, textvariable=self.chart_type_var, values=["5min", "15min", "1day"])
        self.chart_type_menu.grid(row=0, column=1, padx=5)

        tk.Label(control_frame, text="Select Symbol for Chart:").grid(row=0, column=2, padx=5)
        available_symbols = get_available_usd_products()
        self.symbol_var.set(available_symbols[0] if available_symbols else "BTC-USD")
        self.symbol_menu = ttk.Combobox(control_frame, textvariable=self.symbol_var, values=available_symbols)
        self.symbol_menu.grid(row=0, column=3, padx=5)

        self.start_button = tk.Button(control_frame, text="Start Trading", command=self.start_trading)
        self.start_button.grid(row=0, column=4, padx=5)
        self.stop_button = tk.Button(control_frame, text="Stop Trading", command=self.stop_trading)
        self.stop_button.grid(row=0, column=5, padx=5)
        self.exit_button = tk.Button(control_frame, text="Exit", command=self.destroy)
        self.exit_button.grid(row=0, column=6, padx=5)
        self.show_chart_button = tk.Button(control_frame, text="Show Chart", command=self.show_chart)
        self.show_chart_button.grid(row=0, column=7, padx=5)

        # Left Frame Log Display
        self.log_text = tk.Text(left_frame, state='disabled', height=25)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Right Frame: Recent Transactions
        tk.Label(right_frame, text="Recent Transactions:").pack(pady=5)
        self.trans_listbox = tk.Listbox(right_frame, height=25)
        self.trans_listbox.pack(fill=tk.BOTH, expand=True)

    def log(self, message: str):
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def add_transaction(self, message: str):
        self.trans_listbox.insert(tk.END, message)
        self.trans_listbox.yview(tk.END)

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
        filtered_symbols = [sym for sym in symbols if sym in {"BTC-USD", "ETH-USD", "LTC-USD", "DOGE-USD", "TRUMP-USD"}]
        while self.running:
            for symbol in filtered_symbols:
                if not self.running:
                    break
                self.log(f"Scanning {symbol} for buy conditions using {chart_type} chart...")
                self.bot.trade_crypto(symbol, chart_type)
            self.bot.check_portfolio()
            self.log("Cycle complete. Waiting 30 seconds...")
            for _ in range(30):
                if not self.running:
                    break
                time.sleep(1)

    def show_chart(self):
        symbol = self.symbol_var.get()
        chart_type = self.chart_type_var.get()
        self.log(f"Plotting chart for {symbol} with {chart_type} data...")
        plot_macd_volume(self.bot, symbol, chart_type)

def main():
    bot = CryptoAPITrading()
    # Override confirm_trade with the GUI version.
    bot.verify_stop_loss_orders()
    bot.confirm_trade = lambda a, s, q, t, p=None: gui_confirm_trade(a, s, q, t, p)
    # Set the transaction callback so that trades update the transactions list.
    def transaction_callback(message: str):
        app.add_transaction(message)
    bot.transaction_callback = transaction_callback
    app = TradingBotGUI(bot)
    app.mainloop()

if __name__ == "__main__":
    main()