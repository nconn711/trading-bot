import os
import csv
import time
import logging
from datetime import datetime, timezone
from threading import Thread
import argparse

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order

class IBClient(EWrapper, EClient):
    def __init__(self, stock_symbol, upper, lower, buy_qty, host='127.0.0.1', port=7497, client_id=1): # 4002 for IB Gateway
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)

        # Connection parameters
        self.host = host
        self.port = port
        self.client_id = client_id

        # Trading parameters
        self.stock_symbol = stock_symbol
        self.exchange = 'SMART'
        self.currency = 'USD'
        self.upper_bound = upper  # Trigger price for BUY orders
        self.lower_bound = lower  # Trigger price for SELL orders
        self.buy_qty = buy_qty    # Shares per order

        # Create our contract
        self.contract = self.create_contract(self.stock_symbol,
                                             self.exchange,
                                             self.currency)

        # Track the one active order (None if no order is active)
        self.active_order = None
        self.fill_tracker = {}  # order_id -> filled quantity

        # Trading state: True if holding the stock.
        self.in_position = False

        # Flags for initialization from IB data.
        self.open_orders_loaded = False
        self.positions_loaded = False
        self.logged_position = False  # Prevent repeated logging in position callback

        # For tracking running losses: track last BUY execution price and cumulative loss.
        self.last_buy_price = None
        self.running_loss = 0.0

        # Setup logging with UTC timestamps and file output
        os.makedirs("logs", exist_ok=True)
        start_time_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        log_filename = f"logs/ibapp_log_{start_time_utc}.log"
        logging.Formatter.converter = time.gmtime
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_filename),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

        logging.info("Trading Bot Configuration:")
        logging.info(f"  Stock Symbol     : {self.stock_symbol}")
        logging.info(f"  Upper Threshold  : {self.upper_bound}")
        logging.info(f"  Lower Threshold  : {self.lower_bound}")
        logging.info(f"  Buy Quantity     : {self.buy_qty}")

        # Setup CSV trade log (create header if file doesn't exist)
        self.csv_file = f"logs/ibapp_log_{start_time_utc}.csv"
        if not os.path.exists(self.csv_file):
            with open(self.csv_file, "w", newline="") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow([
                    "timestamp", "order_id", "event_type", "action",
                    "order_type", "trigger_price", "executed_price",
                    "fill_quantity", "status", "message"
                ])

        # IB API bookkeeping
        self.next_order_id = None

    def create_contract(self, symbol, exchange, currency):
        """Helper method to create a stock contract."""
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = exchange
        contract.currency = currency
        return contract

    def connect_and_run(self):
        """Connect to IB and start the socket thread."""
        self.connect(self.host, self.port, self.client_id)
        thread = Thread(target=self.run, daemon=True)
        thread.start()
        # Wait until next_order_id is available.
        while self.next_order_id is None:
            time.sleep(0.1)
        # Request open orders and positions for initialization.
        self.reqOpenOrders()
        self.reqPositions()
        self.reqTickByTickData(1, self.contract, "AllLast", 0, False)

    def log_trade_event(self, order_id, event_type, action, order_type,
                        trigger_price, executed_price, fill_quantity, status, message):
        """Append a trade event record to the CSV file."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.csv_file, "a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                now_str, order_id, event_type, action, order_type,
                trigger_price, executed_price, fill_quantity, status, message
            ])

    def initialize_orders_if_ready(self):
        """After open orders and positions are loaded, decide whether to place an initial order."""
        if self.open_orders_loaded and self.positions_loaded:
            self.logger.info("Initialization complete.")
            # If not in position and no active order, place an initial BUY stop order.
            if self.active_order is None:
                self.logger.info("No active position or BUY order detected. Placing initial BUY stop order.")
                self.place_buy_order()
            else:
                self.logger.info("Existing position or active BUY order detected; no initial order placed.")

    # --- IB API Callbacks ---
    def nextValidId(self, order_id: int):
        self.next_order_id = order_id
        self.logger.info(f"Next valid order ID: {order_id}")
        self.reqOpenOrders()
        self.reqPositions()

    def tickByTickAllLast(self, reqId, tickType, timestamp, price, size, tickAttribLast, exchange, specialConditions):
        self.logger.info(f"TickByTick Update: Price {price} at {timestamp}")

    def openOrder(self, order_id, contract, order, order_state):
        """Called for each open order."""
        if contract.symbol == self.stock_symbol and order.orderType == "STP":
            # Store the order object itself.
            order.orderId = order_id
            self.active_order = order
            self.logger.info(f"Found open order: {order_id} Action: {order.action}")

    def openOrderEnd(self):
        self.open_orders_loaded = True
        self.logger.info("Open orders loaded.")
        self.initialize_orders_if_ready()

    def position(self, account, contract, pos, avgCost):
        """Called for each position. Logs only once."""
        if contract.symbol == self.stock_symbol and pos > 0:
            self.in_position = True
            if not self.logged_position:
                self.logger.info(f"Existing position: {pos} shares of {self.stock_symbol}")
                self.logged_position = True

    def positionEnd(self):
        self.positions_loaded = True
        self.logger.info("Positions loaded.")
        self.initialize_orders_if_ready()

    def error(self, req_id, error_code, error_string, misc=''):
        self.logger.error(f"Error. ReqId: {req_id}, Code: {error_code}, Msg: {error_string}")

    def execDetails(self, req_id, contract, execution):
        order_id = execution.orderId
        executed_price = execution.price
        fill_qty = execution.shares

        if self.active_order and self.active_order.orderId == order_id:
            self.fill_tracker[order_id] = self.fill_tracker.get(order_id, 0) + fill_qty
            filled_total = self.fill_tracker[order_id]
            action = self.active_order.action

            if filled_total < self.buy_qty:
                self.logger.info(f"Partial fill: Order {order_id} Action: {action} @ {executed_price} Qty: {fill_qty} (Total filled: {filled_total}/{self.buy_qty})")
                self.log_trade_event(order_id, "PartialFill", action,
                                     self.active_order.orderType,
                                     self.active_order.auxPrice,
                                     executed_price, fill_qty, "Partial",
                                     f"Partial fill at {executed_price}. Running total: {filled_total}")
            elif filled_total >= self.buy_qty:
                self.logger.info(f"Order {order_id} fully filled.")

                if action == "BUY":
                    self.last_buy_price = executed_price
                    self.log_trade_event(order_id, "Executed", action,
                                         self.active_order.orderType,
                                         self.active_order.auxPrice,
                                         executed_price, fill_qty, "Filled",
                                         f"BUY completed at {executed_price}")
                    # Reset order tracking
                    self.active_order = None
                    self.in_position = True
                    self.place_sell_order()

                elif action == "SELL":
                    loss = 0.0
                    if self.last_buy_price is not None:
                        loss = (self.last_buy_price - executed_price) * self.buy_qty
                    self.running_loss += loss
                    self.log_trade_event(order_id, "Executed", action,
                                         self.active_order.orderType,
                                         self.active_order.auxPrice,
                                         executed_price, fill_qty, "Filled",
                                         f"SELL completed at {executed_price}. Loss: {loss:.2f}. Running loss: {self.running_loss:.2f}")
                    # Reset order tracking
                    self.active_order = None
                    self.in_position = False
                    self.place_buy_order()

                del self.fill_tracker[order_id]


    # --- End Callbacks ---

    def place_buy_order(self):
        """Place a BUY stop order at the upper limit."""
        if self.active_order is None:
            order = Order()
            order.action = "BUY"
            order.orderType = "STP"
            order.totalQuantity = self.buy_qty
            order.auxPrice = self.upper_bound  # Trigger price for buying

            order.orderId = self.next_order_id
            self.next_order_id += 1

            self.placeOrder(order.orderId, self.contract, order)
            self.active_order = order
            self.log_trade_event(order.orderId, "Placed", order.action, order.orderType,
                                 order.auxPrice, None, 0, "Pending",
                                 "Stop loss BUY order placed.")
            self.logger.info(f"Placed BUY stop order (ID: {order.orderId}) at trigger {order.auxPrice}.")

    def place_sell_order(self):
        """Place a SELL stop order at the lower limit."""
        if self.active_order is None:
            order = Order()
            order.action = "SELL"
            order.orderType = "STP"
            order.totalQuantity = self.buy_qty
            order.auxPrice = self.lower_bound  # Trigger price for selling

            order.orderId = self.next_order_id
            self.next_order_id += 1

            self.placeOrder(order.orderId, self.contract, order)
            self.active_order = order
            self.log_trade_event(order.orderId, "Placed", order.action, order.orderType,
                                 order.auxPrice, None, 0, "Pending",
                                 "Stop loss SELL order placed.")
            self.logger.info(f"Placed SELL stop order (ID: {order.orderId}) at trigger {order.auxPrice}.")

    def run_bot(self):
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("KeyboardInterrupt caught in run_bot. Exiting...")
            self.disconnect()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the trading bot.")
    parser.add_argument("--stock-symbol", type=str, required=True, help="Ticker symbol (e.g. TSLA, AAPL)")
    parser.add_argument("--upper", type=float, required=True, help="Upper threshold for stop loss buy order")
    parser.add_argument("--lower", type=float, required=True, help="Lower threshold for stop loss sell order")
    parser.add_argument("--buy-qty", type=int, required=True, help="Buy quantity for each order")
    args = parser.parse_args()

    if args.upper <= args.lower:
        parser.error("Upper threshold must be greater than lower threshold.")
        exit()

    client = IBClient(stock_symbol=args.stock_symbol, upper=args.upper, lower=args.lower, buy_qty=args.buy_qty)
    client.connect_and_run()
    client.run_bot()
