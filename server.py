"""Run the safe JSON session server in an independently owned process."""
import argparse
import json
from pathlib import Path
from re7_21 import GameState
from session_server import server_worker
from match import load_timer

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--timer')
    args = parser.parse_args()
    server_worker(json.loads(args.timer) if args.timer else load_timer(Path(__file__).resolve().parent))
