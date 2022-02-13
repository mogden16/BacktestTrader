# imports
import time
from assets.exception_handler import exception_handler
from assets.helper_functions import getDatetime, selectSleep, modifiedAccountID
import requests
import os
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime, timedelta
import requests
import pytz
from tqdm import tqdm
import traceback
import polygon
import pandas as pd


THIS_FOLDER = os.path.dirname(os.path.abspath(__file__))

path = Path(THIS_FOLDER)

load_dotenv(dotenv_path=f"{path.parent}/config.env")

POLY = os.getenv('POLYGON_URI')

class Tasks:

    # THE TASKS CLASS IS USED FOR HANDLING ADDITIONAL TASKS OUTSIDE OF THE LIVE TRADER.
    # YOU CAN ADD METHODS THAT STORE PROFIT LOSS DATA TO MONGO, SELL OUT POSITIONS AT END OF DAY, ECT.
    # YOU CAN CREATE WHATEVER TASKS YOU WANT FOR THE BOT.
    # YOU CAN USE THE DISCORD CHANNEL NAMED TASKS IF YOU ANY HELP.

    def __init__(self):

        self.isAlive = True
        self.isInLunch = False
        self.isMarketOpen = False
        self.takeprofitpercentage = float(os.getenv('TAKE_PROFIT_PERCENTAGE'))
        self.stop_loss_percentage = float(os.getenv('STOP_LOSS_PERCENTAGE'))
        self.take_profit_percentage = float(os.getenv('TAKE_PROFIT_PERCENTAGE'))
        self.polypoly = False

    @exception_handler
    def checkOCOtriggers(self):
        """ Checks OCO triggers (stop loss/ take profit) to see if either one has filled. If so, then close position in mongo like normal.

        """

        open_positions = self.open_positions.find(
            {"Trader": self.user["Name"], "Order_Type": "OCO" or "TRAIL"})

        for position in open_positions:

            childOrderStrategies = position["childOrderStrategies"]

            for order_id in childOrderStrategies.keys():

                spec_order = self.tdameritrade.getSpecificOrder(order_id)

                new_status = spec_order["status"]

                if new_status == "FILLED":

                    self.pushOrder(position, spec_order)

                elif new_status == "CANCELED" or new_status == "REJECTED":

                    other = {
                        "Symbol": position["Symbol"],
                        "Order_Type": position["Order_Type"],
                        "Order_Status": new_status,
                        "Strategy": position["Strategy"],
                        "Trader": self.user["Name"],
                        "Date": getDatetime(),
                        "Account_ID": self.account_id
                    }

                    self.rejected.insert_one(
                        other) if new_status == "REJECTED" else self.canceled.insert_one(other)

                    self.logger.info(
                        f"{new_status.upper()} ORDER For {position['Symbol']} - TRADER: {self.user['Name']} - ACCOUNT ID: {modifiedAccountID(self.account_id)}")

                else:

                    self.open_positions.update_one({"Trader": self.user["Name"], "Symbol": position["Symbol"], "Strategy": position["Strategy"]}, {
                        "$set": {f"childOrderStrategies.{order_id}.Order_Status": new_status}})

    @exception_handler
    def extractOCOchildren(self, spec_order):
        """This method extracts oco children order ids and then sends it to be stored in mongo open positions. 
        Data will be used by checkOCOtriggers with order ids to see if stop loss or take profit has been triggered.

        """

        oco_children = {
            "childOrderStrategies": {}
        }

        childOrderStrategies = spec_order["childOrderStrategies"][0]["childOrderStrategies"]

        for child in childOrderStrategies:

            oco_children["childOrderStrategies"][child["orderId"]] = {
                "Side": child["orderLegCollection"][0]["instruction"],
                "Exit_Price": child["stopPrice"] if "stopPrice" in child else child["price"],
                "Exit_Type": "STOP LOSS" if "stopPrice" in child else "TAKE PROFIT",
                "Order_Status": child["status"]
            }

        return oco_children

    @exception_handler
    def addNewStrategy(self, strategy, asset_type):
        """ METHOD UPDATES STRATEGIES OBJECT IN MONGODB WITH NEW STRATEGIES.

        Args:
            strategy ([str]): STRATEGY NAME
        """

        obj = {"Active": True,
               "Order_Type": "STANDARD",
               "Asset_Type": asset_type,
               "Position_Size": 500,
               "Position_Type": "LONG",
               "Account_ID": self.account_id,
               "Strategy": strategy,
               }

        # IF STRATEGY NOT IN STRATEGIES COLLECTION IN MONGO, THEN ADD IT

        self.strategies.update(
            {"Strategy": strategy},
            {"$setOnInsert": obj},
            upsert=True
        )

    @exception_handler
    def showLunchTime(self):
        """
        LUNCHTIME OPEN(1130 ET)
        LUNCHTIME CLOSE(1340 ET)

        """

        dt = getDatetime()

        tm = dt.strftime("%H:%M")

        market_open = "9:30"

        lunch_open = "11:30"

        lunch_close = "13:40"

        # IF CURRENT TIME IS BETWEEN LUNCHTIME OPEN & CLOSE, THEN PRINT LUNCHTIME
        if not self.isInLunch and tm == lunch_open:
            discord_alert = {"content": f"Lunch is starting!"}
            response = requests.post(os.getenv('DISCORD_WEBHOOK'), json=discord_alert)
            print("Lunch is starting!")
            self.isInLunch = True

        elif self.isInLunch and tm == lunch_close:
            discord_alert = {"content": f"Lunch is over!"}
            response = requests.post(os.getenv('DISCORD_WEBHOOK'), json=discord_alert)
            print("Lunch is over!")
            self.isInLunch = False


        if not self.isMarketOpen and tm == market_open:
            discord_alert = {"content": f"Market Open!"}
            response = requests.post(os.getenv('DISCORD_WEBHOOK'), json=discord_alert)
            print("Market Open!")
            self.isMarketOpen = True
        elif self.isMarketOpen and tm == "16:00":
            discord_alert = {"content": f"Market Closed!"}
            response = requests.post(os.getenv('DISCORD_WEBHOOK'), json=discord_alert)
            print("Market Closed!")
            self.isMarketOpen = False



    @exception_handler
    def killQueueOrder(self):
        """ METHOD QUERIES ORDERS IN QUEUE AND LOOKS AT INSERTION TIME.
            IF QUEUE ORDER INSERTION TIME GREATER THAN TWO HOURS, THEN THE ORDER IS CANCELLED.
        """
        # CHECK ALL QUEUE ORDERS AND CANCEL ORDER IF GREATER THAN TWO MINUTES OLD
        queue_orders = self.queue.find(
            {"Trader": self.user["Name"], "Account_ID": self.account_id})

        dt = datetime.now(tz=pytz.UTC).replace(microsecond=0)

        dt_tz = dt.astimezone(pytz.timezone(os.getenv('TIMEZONE')))

        one_mins_ago = datetime.strptime(datetime.strftime(
            dt_tz - timedelta(minutes=1), "%Y-%m-%d %H:%M:%S"), "%Y-%m-%d %H:%M:%S")

        for order in queue_orders:

            order_date = order["Entry_Date"]

            order_type = order["Order_Type"]

            id = order["Order_ID"]

            forbidden = ["REJECTED", "CANCELED", "FILLED"]

            pre_symbol = order["Pre_Symbol"]

            if one_mins_ago > order_date and (order_type == "BUY" or order_type == "BUY_TO_OPEN") and id != None and order["Order_Status"] not in forbidden:

                # FIRST CANCEL ORDER
                resp = self.tdameritrade.cancelOrder(id)

                if resp.status_code == 200 or resp.status_code == 201:

                    other = {
                        "Symbol": order["Symbol"],
                        "Pre_Symbol": order["Pre_Symbol"],
                        "Order_Type": order["Order_Type"],
                        "Order_Status": "CANCELED",
                        "Strategy": order["Strategy"],
                        "Account_ID": self.account_id,
                        "Trader": self.user["Name"],
                        "Date": getDatetime()
                    }

                    self.other.insert_one(other)

                    self.queue.delete_one(
                        {"Trader": self.user["Name"], "Symbol": order["Symbol"], "Strategy": order["Strategy"]})

                    self.logger.INFO(
                        f"CANCELED ORDER FOR {order['Symbol']} - TRADER: {self.user['Name']}", True)

                    discord_alert = {"content": f"TradingBOT just cancelled order for: {pre_symbol}"}
                    response = requests.post(os.getenv('DISCORD_WEBHOOK'), json=discord_alert)


    def runPolygon(self):

        now = datetime.now()

        today10pm = now.replace(hour=22, minute=0)

        if now < today10pm:
            x=0
            while x != 1:

                analysis_alerts = list(self.analysis.find({}))

                lookback = timedelta(hours=24)
                end_date = datetime.now()
                start_date = end_date - lookback

                POLY = str(os.getenv('POLYGON_URI'))
                KEY = POLY
                client = polygon.StocksClient(KEY)

                alist = tqdm(analysis_alerts)
                x=0
                for alert in alist:
                    alist.set_description("Finding lastPrice for all Analysis_Positions")
                    id = alert['_id']
                    tickersymbol = alert['Symbol']
                    option_type = alert['Option_Type'].lower()
                    strike_price = float(alert['Strike_Price'])
                    exp_date = datetime.strptime(alert['Exp_Date'], '%Y-%m-%d')
                    entry_date = alert['Entry_Date'].replace(microsecond=0)
                    entry_date = pd.to_datetime(entry_date)
                    max_price = alert['Max_Price']

                    try:
                        symbol = polygon.build_option_symbol(tickersymbol, exp_date, option_type, strike_price, prefix_o=True)
                        agg_bars = client.get_aggregate_bars(symbol, start_date, end_date, multiplier='1', timespan='minute')
                        # print(agg_bars)
                        while agg_bars['resultsCount'] == 0:
                            time.sleep(60*10)

                        discord_msg = {"content": f"Just Found agg_bars"}
                        response = requests.post(os.getenv('DISCORD_WEBHOOK'), json=discord_msg)

                        df = pd.DataFrame(agg_bars['results'])
                        df['t'] = pd.to_datetime(df['t'], unit='ms')

                        # file_name = f'dataframe for {tickersymbol}{x}.xlsx'
                        # df.to_excel(file_name)
                        x+=1
                        print(type(entry_date))
                        print(type(end_date))
                        mask = (df['t'] >= entry_date) & (df['t'] <= end_date)
                        df2 = df.loc[mask]

                        df2 = df[df['h'] == df['h'].max()]

                        high = df2['h']
                        high=high.max()
                        max_time = df2['t']
                        max_time = max_time.max()

                        print(f'Scanning {symbol}')

                        if high > max_price:
                            print(high)
                            print(max_time)
                            self.analysis.update_one({"_id": id}, {"$set": {'Max_Price': high}}, upsert=True)
                            self.analysis.update_one({"_id": id}, {"$set": {"Max_Time": max_time}}, upsert=True)

                            print(f'Updated Max Price for {symbol}')

                        time.sleep(14)

                    except Exception:

                        msg = f"error: {traceback.format_exc()}"
                        self.logger.error(msg)

                print('\n')
                discord_msg = {"content": f"Just Found agg_bars"}
                response = requests.post(os.getenv('DISCORD_WEBHOOK'), json=discord_msg)
                x+=1


    def runTasks(self):
        """ METHOD RUNS TASKS ON WHILE LOOP EVERY 5 - 60 SECONDS DEPENDING.
        """

        self.logger.info(
            f"STARTING TASKS FOR {self.user['Name']} ({modifiedAccountID(self.account_id)})", extra={'log': False})

        while self.isAlive:

            try:

                # RUN TASKS ####################################################
                self.checkOCOtriggers()
                self.showLunchTime()
                self.killQueueOrder()
                self.runPolygon()

                ##############################################################

            except KeyError:

                self.isAlive = False

            except Exception as e:

                self.logger.error(
                    f"ACCOUNT ID: {modifiedAccountID(self.account_id)} - TRADER: {self.user['Name']} - {e}")

            finally:

                time.sleep(selectSleep())

        self.logger.warning(
            f"TASK STOPPED FOR ACCOUNT ID {modifiedAccountID(self.account_id)}")
