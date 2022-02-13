# imports
import numpy
import math
import time
import os
from datetime import datetime, timedelta, timezone
from tqdm import tqdm
import pandas_market_calendars as mcal
from dotenv import load_dotenv
from pathlib import Path
import logging
import requests
import certifi
import json
import traceback
import pytz
import polygon
import pandas as pd
import pyti
from pyti.hull_moving_average import hull_moving_average as hma

from api_trader import ApiTrader
from tdameritrade import TDAmeritrade
from mongo import MongoDB
from pymongo import MongoClient

from assets.pushsafer import PushNotification
from assets.exception_handler import exception_handler
from assets.helper_functions import selectSleep, getDatetime
from assets.timeformatter import Formatter
from assets.multifilehandler import MultiFileHandler


ca = certifi.where()
load_dotenv(dotenv_path=f"{os.path.abspath(os.path.dirname(__file__))}/config.env")

MONGO_URI = os.getenv('MONGO_URI')

CHANNELID = str(os.getenv('channelid'))
DISCORD_AUTH = str(os.getenv('discord_auth'))
DISCORD_USER = str(os.getenv('discord_user'))
POLY = os.getenv('POLYGON_URI')

class StratTester:

    def connectAll(self):
        """ METHOD INITIALIZES LOGGER, MONGO, GMAIL, PAPERTRADER.
        """

        # INSTANTIATE LOGGER
        file_handler = MultiFileHandler(
            filename=f'{os.path.abspath(os.path.dirname(__file__))}/logs/error.log', mode='a')

        formatter = Formatter('%(asctime)s [%(levelname)s] %(message)s')

        file_handler.setFormatter(formatter)

        ch = logging.StreamHandler()

        ch.setLevel(level="INFO")

        ch.setFormatter(formatter)

        self.logger = logging.getLogger(__name__)

        self.logger.setLevel(level="INFO")

        self.logger.addHandler(file_handler)

        self.logger.addHandler(ch)

        # CONNECT TO MONGO
        self.mongo = MongoDB(self.logger)

        mongo_connected = self.mongo.connect()

        # CONNECT TO GMAIL API
        self.gmail = None

        if mongo_connected:
            self.traders = {}

            self.accounts = []

            self.not_connected = []

            return True

        return False

    def setupTraders(self):
        """ METHOD GETS ALL USERS ACCOUNTS FROM MONGO AND CREATES LIVE TRADER INSTANCES FOR THOSE ACCOUNTS.
            IF ACCOUNT INSTANCE ALREADY IN SELF.TRADERS DICT, THEN ACCOUNT INSTANCE WILL NOT BE CREATED AGAIN.
        """
        # GET ALL USERS ACCOUNTS
        users = self.mongo.users.find({})

        for user in users:

            try:

                for account_id in user["Accounts"].keys():

                    if account_id not in self.traders and account_id not in self.not_connected:

                        push_notification = PushNotification(
                            user["deviceID"], self.logger)

                        tdameritrade = TDAmeritrade(
                            self.mongo, user, account_id, self.logger, push_notification)

                        connected = tdameritrade.initialConnect()

                        if connected:

                            obj = ApiTrader(user, self.mongo, push_notification, self.logger, int(
                                account_id), tdameritrade)

                            self.traders[account_id] = obj

                            time.sleep(0.1)

                        else:

                            self.not_connected.append(account_id)

                    self.accounts.append(account_id)

            except Exception as e:

                logging.error(e)



    def backTest(self):

        directory = 'Z:/thinkorswim/TradingBOT/BacktestDataframes/Dataframes/'

        for file in os.listdir(directory):

            symbol = file.split('_')
            symbol=symbol[1]

            df = pd.read_excel(f'{directory}{file}')
            df['symbol'] = symbol
            print(df)

            # symbol = .split(' ')
            # symbol = alert['Symbol']
            # option_type = alert['Option_Type'].lower()
            # strike_price = float(alert['Strike_Price'])
            # exp_date = datetime.strptime(alert['Exp_Date'], '%Y-%m-%d')
            # timestamp = timestamp.replace(microsecond=0)
            # timestamp = pd.to_datetime(timestamp).tz_localize(None)
            # end_of_day = timestamp.replace(hour=21)
            # hedge = alert['HedgeAlert']
            #
            # try:
            #     pre_symbol = polygon.build_option_symbol(symbol, exp_date, option_type, strike_price, prefix_o=True)
            #     symbol_no = polygon.build_option_symbol(symbol, exp_date, option_type, strike_price, prefix_o=False)
            #     agg_bars = client.get_aggregate_bars(pre_symbol, start_date, end_date, multiplier='1', timespan='minute')
            #     # print(agg_bars)
            #     if agg_bars['resultsCount'] == 0:
            #         print('SLEEPING')
            #         time.sleep(5)
            #     df = pd.DataFrame(agg_bars['results'])
            #     df['t'] = pd.to_datetime(df['t'], unit='ms')
            #     df['hedge'] = hedge
            #     # print(df)
            #     file_name =f'dataframe_{symbol_no}_{x}.xlsx'
            #     df.to_excel(file_name)
            #     print(pre_symbol)
            #
            #     x+=1
            #
            #     mask = (df['t'] >= timestamp) & (df['t'] <= end_of_day)
            #     df2 = df.loc[mask]
            #     # print(df2)
            #
            #     df3 = df2[df2['h'] == df2['h'].max()]
            #     df4 = df2[df2['l'] == df2['l'].min()]
            #
            #     print('high',df3)
            #     print('low',df4)
            #     high = df2['h']
            #     high = high.max()
            #     low = df2['l']
            #     low = low.min()
            #
            #
            #
            #     print(f'Updated Max Price for {symbol}')
            #
            #     time.sleep(14)
            #
            # except Exception:
            #
            #     msg = f"error: {traceback.format_exc()}"
            #     self.logger.error(msg)

if __name__ == "__main__":
    """ START OF SCRIPT.
        INITIALIZES MAIN CLASS AND STARTS RUN METHOD ON WHILE LOOP WITH A DYNAMIC SLEEP TIME.
    """

    main = StratTester()

    main.connectAll()

    start_time = datetime.now(timezone.utc)

    main.backTest()