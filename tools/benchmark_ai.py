"""Seeded matches through the real command layer; no hidden information in strategies."""
import json
from pathlib import Path
import random
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bot import Strategy, observe
from match import Match

def run(games=60, opponent=Strategy, opponent_difficulty='normal'):
    stats={'games':games,'hard_wins':0,'normal_wins':0,'unfinished':0,'gambler_hits':0,'gambler_busts':0,'cautious_hits':0,'cautious_busts':0}
    for seed in range(games):
        random.seed(seed)
        now=[100000.]
        match=Match(dict(settlement_seconds=0),monotonic=lambda:now[0],wall=lambda:now[0])
        hard_pid=1+seed%2
        bots={pid:(Strategy if pid==hard_pid else opponent)('hard' if pid==hard_pid else opponent_difficulty,'gambler' if seed%3==0 else 'conservative',seed*2+pid) for pid in (1,2)}
        extra={1:0,2:0}
        for step in range(4000):
            now[0]+=1
            gs=match.gs
            match.tick()
            if gs.phase=='GAMEOVER':
                stats['hard_wins' if gs.round_winner==hard_pid else 'normal_wins']+=1
                break
            if gs.phase!='ACTION': continue
            pid=gs.turn
            command=bots[pid].choose(observe(gs,pid),extra[pid])
            accepted=match.command(pid,f'{command}:{gs.round_id}')
            if not accepted: raise AssertionError((seed,step,command))
            if command=='HIT':
                key='gambler' if bots[pid].mood=='gambler' else 'cautious'
                stats[key+'_hits']+=1
                stats[key+'_busts']+=int(sum(getattr(gs,f'p{pid}_hand'))>gs.target_score)
            if command in ('HIT','STAY') or gs.phase!='ACTION': extra={1:0,2:0}
            else: extra[pid]+=1
            numbers=gs.p1_hand+gs.p2_hand+gs.deck
            assert len(numbers)==len(set(numbers)), (seed,step,'duplicate card')
        else: stats['unfinished']+=1
    return stats

if __name__=='__main__': print(json.dumps(run(),indent=2))
