from app_paths import config_path as player_config, presets_path, data_path, sounds_path
"""Append-only local public match history for replaying decisions and reporting bugs."""
from datetime import datetime
from collections import OrderedDict
import json
from pathlib import Path
import uuid
from cards import info, english_name


MAX_LOG_FILE=8*1024*1024
MAX_LOG_FOLDER=64*1024*1024
MAX_SEEN=4096
MAX_ENTRY=8192

class History:
    def __init__(self, root):
        self.path = data_path(root,'logs')/f'{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.jsonl'
        self.entries = []
        self.seen = OrderedDict()
        self.limited = False
        self.error = False

    def ingest(self, state, pid=1):
        match_id = getattr(state, 'match_id', 'legacy')
        new = []
        for entry in getattr(state, 'action_log', []):
            key = (match_id, entry['id'])
            if key in self.seen:
                continue
            self.seen[key]=None
            if len(self.seen)>MAX_SEEN:self.seen.popitem(last=False)
            entry = dict(entry, match=match_id, viewer=pid)
            if entry['event'] == 'round_start' and 'opening' in entry:
                entry['hands'] = [list(h) for h in entry.pop('opening')]
                private = getattr(state,'opening_cards',{}).get(str(entry['round']))
                if private and pid in (1,2): entry['hands'][pid-1][0] = private[pid-1]
            if len(json.dumps(entry,ensure_ascii=True))<=MAX_ENTRY:new.append(entry)
        if not new:
            return
        self.entries.extend(new)
        self.entries = self.entries[-2000:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.limited:return
            current=self.path.stat().st_size if self.path.exists() else 0
            total=sum(p.stat().st_size for p in self.path.parent.glob('*.jsonl') if p.is_file())
            with self.path.open('ab') as stream:
                for entry in new:
                    raw=(json.dumps(entry,ensure_ascii=False)+'\n').encode('utf-8')
                    if current+len(raw)>MAX_LOG_FILE or total+len(raw)>MAX_LOG_FOLDER:
                        self.limited=True
                        break
                    stream.write(raw);current+=len(raw);total+=len(raw)
        except OSError:
            self.error = True


def describe(entry, zh=True, viewer=1):
    viewer=entry.get('viewer',viewer)
    def who(pid):
        return ('你' if pid==viewer else '对手') if zh else ('You' if pid==viewer else 'Opponent')
    player=who(entry['pid'])
    event=entry['event']
    def hands_text(hands):
        return '；'.join(who(pid)+': '+' + '.join('?' if n is None else str(n) for n in hands[pid-1]) for pid in (viewer,3-viewer))
    if event=='round_start' and 'hands' in entry:
        return ('开局 · ' if zh else 'Opening · ')+hands_text(entry['hands'])+(' · 目标 ' if zh else ' · Target ')+str(entry.get('target',21))
    labels={
        'draw_offer': (f'{player} 申请平局',f'{player} offers a draw'),
        'draw_decline': (f'{player} 拒绝平局',f'{player} declines a draw'),
        'round_start': ('新一局开始','New round'),
        'hit': (f'{player} 抽牌',f'{player} draws'),
        'stay': (f'{player} 停牌',f'{player} stays'),
        'discard': (f'{player} 弃置一张王牌',f'{player} discards a trump'),
        'timeout': (f'{player} 时间耗尽',f'{player} runs out of time'),
        'preparation_timeout': (f'{player} 准备超时',f'{player} ran out of preparation time'),
        'rematch': (f'{player} 请求再来一局',f'{player} requests a rematch'),
    }
    if event=='hit' and entry.get('drawn'):
        return player+(' 抽到 ' if zh else ' draws ')+', '.join(map(str,entry['drawn']))
    if event=='trump':
        name=info(entry['card'])[0] if zh else english_name(entry['card'])
        text=player+(' 使用 ' if zh else ' plays ')+name
        before,after=entry.get('before'),entry.get('after')
        actor=entry['pid']-1
        if before and after and len(after[actor])>len(before[actor]):
            text+=(' · 抽到 ' if zh else ' · Drew ')+', '.join(map(str,after[actor][len(before[actor]):]))
        if entry.get('target_before')!=entry.get('target'):
            text+=(' · 目标 ' if zh else ' · Target ')+str(entry.get('target'))
        return text
    if event in ('result','gameover'):
        winner=entry.get('winner',0)
        result=('平局' if zh else 'Draw') if not winner else who(winner)+('获胜' if zh else ' wins')
        if event=='result':
            reveal=((' · 亮牌 ' if zh else ' · Reveal ') + hands_text(entry['hands'])) if 'hands' in entry else ''
            return result+reveal+(' · 伤害 ' if zh else ' · Damage ')+str(entry['damage'])
        return ('游戏结束 · ' if zh else 'Game over · ')+result
    return labels.get(event,(event,event))[0 if zh else 1]
