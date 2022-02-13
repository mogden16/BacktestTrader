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
import optimizestrats

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

class BacktestTrader:
    def __init__(self):
        self.strategy = os.getenv('strategy')
        self.backtestBot = str(os.getenv('backtestBot')).lower()
        self.dst_dir = str(os.getenv('dst_dir'))
        self.ext_dir = str(os.getenv('ext_dir'))


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


    def try_parsing_date(self, text):

        for fmt in ('%Y-%m-%dT%H:%M:%S.%f%z','%Y-%m-%dT%H:%M:%S%z'):
            try:
                return datetime.strptime(text,fmt)
            except ValueError:
                pass
        raise ValueError('no valid date format found')


    def discord_messages(self, CHANNELID, start_time):

        self.backtestlist = []
        self.dontbacktestlist = []

        headers = {
            'authorization': DISCORD_AUTH
        }
        r = requests.get(
            f'https://discord.com/api/v9/channels/{CHANNELID}/messages', headers=headers)

        jsonn = json.loads(r.text)
        # for i in jsonn:
        #     print(i['timestamp'])
        x=0
        for value in jsonn:
            hedge = False
            value['timestamp'] = self.try_parsing_date(value['timestamp'])
            if value['timestamp'] >= start_time - timedelta(days=10):
                # if value['timestamp'].day == 10:
                #     continue
                if value['author']['username'] == DISCORD_USER:
                    if len(value['embeds']) == 0:
                        continue
                    statement = value['embeds'][0]['description'].split(' ')

                    timestamp = value['timestamp']

                    # if statement[1] == 'Hedge':
                    #     continue
                    if statement[1] == 'Hedge':
                        hedge = True
                        statement.remove('Hedge')
                    if statement[1] == "flow,":
                        statement.remove("flow,")
                    if statement[1] == "flow":
                        statement.remove('flow')
                    if statement[1] == " ":
                        statement.remove(" ")
                    if statement[1] == "":
                        statement.remove("")
                    if statement[1] == ",":
                        statement.remove(",")
                    symbol = statement[0]
                    str_month = statement[1]
                    datetime_month_object = datetime.strptime(str_month, '%b')
                    exp_month = '%02d' % datetime_month_object.month
                    float_day = float(statement[2])
                    if float_day < 10:
                        exp_day = str(0)+statement[2]
                    else:
                        exp_day = statement[2]
                    min_strike = statement[3].split('-')[0].lstrip('$')
                    max_strike = statement[3].split('-')[1].lstrip('$')
                    option_type = statement[4].upper()
                    option_type = option_type.rstrip(option_type[-1])
                    option_type_short = option_type[0]

                    trade_symbol = (f'{symbol}_{exp_month}{exp_day}22{option_type_short}{min_strike}')
                    list_pre_symbol = {'symbol': symbol, 'exp_month': exp_month, 'exp_day': exp_day,
                                       'option_type': option_type, 'min_strike': min_strike, 'max_strike': max_strike}

                    obj = {
                        "Symbol": symbol,
                        "Side": "BUY_TO_OPEN",
                        "Pre_Symbol": trade_symbol,
                        "Exp_Date": f'2022-{exp_month}-{exp_day}',
                        "Strike_Price": min_strike,
                        "Option_Type": option_type,
                        "Strategy": self.strategy,
                        "Asset_Type": "OPTION",
                        "HedgeAlert": "TRUE" if hedge else "FALSE",
                        "Timestamp": timestamp
                    }

                    self.backtestlist.append(obj)


    def grabDataframes(self):
        self.analysis = self.mongo.analysis

        analysis_alerts = list(self.analysis.find({}))

        lookback = timedelta(days=14)
        end_date = datetime.now()
        start_date = end_date - lookback

        POLY = str(os.getenv('POLYGON_URI'))
        KEY = POLY
        client = polygon.StocksClient(KEY)

        x=0

        if self.backtestBot == "analysis":
            analysisList = analysis_alerts
        elif self.backtestBot == "closed_positions":
            print('this is not developed yet! Please choose another self.backtestBot setting')
        elif self.backtestBot == "discord":
            analysisList = self.backtestlist
        else:
            print('error in self.backtestBot env')


        for alert in tqdm(analysisList):
            if self.backtestBot == "analysis" or self.backtestBot == "closed_positions":
                timestamp = alert['Entry_Date']
            elif self.backtestBot == "discord":
                timestamp = alert['Timestamp']

            symbol = alert['Symbol']
            option_type = alert['Option_Type'].lower()
            strike_price = float(alert['Strike_Price'])
            exp_date = datetime.strptime(alert['Exp_Date'], '%Y-%m-%d')
            timestamp = timestamp.replace(microsecond=0)
            timestamp = pd.to_datetime(timestamp).tz_localize(None)
            end_of_day = timestamp.replace(hour=21)
            hedge = alert['HedgeAlert']

            try:
                pre_symbol = polygon.build_option_symbol(symbol, exp_date, option_type, strike_price, prefix_o=True)
                symbol_no = polygon.build_option_symbol(symbol, exp_date, option_type, strike_price, prefix_o=False)
                agg_bars = client.get_aggregate_bars(pre_symbol, start_date, end_date, multiplier='1', timespan='minute')
                # print(agg_bars)
                if agg_bars['resultsCount'] == 0:
                    print('SLEEPING')
                    time.sleep(5)
                df = pd.DataFrame(agg_bars['results'])
                df['t'] = pd.to_datetime(df['t'], unit='ms')
                df['hedge'] = hedge
                # print(df)
                print(pre_symbol)
                print('entry_date: ', timestamp)

                x+=1

                mask = (df['t'] >= timestamp) & (df['t'] <= end_of_day)
                df2 = df.loc[mask]
                file_name =f'dataframe_{symbol_no}_{x}.xlsx'
                df2.to_excel(file_name)
                # print(df2)

                df3 = df2[df2['h'] == df2['h'].max()]
                df4 = df2[df2['l'] == df2['l'].min()]

                print(df3)
                print(df4)

                time.sleep(14)

            except Exception:

                msg = f"error: {traceback.format_exc()}"
                self.logger.error(msg)


    def movexlsx(self):

        # ABSOLUTE PATH TO CSV_FILES DIRECTORY
        tf_path = self.dst_dir + '/Dataframes/'
        print(f"Moving files to: {tf_path}")

        for file in tqdm(glob.glob(self.ext_dir + '/' + '*.xlsx')):

            try:

                # MOVES MOST RECENT STRATEGY REPORT FILE FROM DOCUMENTS FOLDER TO CSV_FILES FOLDER

                shutil.move(file, tf_path)

            except shutil.Error as e:

                self.throwError(e)

if __name__ == "__main__":
    """ START OF SCRIPT.
        INITIALIZES MAIN CLASS AND STARTS RUN METHOD ON WHILE LOOP WITH A DYNAMIC SLEEP TIME.
    """

    main = BacktestTrader()

    connected = main.connectAll()

    start_time = datetime.now(timezone.utc) - timedelta(days=1)

    if main.backtestBot == 'discord':
        main.discord_messages(CHANNELID, start_time)

    main.grabDataframes()

    main.movexlsx()

    for file in tqdm(glob.glob(main.dst_dir + '/Dataframes/' + '*.xlsx')):
        file_name = file.split('_')
        file_name = file_name[1]
        print(file_name)
        optimizestrats.opendataframes()
        print('\n')


