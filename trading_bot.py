import pandas as pd
import matplotlib.pyplot as plt

# Fake data for 30 days
dates = pd.date_range(end=pd.Timestamp.now(), periods=30, freq="D")
df_test = pd.DataFrame({
    "time": dates,
    "close": range(30),
    "macd": range(30),
    "macd_signal": [x + 1 for x in range(30)]
})

fig, ax = plt.subplots()
ax.plot(df_test["time"], df_test["macd"], label="MACD")
ax.plot(df_test["time"], df_test["macd_signal"], label="Signal")
ax.legend()
plt.show()