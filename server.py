"""Run the original server in an independently owned process."""
from re7_21 import GameState, server_worker

if __name__ == '__main__':
    # Original executables serialize this class as __main__.GameState.
    GameState.__module__ = '__main__'
    server_worker()
