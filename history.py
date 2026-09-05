"""Append-only local public match history for replaying decisions and reporting bugs."""
from datetime import datetime
import json
from pathlib import Path
import uuid
from cards import info


class History:
    def __init__(self, root):
        self.path = Path(root)/'logs'/f'{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.jsonl'
        self.entries = []
        self.seen = set()
        self.error = False

    def ingest(self, state):
        match_id = getattr(state, 'match_id', 'legacy')
        new = []
        for entry in getattr(state, 'action_log', []):
            key = (match_id, entry['id'])
            if key in self.seen:
                continue
            self.seen.add(key)
            new.append(dict(entry, match=match_id))
        if not new:
            return
        self.entries.extend(new)
        self.entries = self.entries[-2000:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8') as stream:
                for entry in new:
                    stream.write(json.dumps(entry, ensure_ascii=False)+'\n')
        except OSError:
            self.error = True


def describe(entry, zh=True):
    player = f'玩家 {entry["pid"]}' if zh else f'Player {entry["pid"]}'
    event = entry['event']
    labels = {
        'round_start': ('新一局开始', 'New round'), 'hit': (f'{player} 抽牌', f'{player} draws'),
        'stay': (f'{player} 停牌', f'{player} stays'), 'discard': (f'{player} 弃置一张王牌', f'{player} discards a trump'),
        'timeout': (f'{player} 时间耗尽', f'{player} runs out of time'),
        'rematch': (f'{player} 请求再来一局', f'{player} requests a rematch'),
    }
    if event == 'trump':
        name = info(entry['card'])[0] if zh else entry['card']
        return f'{player} '+('使用 ' if zh else 'plays ')+name
    if event in ('result', 'gameover'):
        winner = entry.get('winner', 0)
        result = ('平局' if zh else 'Draw') if not winner else (f'玩家 {winner} 获胜' if zh else f'Player {winner} wins')
        if event == 'result':
            return result+ (f' · 点数 {entry["totals"][0]} / {entry["totals"][1]} · 伤害 {entry["damage"]}' if zh else f' · Totals {entry["totals"][0]} / {entry["totals"][1]} · Damage {entry["damage"]}')
        return ('游戏结束 · ' if zh else 'Game over · ')+result
    return labels.get(event, (event, event))[0 if zh else 1]
