from app_paths import config_path as player_config, presets_path, data_path, sounds_path
"""Local, optional sound feedback. No changes to the game protocol or rules."""
import json
import math
from pathlib import Path
import time

import pygame

# category, priority; higher-priority transitions suppress incidental sounds in a burst.
EVENTS = {
    'ui_click': ('ui', 1), 'card_select': ('ui', 2),
    'connected': ('game', 3), 'round_start': ('game', 6),
    'card_draw': ('game', 3), 'card_move': ('game', 3),
    'trump_play': ('game', 4), 'trump_change': ('game', 3),
    'card_discard': ('game', 4), 'stay': ('game', 3),
    'your_turn': ('game', 5), 'bust': ('game', 8),
    'damage': ('game', 7), 'error': ('ui', 9),
    'round_win': ('result', 10), 'round_loss': ('result', 10), 'round_draw': ('result', 10),
    'game_win': ('result', 12), 'game_loss': ('result', 12), 'game_draw': ('result', 12),
}


def volume(value, default):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return min(1., max(0., float(value)))


class SoundManager:
    def __init__(self, root):
        self.root = Path(root)
        self.enabled = True
        self.available = False
        self.master = .65
        self.levels = {'ui': .45, 'game': .75, 'result': .8}
        self.sounds = {}
        self.last_played = {}
        self.issues = []
        config = {}
        try:
            with (player_config(self.root,'audio.json')).open(encoding='utf-8-sig') as stream:
                config = json.load(stream)
            if not isinstance(config, dict):
                raise ValueError('audio.json must be an object')
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            self.issues.append(str(exc))
            config = {}
        self.enabled = config.get('enabled', True) is not False
        self.master = volume(config.get('master_volume'), self.master)
        for category in self.levels:
            self.levels[category] = volume(config.get(category+'_volume'), self.levels[category])
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            pygame.mixer.set_num_channels(max(8, pygame.mixer.get_num_channels()))
            self.available = True
        except pygame.error as exc:
            self.issues.append(str(exc))
            return
        entries = config.get('events', {})
        if not isinstance(entries, dict):
            entries = {}
        for event, (category, _) in EVENTS.items():
            entry = entries.get(event, {})
            if entry is False:
                continue
            if not isinstance(entry, dict):
                entry = {}
            if entry.get('enabled', True) is False:
                continue
            path = entry.get('file', f'sounds/{event}.wav')
            if not isinstance(path, str) or not path.strip():
                continue
            try:
                effect = pygame.mixer.Sound(str(player_config(self.root,'audio.json').parent/path))
                effect.set_volume(self.master*self.levels[category]*volume(entry.get('volume'), 1.))
                self.sounds[event] = effect
            except (pygame.error, OSError, ValueError) as exc:
                # One bad/missing custom asset must never stop a game.
                self.issues.append(f'{event}: {exc}')

    def toggle(self):
        self.enabled = not self.enabled
        if not self.enabled and self.available:
            pygame.mixer.stop()

    def play(self, event):
        if not self.enabled or not self.available or event not in self.sounds:
            return False
        now = time.monotonic()
        if now-self.last_played.get(event, -100) < .12:
            return False
        category, _ = EVENTS[event]
        # Separate lanes keep menu ticks from interrupting a result melody.
        lane = 3 if event == 'damage' else {'ui': 0, 'game': 1, 'result': 2}[category]
        try:
            pygame.mixer.Channel(lane).play(self.sounds[event])
            self.last_played[event] = now
            return True
        except pygame.error:
            self.available = False
            return False

    def play_many(self, events):
        unique = set(events) & EVENTS.keys()
        if not unique:
            return
        primary = max(unique, key=lambda event: (EVENTS[event][1], event))
        self.play(primary)
        # A short impact may accompany a result, but avoid a cascade of stale cues.
        if 'damage' in unique and primary != 'damage':
            self.play('damage')


class SoundTracker:
    """Infer transitions once from immutable public snapshots, not from render frames."""
    def __init__(self):
        self.previous = None
        self.pending = None

    def reset(self):
        self.previous = None
        self.pending = None

    def sent(self, command, state, pid):
        if command != 'REMATCH':
            self.pending = (command.split(':')[0], state.round_id, state.last_action_time[pid])

    def update(self, state, pid):
        other = 3-pid
        other_hand = getattr(state, f'p{other}_hand')
        snapshot = {
            'round': state.round_id, 'phase': state.phase, 'turn': state.turn,
            'hand': tuple(getattr(state, f'p{pid}_hand')),
            # Deliberately exclude the opponent's hidden-card value.
            'other_hand': (None,)+tuple(other_hand[1:]) if other_hand else (),
            'trumps': tuple(getattr(state, f'p{pid}_trumps')),
            'other_count': len(getattr(state, f'p{other}_trumps')),
            'hp': getattr(state, f'p{pid}_fingers'),
            'health': (state.p1_fingers, state.p2_fingers),
            'match': getattr(state, 'match_id', None),
            'stops': (state.p1_stop, state.p2_stop),
            'bust': sum(getattr(state, f'p{pid}_hand')) > state.target_score,
        }
        previous, self.previous = self.previous, snapshot
        if previous == snapshot:
            return []
        damaged = bool(previous and previous['match'] == snapshot['match'] and getattr(state, 'end_reason', '') not in ('timeout','preparation_timeout') and any(now < old for now, old in zip(snapshot['health'], previous['health'])))
        if snapshot['phase'] == 'GAMEOVER':
            self.pending = None
            if previous and previous['phase'] == 'GAMEOVER':
                return []
            result = 'draw' if state.round_winner == 0 else 'win' if state.round_winner == pid else 'loss'
            return (['damage'] if damaged else [])+['game_'+result]
        if snapshot['phase'] == 'RESULT':
            self.pending = None
            if previous and previous['round'] == snapshot['round'] and previous['phase'] == 'RESULT':
                return []
            result = 'draw' if state.round_winner == 0 else 'win' if state.round_winner == pid else 'loss'
            return (['damage'] if damaged else [])+['round_'+result]
        if previous is None or previous['round'] != snapshot['round'] or previous['phase'] != 'ACTION':
            self.pending = None
            return (['damage'] if damaged else [])+['round_start']
        events = []
        acknowledged = None
        if self.pending:
            command, round_id, last_action = self.pending
            if round_id == state.round_id and state.last_action_time[pid] > last_action:
                acknowledged = command
                self.pending = None
        if acknowledged in ('TRUMP', 'DISCARD'):
            events.append('trump_play' if acknowledged == 'TRUMP' else 'card_discard')
        for hand in ('hand', 'other_hand'):
            if snapshot[hand] != previous[hand]:
                events.append('card_draw' if len(snapshot[hand]) > len(previous[hand]) else 'card_move')
        if snapshot['trumps'] != previous['trumps'] or snapshot['other_count'] != previous['other_count']:
            if acknowledged not in ('TRUMP', 'DISCARD'):
                events.append('trump_change')
        if any(now and not old for now, old in zip(snapshot['stops'], previous['stops'])):
            events.append('stay')
        if not previous['bust'] and snapshot['bust']:
            events.append('bust')
        if damaged:
            events.append('damage')
        if previous['turn'] != pid and snapshot['turn'] == pid:
            events.append('your_turn')
        return events
