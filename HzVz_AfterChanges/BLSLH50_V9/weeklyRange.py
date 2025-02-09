from strategyTools.tools import OHLCDataFetch, resample_data, get_candle_data
from datetime import datetime, time
import talib
import pandas as pd

stock = "RELIANCE"
startDateTime = datetime.now().timestamp()
df = get_candle_data(stock, startDateTime-(86400*2000), "d")

df['date'] = pd.to_datetime(df['date'])
df.set_index('date', inplace=True)

df = df.resample('W-FRI').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum',
    }).reset_index()

df['Symbol'] = stock
df['rsi'] = talib.RSI(df['Close'], timeperiod=14)
df.dropna(inplace=True)
print(df)
df.to_csv("main.csv")