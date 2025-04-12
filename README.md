# trading-bot

A Python-based trading bot leveraging Interactive Brokers' TWS API via the `ib_insync` library. Video instructions for setting up environment: https://www.youtube.com/watch?v=ZEtsLuXdC-g.

## Installation

**Install and Configure TWS/Gateway:**
1. Open the following link: https://www.interactivebrokers.com/campus/ibkr-quant-news/interactive-brokers-python-api-native-a-step-by-step-guide/
2. Follow the steps starting at **Download your IB client (TWS or IB Gateway)** to install IB Trader Work Station (TWS)
3. Make sure you sign into paper trading account

**Open Terminal:**
1. Click cmd+space to open Spotlight Search
2. Search for Terminal and click enter

**Clone the Repository:**
```bash
git clone https://github.com/nconn711/trading-bot.git
cd trading-bot
```

**Set Up a Virtual Environment:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Install Dependencies:**
```bash
pip3 install setuptools
cd IBJts/source/pythonclient
python3 setup.py install
cd ../../../
```

## Usage

**TWS/Gateway:**

- Ensure that Interactive Brokers' Trader Workstation (TWS) or IB Gateway is running.

**Run the Bot:**

```bash
python3 IBApp.py --stock-symbol TSLA --upper 200.0 --lower 180.0 --buy-qty 10
```

**Arguments:**

- `--stock-symbol`: Ticker symbol (e.g. TSLA, AAPL)
- `--upper`: Upper threshold for triggering a stop loss **buy** order
- `--lower`: Lower threshold for triggering a stop loss **sell** order
- `--buy-qty`: Number of shares to buy per order

> **Note:** The `--upper` value must be greater than the `--lower` value.


## File Structure

- `IBApp.py`: Main application script initializing the IB connection and managing trading logic.
