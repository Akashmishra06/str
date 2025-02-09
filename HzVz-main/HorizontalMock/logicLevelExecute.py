from strategyTools.infra import getBuyLimitPrice, getSellLimitPrice, postOrderToDbLIMIT
from strategyTools.statusUpdater import infoMessage, errorMessage, positionUpdator
from pandas.api.types import is_datetime64_any_dtype
from configparser import ConfigParser
import os
import talib
import logging
import threading
import numpy as np
import pandas as pd
from time import sleep
from datetime import datetime, time
from strategyTools.tools import OHLCDataFetch, resample_data
from strategyTools.SOTools import option_health
from strategyTools import dataFetcher, reconnect
import json


def updateCurrentPrices(self1):
    currentDatetime = datetime.now()
    currentTime = currentDatetime.time()

    for stock, stock_data in self1.stockDict.items():
        try:
            try:
                df_1d, candle_flag_1d, last_candle_time_1d = OHLCDataFetch(stock, currentDatetime.timestamp(), self1.candle_1d[stock]['last_candle_time'], 'd',
                    150, self1.candle_1d[stock]['df'], self1.stockDict[stock].stockLogger)
                self1.candle_1d[stock]['df'] = df_1d
                self1.candle_1d[stock]['last_candle_time'] = last_candle_time_1d
                resample_data(df_1d, 'd')
                self1.rename_col(df_1d)
            except Exception as e:
                self1.stockDict[stock].stockLogger.error(f"Error fetching daily OHLC data for {stock}: {e}")
                continue

            if df_1d is None or df_1d.empty:
                raise ValueError(f"Empty or invalid dataframe fetched for {stock}")

            prev_day_close = df_1d.iloc[-2]['c'] if len(df_1d) > 1 else df_1d.iloc[-1]['c']
            self1.stockDict[stock].prev_day_close = prev_day_close
            stock_data.stockLogger.info(f'Updated prev_day_close for {stock}: {prev_day_close}')

        except Exception as e:
            self1.stockDict[stock].stockLogger.error(f"Error fetching daily OHLC data for {stock}: {e}")
            continue

        sleep(0.1)

        try:
            if stock_data.openPnl is not None and not stock_data.openPnl.empty:
                currentPrice = dataFetcher([self1.idMap[stock]])[self1.idMap[stock]]
                stock_data.stockLogger.info(f'[Tick] => Current Price for {stock}: {currentPrice}')

                for index, row in stock_data.openPnl.iterrows():
                    try:
                        stock_data.openPnl.at[index, "CurrentPrice"] = currentPrice
                        stock_data.openPnl.at[index, "prev_day_c"] = self1.stockDict[stock].prev_day_close
                        stock_data.openPnl.at[index, "sum"] = (currentPrice - self1.stockDict[stock].prev_day_close) * row['Quantity']
                    except Exception as e:
                        stock_data.stockLogger.error(f"Error updating PnL for {stock} at index {index}: {e}")
                        continue

                stock_data.pnlCalculator()

        except Exception as e:
            self1.stockDict[stock].stockLogger.error(f"Error updating prices for {stock}: {e}")
            continue

def algoInfoMessage():

    df_openPositions = combineOpenPnlCSV()
    df_colosedPositions = combineClosePnlCSV()

    if df_openPositions is not None and len(df_openPositions) > 0:

        df_openPositions['investedAmount'] = (df_openPositions['EntryPrice'] * df_openPositions['Quantity'])
        totalInvestedAmount = df_openPositions['investedAmount'].sum()
        totalInvestedAmountPercentage = ((totalInvestedAmount * 100) // 1500000)

        df_openPositions['currentAmount'] = (df_openPositions['CurrentPrice'] * df_openPositions['Quantity'])
        totalCurrentAmount = df_openPositions['currentAmount'].sum()
        totalCurrentAmountPercentage = ((totalCurrentAmount * 100) // 1500000)

        netPnl = (totalCurrentAmount - totalInvestedAmount)
        netPnlPercentage = ((netPnl * 100) // totalInvestedAmount)

        df_openPositions['mtm'] = ((df_openPositions['CurrentPrice'] - df_openPositions['prev_day_c']) * df_openPositions['Quantity'])
        mtm = df_openPositions['mtm'].sum()
        mtmPercentage = ((mtm * 100) // 1500000)

    else:
        totalInvestedAmount, totalInvestedAmountPercentage, totalCurrentAmount, totalCurrentAmountPercentage, netPnl, netPnlPercentage, mtm, mtmPercentage = 0, 0, 0, 0, 0, 0, 0, 0

    if df_colosedPositions is not None and len(df_colosedPositions) > 0:

        realisedPnl = df_colosedPositions['Pnl'].sum()
        realisedPnlPercentage = ((realisedPnl * 100) // 1500000)

    else:
        realisedPnl, realisedPnlPercentage = 0, 0

    return totalInvestedAmount, totalInvestedAmountPercentage, totalCurrentAmount, totalCurrentAmountPercentage, netPnl, netPnlPercentage, mtm, mtmPercentage, realisedPnl, realisedPnlPercentage

def setup_and_append(logFileFolder, values_to_append):
    if not os.path.exists(logFileFolder):
        os.makedirs(logFileFolder)
    file_path = os.path.join(logFileFolder, "DataNotFind.txt")
    with open(file_path, 'a') as file:
        file.write(values_to_append + '\n')

def algoLoggerSetup(algoName):
    logFileFolder = f'/root/liveAlgos/algoLogs/{algoName}'
    try:
        if not os.path.exists(logFileFolder):
            os.makedirs(logFileFolder)
    except Exception as e:
        print(e)
    
    jsonFileFolder = f'/root/liveAlgos/algoJson/{algoName}'
    try:
        if not os.path.exists(jsonFileFolder):
            os.makedirs(jsonFileFolder)
    except Exception as e:
        print(e)
    return logFileFolder,jsonFileFolder

def setup_logger(name, log_file, level=logging.INFO):
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    handler = logging.FileHandler(log_file)
    handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(handler)
    logging.basicConfig(level=level, filemode='a', force=True)
    return logger

def createPortfolioList(file_path):
    with open(file_path, 'r') as file:
        stocks = [line.strip() for line in file if line.strip()]
    return stocks

def createSubPortfoliosList(stock_list, num_batches):
    batch_size = len(stock_list) // num_batches
    remainder = len(stock_list) % num_batches

    batches = []
    start = 0
    for i in range(num_batches):
        end = start + batch_size + (1 if i < remainder else 0)
        batches.append(stock_list[start:end])
        start = end
    return batches

def combineClosePnlCSV():
    closeCsvDir = fileDir["closedPositions"]
    if not os.listdir(closeCsvDir):
        return
    csvFiles = [file for file in os.listdir(closeCsvDir) if file.endswith(".csv")]
    closedPnl = pd.concat([pd.read_csv(os.path.join(closeCsvDir, file)) for file in csvFiles])
    if closedPnl.empty:
        return None
    if not is_datetime64_any_dtype(closedPnl["Key"]):
        closedPnl["Key"] = pd.to_datetime(closedPnl["Key"])
    if not is_datetime64_any_dtype(closedPnl["ExitTime"]):
        closedPnl["ExitTime"] = pd.to_datetime(closedPnl["ExitTime"])
    if "Unnamed: 0" in closedPnl.columns:
        closedPnl.drop(columns=["Unnamed: 0"], inplace=True)

    closedPnl.sort_values(by=["Key"], inplace=True)
    closedPnl.reset_index(inplace=True, drop=True)

    closedPnl.to_csv(f"{fileDir['baseJson']}/closePnl.csv", index=False)
    return closedPnl

def create_dir_if_not_exists(dir_path):
    """Helper function to create directories if they do not exist."""
    if not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)

def readJson(key=None):
    file_path = f"{fileDir['jsonValue']}/data.json"
    """Reads the single JSON file and returns data or a specific key."""
    create_dir_if_not_exists(os.path.dirname(file_path))

    if not os.path.exists(file_path):
        initial_data = {
            'ProfitAmount': 0,
            'TotalTradeCanCome': 50
        }
        with open(file_path, 'w') as json_file:
            json.dump(initial_data, json_file, indent=4)
        return initial_data

    try:
        with open(file_path, 'r') as json_file:
            jsonDict = json.load(json_file)
        if key:
            return jsonDict.get(key, 50)
        return jsonDict
    except (json.JSONDecodeError, IOError):
        return {}


def writeJson1(key, value):
    file_path = f"{fileDir['jsonValue']}/data.json"
    jsonDict = readJson()
    if key in jsonDict:
        print(f"Key '{key}' already exists in the JSON file. Skipping write.")
        return
    jsonDict[key] = value
    with open(file_path, 'w') as json_file:
        json.dump(jsonDict, json_file, indent=4)
        print(f"Key '{key}' added successfully.")

def writeJson(key, value):

    file_path = f"{fileDir['jsonValue']}/data.json"
    jsonDict = readJson()
    jsonDict[key] = value
    with open(file_path, 'w') as json_file:
        json.dump(jsonDict, json_file, indent=4)


def combineOpenPnlCSV():
    openCsvDir = fileDir["openPositions"]
    if not os.listdir(openCsvDir): return pd.DataFrame()
    csvFiles = [file for file in os.listdir(openCsvDir) if file.endswith(".csv")]
    if not csvFiles: return pd.DataFrame()
    data_frames = []
    for file in csvFiles:
        file_path = os.path.join(openCsvDir, file)
        if os.stat(file_path).st_size == 0:
            print(f"Skipping empty file: {file_path}")
            continue
        try:
            df = pd.read_csv(file_path)
            if df.empty:
                print(f"Warning: File {file_path} is empty.")
                continue
            data_frames.append(df)
        except pd.errors.EmptyDataError:
            print(f"Error: No columns in {file_path}")
        except Exception as e:
            print(f"Error reading {file_path}: {str(e)}")
    if not data_frames: return pd.DataFrame()
    openPnl = pd.concat(data_frames, ignore_index=True)
    if "EntryTime" in openPnl.columns and not is_datetime64_any_dtype(openPnl["EntryTime"]):
        openPnl["EntryTime"] = pd.to_datetime(openPnl["EntryTime"], errors="coerce")
    if "Unnamed: 0" in openPnl.columns:
        openPnl.drop(columns=["Unnamed: 0"], inplace=True)
    openPnl.sort_values(by=["EntryTime"], inplace=True)
    openPnl.reset_index(inplace=True, drop=True)
    openPnl.to_csv(f"{fileDir['baseJson']}/openPnl.csv", index=False)
    return openPnl

class Stock:
    def __init__(self, stockName):
        self.stockName = stockName

        self.openPnl = pd.DataFrame(columns=["EntryTime", "Symbol", "EntryPrice", "CurrentPrice", "Quantity", "PositionStatus", "Pnl"])
        self.closedPnl = pd.DataFrame(columns=["Key", "ExitTime", "Symbol", "EntryPrice", "ExitPrice", "Quantity", "PositionStatus", "Pnl", "ExitType"])

        stockLogDir = f"{fileDir['stockLogs']}/{self.stockName}"
        os.makedirs(stockLogDir, exist_ok=True)
        self.stockLogger = setup_logger(self.stockName, f"{stockLogDir}/log_{datetime.now().replace(microsecond=0)}.log")
        self.stockLogger.propagate = False

        self.readOpenPnlCsv()
        self.readClosePnlCsv()

        self.data_not_available = 0
        self.realizedPnl = 0
        self.unrealizedPnl = 0
        self.netPnl = 0
        self.sum = 0

    def readOpenPnlCsv(self):
        openPnlCsvFilePath = f"{fileDir['openPositions']}/{self.stockName}_openPositions.csv"

        if os.path.exists(openPnlCsvFilePath):
            openPnlCsvDf = pd.read_csv(openPnlCsvFilePath)

            if 'Unnamed: 0' in openPnlCsvDf.columns:
                openPnlCsvDf.drop(columns=['Unnamed: 0'], inplace=True)

            self.openPnl = pd.concat([self.openPnl, openPnlCsvDf])

            if not is_datetime64_any_dtype(self.openPnl["EntryTime"]):
                self.openPnl["EntryTime"] = pd.to_datetime(self.openPnl["EntryTime"])

            self.stockLogger.info(f"OpenPnl CSV read successfully.")
        else:
            self.stockLogger.info(f"OpenPnl CSV not found.")

    def writeOpenPnlCsv(self):
        self.openPnl.to_csv(f"{fileDir['openPositions']}/{self.stockName}_openPositions.csv")

    def readClosePnlCsv(self):
        closePnlCsvFilePath = f"{fileDir['closedPositions']}/{self.stockName}_closedPositions.csv"

        if os.path.exists(closePnlCsvFilePath):
            closePnlCsvDf = pd.read_csv(closePnlCsvFilePath)

            if 'Unnamed: 0' in closePnlCsvDf.columns:
                closePnlCsvDf.drop(columns=['Unnamed: 0'], inplace=True)

            self.closedPnl = pd.concat([self.closedPnl, closePnlCsvDf])

            if not is_datetime64_any_dtype(self.closedPnl["Key"]):
                self.closedPnl["Key"] = pd.to_datetime(self.closedPnl["Key"])
            if not is_datetime64_any_dtype(self.closedPnl["ExitTime"]):
                self.closedPnl["ExitTime"] = pd.to_datetime(
                    self.closedPnl["ExitTime"])

            self.stockLogger.info(f"ClosedPnl CSV read successfully.")
        else:
            self.stockLogger.info(f"ClosedPnl CSV not found.")

    def writeClosePnlCsv(self):
        self.closedPnl.to_csv(f"{fileDir['closedPositions']}/{self.stockName}_closedPositions.csv")

    def entryOrder(self, instrumentID, symbol, entryPrice, quantity, orderSide, extraCols=None):
        if orderSide == "BUY":
            limitPrice = getBuyLimitPrice(entryPrice, float(config.get('inputParameters', 'extraPercent')))
        else:
            limitPrice = getSellLimitPrice(entryPrice, float(config.get('inputParameters', 'extraPercent')))

        postOrderToDbLIMIT(exchangeSegment="NSECM",
            # productType='NRML',
            algoName=algoName,
            isLive=True if config.get('inputParameters', 'islive') == "True" else False,
            exchangeInstrumentID=instrumentID,
            orderSide=orderSide,
            orderQuantity=quantity,
            limitPrice=limitPrice,
            upperPriceLimit=(float(config.get('inputParameters', 'upperPriceLimitPercent')) * limitPrice) if orderSide == "BUY" else 0,
            lowerPriceLimit=0 if orderSide == "BUY" else (float(config.get('inputParameters', 'lowerPriceLimitPercent')) * limitPrice),
            timePeriod=int(config.get('inputParameters', 'timeLimitOrder')),
            extraPercent=float(config.get('inputParameters', 'extraPercent')),
        )

        newTrade = pd.DataFrame({
            "EntryTime": datetime.now(),
            "Symbol": symbol,
            "EntryPrice": entryPrice,
            "CurrentPrice": entryPrice,
            "Quantity": quantity,
            "PositionStatus": 1 if orderSide == "BUY" else -1,
            "Pnl": 0
        }, index=[0])

        if extraCols:
            for key in extraCols.keys():
                newTrade[key] = extraCols[key]

        self.openPnl = pd.concat([self.openPnl, newTrade], ignore_index=True)
        self.openPnl.reset_index(inplace=True, drop=True)

        self.writeOpenPnlCsv()
        self.stockLogger.info(f'ENTRY {orderSide}: {symbol} @ {entryPrice} '.upper() + f'Qty- {quantity}')

    def exitOrder(self, index, instrumentID, exitPrice, exitType):
        trade_to_close = self.openPnl.loc[index].to_dict()

        if trade_to_close['PositionStatus'] == 1:
            limitPrice = getBuyLimitPrice(exitPrice, float(config.get('inputParameters', 'extraPercent')))
            orderSide = "SELL"
        else:
            limitPrice = getSellLimitPrice(exitPrice, float(config.get('inputParameters', 'extraPercent')))
            orderSide = "BUY"

        postOrderToDbLIMIT(
            exchangeSegment="NSECM",
            # productType='NRML',
            algoName=algoName,
            isLive=True if config.get('inputParameters', 'islive') == "True" else False,
            exchangeInstrumentID=instrumentID,
            orderSide=orderSide,
            orderQuantity=trade_to_close['Quantity'],
            limitPrice=limitPrice,
            upperPriceLimit=0 if trade_to_close['PositionStatus'] == 1 else (float(config.get('inputParameters', 'upperPriceLimitPercent')) * limitPrice),
            lowerPriceLimit=(float(config.get('inputParameters', 'lowerPriceLimitPercent')) * limitPrice) if trade_to_close['PositionStatus'] == 1 else 0,
            timePeriod=int(config.get('inputParameters', 'timeLimitOrder')),
            extraPercent=float(config.get('inputParameters', 'extraPercent')),
        )

        self.openPnl.drop(index=index, inplace=True)

        trade_to_close['Key'] = trade_to_close['EntryTime']
        trade_to_close['ExitTime'] = datetime.now()
        trade_to_close['ExitPrice'] = exitPrice
        trade_to_close['Pnl'] = (trade_to_close['ExitPrice'] - trade_to_close['EntryPrice']) * trade_to_close['Quantity'] * trade_to_close['PositionStatus']
        trade_to_close['ExitType'] = exitType

        for col in self.openPnl.columns:
            if col not in self.closedPnl.columns:
                del trade_to_close[col]

        self.closedPnl = pd.concat([self.closedPnl, pd.DataFrame([trade_to_close])], ignore_index=True)
        self.closedPnl.reset_index(inplace=True, drop=True)

        percentPnl = round(((trade_to_close['ExitPrice'] - trade_to_close['EntryPrice'])*trade_to_close['PositionStatus'])*100/trade_to_close['EntryPrice'], 1)
        percentPnl = "+" + str(percentPnl) if percentPnl > 0 else "-" + str(abs(percentPnl))

        profit = round(trade_to_close['Pnl'])
        if profit > 0:
            profit = f"+{round(profit)}"

        infoMessage(algoName=algoName, message=f'Exit {exitType}: {trade_to_close["Symbol"]} @ {exitPrice} [{percentPnl}%]'.upper() + f' PnL: {profit}')

        self.writeOpenPnlCsv()
        self.writeClosePnlCsv()
        self.stockLogger.info(f'Exit {exitType}: {trade_to_close["Symbol"]} @ {exitPrice}'.upper() + f'PnL: {profit}')


    def pnlCalculator(self):
        if not self.openPnl.empty:
            self.openPnl["PositionStatus"] = self.openPnl["PositionStatus"].fillna(0).astype(int)

            self.openPnl["Pnl"] = (self.openPnl["CurrentPrice"] - self.openPnl["EntryPrice"]) * self.openPnl["Quantity"] * self.openPnl["PositionStatus"]
            self.unrealizedPnl = self.openPnl["Pnl"].sum()

            self.writeOpenPnlCsv()
        else:
            self.unrealizedPnl = 0

        if not self.closedPnl.empty:
            self.realizedPnl = self.closedPnl["Pnl"].sum()
        else:
            self.realizedPnl = 0

        self.netPnl = self.unrealizedPnl + self.realizedPnl


class Strategy:
    def __init__(self):
        self.idMap = {}
        self.symListConn = None
        self.candle_1d = {}
        self.candle_1Min = {}
        self.stockDict = {}
        self.breakEven = {}
        self.lastPrintHour = 0
        # self.sum = 0

    def rename_col(self, df):
        df["ti"] = df.index
        df["o"] = df["Open"]
        df["h"] = df["High"]
        df["l"] = df["Low"]
        df["c"] = df["Close"]
        df["v"] = df["Volume"]
        df["sym"] = df["Symbol"]
        df["date"] = pd.to_datetime(df.index, unit='s')

        del df["Open"]
        del df["High"]
        del df["Low"]
        del df["Close"]
        del df["Volume"]

    def updateOpenPositionsInfra(self):
        combinedOpenPnl = pd.DataFrame(columns=["EntryTime", "Symbol", "EntryPrice", "CurrentPrice", "Quantity", "PositionStatus", "Pnl"])
        for stock in self.stockDict.keys():
            combinedOpenPnl = pd.concat([combinedOpenPnl, self.stockDict[stock].openPnl], ignore_index=True)
        combinedOpenPnl['EntryTime'] = combinedOpenPnl['EntryTime'].astype(str)
        positionUpdator(combinedOpenPnl, 'Process 1', algoName)

    def run_strategy(self, portfolio):
        try:
            subscribe_list = set(portfolio)
            for stock in portfolio:
                if stock not in self.stockDict:
                    self.stockDict[stock] = Stock(stock)
                    self.breakEven[stock] = False
                    writeJson1(f"breakEven{stock}", self.breakEven[stock])
                    self.candle_1d[stock] = {'last_candle_time': 0, 'df': None}
                    self.candle_1Min[stock] = {'last_candle_time': 0, 'df': None}
                subscribe_list.update(self.stockDict[stock].openPnl["Symbol"].unique().tolist())

            strategyLogger.info(f"Subscribing to the following symbols: {subscribe_list}")
            data, self.idMap, self.symListConn = reconnect(self.idMap, list(subscribe_list))

            portfolio = createSubPortfoliosList(portfolio, int(config.get('inputParameters', 'maxNumberOfThreads')))

            while True:
                current_time = datetime.now().time()
                if (current_time < time(9, 16)) or (current_time > time(15, 35)):
                    sleep(5)
                    continue
                for subPortfolio in portfolio:
                    if (current_time > time(15, 10)) and (current_time < time(15, 35)):
                        self.exec_strategy(subPortfolio)

                currentDatetime = datetime.now()
                currentTime = currentDatetime.time()

                sleep(1)
                self.updateOpenPositionsInfra()
                combineClosePnlCSV()
                combineOpenPnlCSV()

                if (readJson("algoStart") == True) or ((time(9, 20) < currentTime < time(9, 30)) or (time(15, 21) < currentTime < time(15, 29))) and not (time(15, 15) <= currentTime <= time(15, 20)):
                    try:
                        writeJson("algoStart", False)
                        updateCurrentPrices(self)
                        sleep(2)
                        totalInvestedAmount, totalInvestedAmountPercentage, totalCurrentAmount, totalCurrentAmountPercentage, netPnl, netPnlPercentage, mtm, mtmPercentage, realisedPnl, realisedPnlPercentage = algoInfoMessage()                          
                        sleep(1)
                        infoMessage(algoName=algoName, message=f"INVESTED: {round(totalInvestedAmount)}[{round(totalInvestedAmountPercentage, 1)}%] | CURRENT: {round(totalCurrentAmount)}[{round(totalCurrentAmountPercentage, 1)}%] | TOTAL: 1500000")
                        infoMessage(algoName=algoName, message=f"MTM: {round(mtm)}[{round(mtmPercentage, 1)}%] | NET P/L: {round(netPnl)}[{round(netPnlPercentage, 1)}%]  | REALISED: {round(realisedPnl)}[{round(realisedPnlPercentage, 1)}%]")
                        sleep(1)
                    except Exception as e:
                        infoMessage(algoName=algoName, message=f"Error: {str(e)}")

                elif not (time(14, 10) <= currentTime <= time(15, 35)):
                    updateCurrentPrices(self)
                    sleep(3600)
                    updateCurrentPrices(self)

        except Exception as err:
            errorMessage(algoName=algoName, message=str(err))
            strategyLogger.exception(str(err))


    def exec_strategy(self, subPortfolio):
        try:
            currentDatetime = datetime.now()
            currentTime = currentDatetime.time()

            algoName = config.get('inputParameters', 'algoName')
            logFileFolder, jsonFileFolder = algoLoggerSetup(algoName)

            for stock in subPortfolio:
                stock_logger = self.stockDict[stock].stockLogger

                try:
                    currentPrice = dataFetcher([self.idMap[stock]])[self.idMap[stock]]
                    stock_logger.info(f'[Tick] => Current Price: {currentPrice}')
                except Exception as e:
                    stock_logger.error(f"Error fetching current price for {stock}: {e}")
                    continue

                if currentPrice is None:
                    infoMessage(algoName=algoName, message=f"Data not found for: {stock}")
                    continue

                try:
                    df_1d, candle_flag_1d, last_candle_time_1d = OHLCDataFetch(
                        stock, currentDatetime.timestamp(), self.candle_1d[stock]['last_candle_time'], 'd', 
                        150, self.candle_1d[stock]['df'], stock_logger)
                    self.candle_1d[stock]['df'], self.candle_1d[stock]['last_candle_time'] = df_1d, last_candle_time_1d
                    resample_data(df_1d, 'd')
                    self.rename_col(df_1d)
                except Exception as e:
                    stock_logger.error(f"Error fetching daily OHLC data for {stock}: {e}")
                    continue

                try:
                    df_1Min, candle_flag_1Min, last_candle_time_1Min = OHLCDataFetch(
                        stock, currentDatetime.timestamp(), self.candle_1Min[stock]['last_candle_time'], 1, 
                        5, self.candle_1Min[stock]['df'], stock_logger)
                    if candle_flag_1Min and df_1Min is not None:
                        self.candle_1Min[stock]['df'], self.candle_1Min[stock]['last_candle_time'] = df_1Min, last_candle_time_1Min
                        self.rename_col(df_1Min)
                except Exception as e:
                    stock_logger.error(f"Error fetching 1-minute OHLC data for {stock}: {e}")
                    continue

                try:
                    df_1d = pd.concat([df_1d, df_1Min.tail(1)], ignore_index=False)
                    df_1d['rsi'] = talib.RSI(df_1d['c'], timeperiod=int(config.get('technicalIndicatorParameters', 'rsiTimePeriod')))
                    stock_logger.info(f"[1d] => Close: {df_1d.at[df_1d.index[-1], 'c']}")
                except Exception as e:
                    stock_logger.error(f"Error processing RSI for {stock}: {e}")
                    continue

                if not (time(15, 15) <= currentTime < time(15, 20)):
                    sleep(2)
                    continue

                # Exit conditions
                self.breakEven[stock] = readJson(f"breakEven{stock}")
                rsi_current = df_1d['rsi'].iloc[-1]
                rsi_breakeven_exit = int(config["inputParameters"]["breakevenExitRsi"])
                rsi_entry_time = int(config["inputParameters"]["EntryTimeRsi"])
                rsi_target_using = int(config["inputParameters"]["RsiTargetUsingRsi"])

                if not self.stockDict[stock].openPnl.empty:
                    for index, row in self.stockDict[stock].openPnl.iterrows():
                        entry_price, current_price, quantity, symbol = row['EntryPrice'], row['CurrentPrice'], row['Quantity'], row['Symbol']

                        if currentPrice < entry_price and rsi_current < rsi_breakeven_exit:
                            self.breakEven[stock] = True
                            writeJson(f"breakEven{stock}", self.breakEven[stock])

                        if self.breakEven[stock] and currentPrice > entry_price:
                            if rsi_current < rsi_entry_time:
                                exitType = "Breakeven"
                                self.breakEven[stock] = False
                                writeJson(f"breakEven{stock}", self.breakEven[stock])
                                self.stockDict[stock].exitOrder(index, self.idMap[symbol], current_price, exitType)
                                
                            elif rsi_current > rsi_entry_time:
                                self.breakEven[stock] = False
                                writeJson(f"breakEven{stock}", self.breakEven[stock])
                                infoMessage(algoName=algoName, message=f"Position_continue {stock}")

                        elif not self.breakEven[stock] and currentPrice > entry_price and rsi_current < rsi_target_using:
                            exitType = "TargetHit"
                            self.stockDict[stock].exitOrder(index, self.idMap[symbol], current_price, exitType)

                            PnLL = (current_price - entry_price) * quantity
                            ProfitAmount = readJson("ProfitAmount") + PnLL
                            writeJson("ProfitAmount", ProfitAmount)
                            infoMessage(algoName=algoName, message=f"TotalProfitAmount: {round(ProfitAmount)}")

                self.stockDict[stock].pnlCalculator()

                nowTotalTrades = len(combineOpenPnlCSV())
                TotalTradeCanCome = readJson("TotalTradeCanCome")
                ProfitAmount = readJson("ProfitAmount")
                amountPerTrade = int(config["inputParameters"]["amountpertrade"])

                if ProfitAmount > amountPerTrade:
                    TotalTradeCanCome += 1
                    ProfitAmount -= amountPerTrade
                    writeJson("TotalTradeCanCome", TotalTradeCanCome)
                    writeJson("ProfitAmount", ProfitAmount)
                    infoMessage(algoName=algoName, message=f"nowTotalTrades: {nowTotalTrades}, RestProfitAmount: {round(ProfitAmount)}, TotalTradeCanCome: {TotalTradeCanCome}")

                if self.stockDict[stock].openPnl.empty and nowTotalTrades < TotalTradeCanCome:
                    last_rsi, prev_rsi = df_1d['rsi'].iloc[-1], df_1d['rsi'].iloc[-2]

                    if last_rsi > int(config["inputParameters"]["EntryTimeRsi"]) and last_rsi > prev_rsi:
                        self.breakEven[stock] = False
                        nowTotalTrades += 1
                        prev_day_c = df_1d['c'].iloc[-2]
                        quantity = amountPerTrade // currentPrice
                        total_amount = round(currentPrice * quantity)
                        self.stockDict[stock].entryOrder(self.idMap[stock], stock, currentPrice, quantity, "BUY", {"prev_day_c": prev_day_c})
                        infoMessage(algoName=algoName, message=f'{nowTotalTrades} of {TotalTradeCanCome}, RSI: {round(last_rsi, 2)} Entry BUY: {stock} @ {currentPrice}, Qty: {quantity}, Amount: {total_amount}')

                sleep(0.1)

        except Exception as err:
            errorMessage(algoName=algoName, message=str(err))
            strategyLogger.exception(str(err))

class algoLogic:
    def mainLogic(self, mpName):
        try:
            global config
            config = ConfigParser()
            config.read('config.ini')

            global algoName
            algoName = config.get('inputParameters', f'algoName')
            global fileDir

            logFileFolder, jsonFileFolder = algoLoggerSetup(algoName)

            fileDir = {
                "baseJson": f"{jsonFileFolder}/json",
                "openPositions": f"{jsonFileFolder}/json/OpenPositions",
                "closedPositions": f"{jsonFileFolder}/json/ClosedPositions",
                "baseLog": f"{logFileFolder}/logs",
                "strategyLogs": f"{logFileFolder}/logs/StrategyLog",
                "stockLogs": f"{logFileFolder}/logs/StrategyLog/Stocks",
                "jsonValue": f"{jsonFileFolder}/jsonss/jsonFiles"
            }
            for keyDir in fileDir.keys():
                os.makedirs(fileDir[keyDir], exist_ok=True)

            global strategyLogger
            strategyLogger = setup_logger(algoName, f"{fileDir['strategyLogs']}/log_{datetime.now().replace(microsecond=0)}.log")
            strategyLogger.propagate = False

            portfolio = createPortfolioList(config.get('strategyParameters', 'portfolioList'))
            strategyLogger.info(f"PORTFOLIO USED: {portfolio}")

            writeJson("algoStart", True)
            strategyObj = Strategy()
            strategyObj.run_strategy(portfolio)

        except Exception as err:
            errorMessage(algoName=algoName, message=str(Exception(err)))
            strategyLogger.exception(str(Exception(err)))

if __name__ == "__main__":
    algoLogicObj = algoLogic()
    algoLogicObj.mainLogic("")