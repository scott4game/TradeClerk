# TradeClerk

An export-sales AI agent for RFQs, product catalog, quotations, trade terms, shipment tracking, and sales follow-up.

The source is public so customers can audit it. You may not offer this software as a competing hosted or SaaS product.

## Run

Python 3.10+. Without `OPENAI_API_KEY`, the server runs in mock mode (no model calls; APIs and tools still work).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn tradeclerk.app:app --host 0.0.0.0 --port 8080
```

Or:

```bash
ADDR=:8080 tradeclerk
```

Open http://127.0.0.1:8080. Optional environment variables: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL` (default `gpt-4o-mini`), `ADDR` (default `:8080`).

## License

[FSL-1.1-ALv2](LICENSE.md) (Functional Source License 1.1).

**Allowed:** view, modify, internal use, non-commercial education/research, and professional services for a licensee using the software under these terms.

**Not allowed:** offering this software (or substantially the same functionality) to others as a commercial product or hosted service.

A separate commercial license is required to run a competing hosted service.

Before publishing to GitHub, replace the copyright holder `TradeClerk` in `LICENSE.md` with your legal name or company name.
