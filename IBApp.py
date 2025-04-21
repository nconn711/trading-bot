import os
import csv
import time
import logging
import requests
from datetime import datetime, timezone
from threading import Thread
import argparse

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order




class IBClient(EWrapper, EClient):
    def __init__(
        self,
        stock_symbol,
        upper,
        lower,
        buy_qty,
        host='127.0.0.1',
        port=7497,
        client_id=1
    ):
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)

        self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL")

        # Connection parameters
        self.host = host
        self.port = port
        self.client_id = client_id

        # Trading parameters
        self.stock_symbol = stock_symbol
        self.exchange = 'SMART'
        self.currency = 'USD'
        self.upper_bound = upper
        self.lower_bound = lower
        self.buy_qty = buy_qty

        # Runtime state
        self.held_qty = 0
        self.in_position = False
        self.active_order = None
        self.fill_tracker = {}         # order_id -> filled quantity
        self.last_buy_price = None
        self.running_loss = 0.0

        # IB bookkeeping
        self.next_order_id = None
        self.open_orders_loaded = False
        self.positions_loaded = False
        self.logged_position = False

        # Prepare contract
        self.contract = self.create_contract(
            self.stock_symbol, self.exchange, self.currency
        )

        # Setup logging
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

        # CSV setup
        self.csv_file = f"logs/ibapp_log_{start_time_utc}.csv"
        with open(self.csv_file, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                "timestamp", "event", "action", "price", "quantity", "running_loss", "note"
            ])

        # Initial log
        self.logger.info("Trading Bot Configuration:")
        self.logger.info(f"  Stock Symbol     : {self.stock_symbol}")
        self.logger.info(f"  Upper Threshold  : {self.upper_bound}")
        self.logger.info(f"  Lower Threshold  : {self.lower_bound}")
        self.logger.info(f"  Buy Quantity     : {self.buy_qty}")

    def send_notification(self, text: str):
        if not self.webhook_url:
            return
        payload = {"content": text}
        try:
            requests.post(self.webhook_url, json=payload, timeout=2)
        except Exception:
            pass

    def create_contract(self, symbol, exchange, currency):
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = exchange
        contract.currency = currency
        return contract

    def connect_and_run(self):
        self.connect(self.host, self.port, self.client_id)
        thread = Thread(target=self.run, daemon=True)
        thread.start()
        while self.next_order_id is None:
            time.sleep(0.1)
        self.reqOpenOrders()
        self.reqPositions()
        self.reqTickByTickData(1, self.contract, "AllLast", 0, False)

    def log_trade_event(self, event_type, action, price=None, qty=0, note="", loss=None):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_note = note.replace(",", ";")
        with open(self.csv_file, "a", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                now_str,
                event_type,
                action,
                f"{price:.2f}" if price is not None else "",
                qty,
                f"{self.running_loss:.2f}",
                safe_note
            ])
        msg = f"[{event_type}] {action} {qty}@{price:.2f} | running loss: {self.running_loss:.2f} | {safe_note}"
        self.send_notification(msg)

    def initialize_orders_if_ready(self):
        if not (self.open_orders_loaded and self.positions_loaded):
            return
        self.logger.info("Initialization complete. Evaluating current position...")
        self.logger.info(f"Detected {self.held_qty} shares of {self.stock_symbol} in account.")
        if self.held_qty >= self.buy_qty:
            self.in_position = True
            self.logger.info("Position fully held. Placing SELL stop order.")
            self.place_sell_order()
        elif self.held_qty == 0:
            self.in_position = False
            self.logger.info("No position held. Placing BUY stop order.")
            self.place_buy_order()
        else:
            self.in_position = True
            self.logger.warning(
                f"Partial position detected: {self.held_qty}/{self.buy_qty} shares.")
            self.logger.info("Placing SELL stop order for remaining shares.")
            self.place_sell_order(for_qty=self.held_qty)

    # IB Callbacks
    def nextValidId(self, order_id: int):
        self.next_order_id = order_id
        self.logger.info(f"Next valid order ID: {order_id}")
        self.reqOpenOrders()
        self.reqPositions()

    def tickByTickAllLast(
        self, reqId, tickType, timestamp, price,
        size, tickAttribLast, exchange, specialConditions
    ):
        self.logger.info(f"Tick: {price} at {timestamp}")

    def openOrder(self, order_id, contract, order, order_state):
        if contract.symbol == self.stock_symbol and order.orderType == "STP":
            order.orderId = order_id
            self.active_order = order
            self.logger.info(f"Found open order: {order_id} Action: {order.action}")

    def openOrderEnd(self):
        self.open_orders_loaded = True
        self.logger.info("Open orders loaded.")
        self.initialize_orders_if_ready()

    def position(self, account, contract, pos, avgCost):
        if contract.symbol == self.stock_symbol:
            self.held_qty = int(pos)
            self.logger.info(f"Position callback: {self.held_qty} shares.")

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
            if action == "BUY":
                self.last_buy_price = executed_price
                self.log_trade_event(
                    "Executed", action, price=executed_price,
                    qty=fill_qty,
                    note=f"Partial BUY fill at {executed_price:.2f}; total filled: {filled_total}/{self.buy_qty}"
                )
            elif action == "SELL":
                loss = (
                    (self.last_buy_price - executed_price) * fill_qty
                    if self.last_buy_price is not None else 0.0
                )
                self.running_loss += loss
                self.log_trade_event(
                    "Executed", action, price=executed_price,
                    qty=fill_qty,
                    note=f"Partial SELL at {executed_price:.2f}; loss: {loss:.2f}; running: {self.running_loss:.2f}",
                    loss=loss
                )
            if filled_total >= self.buy_qty:
                self.logger.info(f"Order {order_id} fully filled.")
                if action == "BUY":
                    self.active_order = None
                    self.in_position = True
                    self.place_sell_order()
                elif action == "SELL":
                    self.active_order = None
                    self.in_position = False
                    self.place_buy_order()
                del self.fill_tracker[order_id]

    # Order placement
    def place_buy_order(self):
        if self.active_order is None:
            order = Order()
            order.action = "BUY"
            order.orderType = "STP"
            order.totalQuantity = self.buy_qty
            order.auxPrice = self.upper_bound
            order.orderId = self.next_order_id
            order.orderRef = "AutoBot"
            self.next_order_id += 1
            self.placeOrder(order.orderId, self.contract, order)
            self.active_order = order
            self.log_trade_event(
                "Placed", "BUY",
                price=order.auxPrice,
                qty=order.totalQuantity,
                note="Stop loss BUY order placed."
            )
            self.logger.info(
                f"Placed BUY stop order (ID: {order.orderId}) at {order.auxPrice}"
            )

    def place_sell_order(self, for_qty=None):
        if self.active_order is None:
            qty = for_qty if for_qty is not None else self.buy_qty
            order = Order()
            order.action = "SELL"
            order.orderType = "STP"
            order.totalQuantity = qty
            order.auxPrice = self.lower_bound
            order.orderId = self.next_order_id
            order.orderRef = "AutoBot"
            self.next_order_id += 1
            self.placeOrder(order.orderId, self.contract, order)
            self.active_order = order
            self.log_trade_event(
                "Placed", "SELL",
                price=order.auxPrice,
                qty=qty,
                note="Stop loss SELL order placed."
            )
            self.logger.info(
                f"Placed SELL stop order (ID: {order.orderId}) for {qty} shares at {order.auxPrice}"
            )

    def run_bot(self):
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("KeyboardInterrupt caught in run_bot. Exiting...")
            self.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the trading bot.")
    parser.add_argument("--stock-symbol", type=str, required=True, help="Ticker symbol (e.g. TSLA)")
    parser.add_argument("--upper", type=float, required=True, help="Upper threshold for stop loss buy order")
    parser.add_argument("--lower", type=float, required=True, help="Lower threshold for stop loss sell order")
    parser.add_argument("--buy-qty", type=int, required=True, help="Buy quantity for each order")
    args = parser.parse_args()

    if args.upper <= args.lower:
        parser.error("Upper threshold must be greater than lower threshold.")
        exit()

    client = IBClient( stock_symbol=args.stock_symbol, upper=args.upper, lower=args.lower, buy_qty=args.buy_qty)
    client.connect_and_run()
    client.run_bot()
