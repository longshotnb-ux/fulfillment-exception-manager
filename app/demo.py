"""Run a local synthetic-data demo that starts fresh on every launch."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from .main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    with TemporaryDirectory(prefix="fulfillment-demo-") as directory:
        application = create_app(database_path=Path(directory) / "demo.db")
        print("Synthetic demo: changes are temporary and reset when this process exits.")
        uvicorn.run(application, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
