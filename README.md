# trading-bot

A Python-based trading bot leveraging Interactive Brokers' TWS API via the `ib_insync` library. Video instructions for setting up environment: https://www.youtube.com/watch?v=ZEtsLuXdC-g.

## Installation

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

**Configure TWS/Gateway:**

- Ensure that Interactive Brokers' Trader Workstation (TWS) or IB Gateway is running.
- Enable API access in the settings.

**Run the Bot:**
```bash
python3 IBApp.py
```

## File Structure

- `IBApp.py`: Main application script initializing the IB connection and managing trading logic.
