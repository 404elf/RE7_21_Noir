"""Reproducible command-layer matches, alternating seats, with no secret observations."""
import argparse
import json
from pathlib import Path
import random
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bot import Strategy,observe
from match import Match


def run(games=20,left='nightmare',right='hard',style='swing',rival=Strategy):
    stats=dict(games=games,left=left,right=right,style=style,wins=0,losses=0,draws=0,unfinished=0,invalid=0,decisions=0,max_decision_ms=0)
    for seed in range(games):
        random.seed(seed+77000)
        now=[100000.]
        m=Match({'settlement_seconds':0},monotonic=lambda:now[0],wall=lambda:now[0])
        seat=1+seed%2
        bots={p:(Strategy if p==seat else rival)(left if p==seat else right,style,seed*2+p) for p in (1,2)}
        extra={1:0,2:0}
        for step in range(2500):
            now[0]+=1;m.tick();gs=m.gs
            if gs.phase=='GAMEOVER':
                stats['wins' if gs.round_winner==seat else 'draws' if gs.round_winner==0 else 'losses']+=1;break
            if gs.phase!='ACTION':continue
            pid=gs.turn;start=time.perf_counter()
            action=bots[pid].choose(observe(gs,pid),extra[pid])
            stats['max_decision_ms']=max(stats['max_decision_ms'],round((time.perf_counter()-start)*1000,2))
            stats['decisions']+=1
            if not m.command(pid,f'{action}:{gs.round_id}'):
                stats['invalid']+=1
                raise AssertionError((seed,step,action))
            numbers=gs.p1_hand+gs.p2_hand+gs.deck
            assert len(numbers)==len(set(numbers)),(seed,step,'duplicate')
            if action in ('HIT','STAY') or gs.phase!='ACTION':extra={1:0,2:0}
            else:extra[pid]+=1
        else:stats['unfinished']+=1
    return stats

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--games',type=int,default=20)
    parser.add_argument('--previous',type=Path,help='Verified source archive containing the previous bot.py')
    parser.add_argument('--only-previous',action='store_true',help='Compare both updated difficulties to their previous implementations')
    args=parser.parse_args()
    if args.previous:
        import types,zipfile
        previous=types.ModuleType('previous_bot');sys.modules[previous.__name__]=previous
        with zipfile.ZipFile(args.previous) as archive:
            source=archive.read('bot.py').decode('utf-8')
            if 'tactics.py' in archive.namelist():
                tactics=types.ModuleType('previous_tactics');sys.modules[tactics.__name__]=tactics
                exec(compile(archive.read('tactics.py'),'previous_tactics.py','exec'),tactics.__dict__)
                source=source.replace('from tactics import Planner','from previous_tactics import Planner')
            exec(compile(source,'previous_bot.py','exec'),previous.__dict__)
        for difficulty in (('hard','nightmare') if args.only_previous else ('hard',)):
            for style in (('swing',) if difficulty=='nightmare' else ('conservative','gambler','swing')):
                print(json.dumps(run(args.games,left=difficulty,right=difficulty,style=style,rival=previous.Strategy)),flush=True)
    if not args.only_previous:
        for style in ('conservative','gambler','swing'):
            print(json.dumps(run(args.games,style=style)),flush=True)
