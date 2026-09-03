import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "artel.ledger.app:app",
        host=os.environ.get("LEDGER_HOST", "0.0.0.0"),
        port=int(os.environ.get("LEDGER_PORT", "8090")),
        log_level=os.environ.get("LEDGER_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
