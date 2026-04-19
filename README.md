# TradingBotMiroFish

> Multi-agent market intelligence system for SPY and NVDA option signals.

## 👉 Project is in [`/market-swarm-lab`](./market-swarm-lab)

| Resource | Link |
|---|---|
| 🌐 **Architecture Diagram** | [lakshmanb4u.github.io/TradingBotMiroFish](https://lakshmanb4u.github.io/TradingBotMiroFish/) |
| 📄 **Technical Design Doc** | [TECHNICAL_DESIGN.md](./market-swarm-lab/docs/TECHNICAL_DESIGN.md) |
| 🏛 **Architecture Docs** | [ARCHITECTURE.md](./market-swarm-lab/docs/ARCHITECTURE.md) |
| 📖 **Project README** | [market-swarm-lab/README.md](./market-swarm-lab/README.md) |

## What It Does

Collects live data from 5 sources → runs 100 AI agents → detects signal divergence → generates CALL/PUT/HOLD option signals with confidence, position sizing, and full audit trail.

**Sources:** Alpha Vantage · Apify/Reddit · NewsAPI · Kalshi · SEC/EDGAR  
**Stack:** Python 3.11 · FastAPI · TimesFM 2.5 · pandas · Docker  
**Signals:** SPY · NVDA · Paper trading by default
