"""
Convenience script to start the server.

Usage:
    python scripts/run_server.py
    python scripts/run_server.py --port 8080
    python scripts/run_server.py --dev    # development mode with auto-reload
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn
from config.settings import settings


def main():
    parser = argparse.ArgumentParser(description="Start Football Predictor server")
    parser.add_argument("--host", default=settings.APP_HOST)
    parser.add_argument("--port", type=int, default=settings.APP_PORT)
    parser.add_argument("--dev", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    print(f"\n⚽ Football Predictor")
    print(f"   Server: http://{args.host}:{args.port}")
    print(f"   Docs:   http://{args.host}:{args.port}/docs")
    print()

    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=args.dev,
        log_level="info",
    )


if __name__ == "__main__":
    main()
