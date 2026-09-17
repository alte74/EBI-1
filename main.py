import argparse

import uvicorn

from sentient_ai.chat import run_chat


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentient AI")
    parser.add_argument("--cli", action="store_true", help="Terminal chat instead of the web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    if args.cli:
        run_chat()
        return
    uvicorn.run("sentient_ai.server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
