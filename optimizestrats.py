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
import shutil
import glob

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

class OptimizeStrats:
    def __init__(self):
        self.strategy = os.getenv('strategy')
        self.backtestBot = str(os.getenv('backtestBot')).lower()
        self.dst_dir = str(os.getenv('dst_dir'))
        self.ext_dir = str(os.getenv('ext_dir'))

        self.position_size = float(os.getenv('position_size'))
        self.take_profit_percentage = [.1,.15,.2,.25,.3,.5,1]
        self.p_l = []

        self.strategy_to_backtest = str(os.getenv('strategy_to_backtest')).lower()


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

    def opendataframes(self, take_profit_pct):

        df = pd.read_excel(file, engine='openpyxl')

        if df.empty:
            return
        else:
            entry = df.loc[0]
        entry_date = entry['t']
        entry_price = entry['c']
        quantity = int((self.position_size/100)/entry_price)

        for index, row in df.iterrows():
            open_order = True
            if self.strategy_to_backtest == "take_profit":
                take_profit_price = round(entry_price*(1+take_profit_pct),2)
                if row['h'] >= take_profit_price and open_order == True:
                    profit = take_profit_price * quantity * 100
                    exit_time = row['t']
                    self.p_l.append(profit)
                    open_order = False
                    # print(f'entry_price: {entry_price}  at  entry_date: {entry_date}   ->  '
                    #        f'now exit_price {take_profit_price}  at exit_date{exit_time}')
                    # print(f'take profit hit: P/L= ${round(profit, 2)}')
                    return

                elif index == (len(df.index)-1) and open_order == True:
                    # print('did not hit take_profit')
                    exit = df.iloc[-1]
                    exit_price = exit['c']
                    exit_time = exit['t']
                    exit_p_l = (exit_price - entry_price) * quantity * 100
                    self.p_l.append(exit_p_l)
                    # print(f'entry_price: {entry_price}  at  entry_date: {entry_date}   ->  '
                    #       f'now exit_price {exit_price}  at exit_date{exit_time}')
                    # print(f'never hit stop price: P/L= ${exit_p_l}')
                    open_order = False
                    return

            if self.strategy_to_backtest == "trail":
                print('NOT STARTED YET')



if __name__ == "__main__":
    """ START OF SCRIPT.
        INITIALIZES MAIN CLASS AND STARTS RUN METHOD ON WHILE LOOP WITH A DYNAMIC SLEEP TIME.
    """

    main = OptimizeStrats()

    connected = main.connectAll()

    start_time = datetime.now(timezone.utc) - timedelta(days=1)

    for take_profit_pct in main.take_profit_percentage:

        for file in tqdm(glob.glob(main.dst_dir + '/Dataframes/' + '*.xlsx')):
            file_name = file.split('_')
            file_name = file_name[1]
            # print(file_name)
            try:
                main.opendataframes(take_profit_pct)
                # print('\n')

            except Exception:

                msg = f"error: {traceback.format_exc()}"
                main.logger.error(msg)

        print(f'P/L if take profit is {take_profit_pct*100}%: {sum(main.p_l)}')


