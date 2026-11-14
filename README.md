# Quant

## News API setup

This project includes a free-tier news provider adapter using Finnhub.

1. Copy the example environment file:
   - `copy .env.example .env`
2. Add your key:
   - `FINNHUB_API_KEY=your_key_here`
3. Start the app and the default provider will be registered automatically.

If no key is configured, the system falls back to local fixture sources and keeps the rest of the pipeline running without crashing.
