"""Versioned, bounded JSON transport. Never instantiate network-supplied classes."""
import json
import math
import socket
import struct

MAGIC=b'R21J'
VERSION=3
MAX_FRAME=262144
FIELDS=set('deck p1_hand p2_hand p1_trumps p2_trumps active_trumps max_hp_limit p1_fingers p2_fingers target_score round_starter turn phase round_id round_winner round_damage result_timer p1_stop p2_stop p1_req_rematch p2_req_rematch last_action_time is_escape_end INSTANT_TYPES last_result end_reason draw_offer blood_loss enabled_cards clock_config clock_remaining clock_active action_log match_id network_paused'.split())
REQUIRED=set('deck p1_hand p2_hand p1_trumps p2_trumps active_trumps max_hp_limit p1_fingers p2_fingers target_score round_starter turn phase round_id round_winner round_damage result_timer p1_stop p2_stop p1_req_rematch p2_req_rematch last_action_time is_escape_end INSTANT_TYPES'.split())
FIELDS.add('opening_cards')
FIELDS.update(('preparation_remaining','preparation_active'))

def bounded(value, depth=0, budget=None):
    if budget is None: budget=[20000]
    budget[0]-=1
    if depth>12 or budget[0]<0: raise ValueError('structure_limit')
    if value is None or type(value) is bool: return
    if type(value) in (int,float):
        if not math.isfinite(value) or abs(value)>1e12: raise ValueError('number_limit')
    elif isinstance(value,str):
        if len(value)>2048: raise ValueError('text_limit')
    elif isinstance(value,(list,tuple)):
        if len(value)>1024: raise ValueError('array_limit')
        for item in value: bounded(item,depth+1,budget)
    elif isinstance(value,dict):
        if len(value)>128: raise ValueError('object_limit')
        for key,item in value.items():
            if not isinstance(key,(str,int)) or len(str(key))>80: raise ValueError('key_limit')
            bounded(item,depth+1,budget)
    else: raise ValueError('invalid_type')

def pairs(items):
    out={}
    for key,value in items:
        if key in out: raise ValueError('duplicate_key')
        out[key]=value
    return out

def state_from(data):
    from re7_21 import GameState
    if not isinstance(data,dict) or not REQUIRED<=data.keys() or data.keys()-FIELDS:
        raise ValueError('state_fields')
    if data['phase'] not in ('ACTION','RESULT','GAMEOVER') or data['turn'] not in (1,2): raise ValueError('state_phase')
    for key in ('deck','p1_hand','p2_hand'):
        if not isinstance(data[key],list) or len(data[key])>100 or any(type(v) is not int or not 1<=v<=100 for v in data[key]): raise ValueError('number_cards')
    for key in ('p1_trumps','p2_trumps'):
        if not isinstance(data[key],list) or len(data[key])>101: raise ValueError('trumps')
        for card in data[key]:
            if not isinstance(card,list) or len(card)!=3 or not all(isinstance(s,str) for s in card[:2]) or type(card[2]) is not int: raise ValueError('trump')
        data[key]=[tuple(card) for card in data[key]]
    for key in ('last_action_time','blood_loss','clock_remaining','preparation_remaining'):
        if key in data:
            if not isinstance(data[key],dict) or set(data[key])!={'1','2'} or any(type(v) not in (int,float) and not (v is None and key in ('clock_remaining','preparation_remaining')) for v in data[key].values()): raise ValueError('player_map')
            data[key]={int(k):v for k,v in data[key].items()}
    for key in ('max_hp_limit','target_score','p1_fingers','p2_fingers','round_starter','round_id','round_winner','round_damage'):
        if type(data[key]) is not int or not -10000<=data[key]<=1000000: raise ValueError('state_number')
    if data['round_starter'] not in (1,2) or data['round_winner'] not in (0,1,2) or data['round_id']<1 or data['round_damage']<0: raise ValueError('round_indices')
    if data['phase'] in ('RESULT','GAMEOVER') and data['round_damage'] and not data['round_winner']: raise ValueError('damage_without_winner')
    if not 1<=data['target_score']<=999 or not 1<=data['max_hp_limit']<=999: raise ValueError('state_range')
    for key in ('p1_stop','p2_stop','p1_req_rematch','p2_req_rematch','is_escape_end'):
        if type(data[key]) is not bool: raise ValueError('state_bool')
    for key in ('active_trumps','INSTANT_TYPES','action_log'):
        if key in data and not isinstance(data[key],list): raise ValueError('state_list')
    if len(data.get('action_log',[]))>300: raise ValueError('log_limit')
    if len(data['active_trumps'])>202: raise ValueError('table_limit')
    for card in data['active_trumps']:
        if not isinstance(card,dict) or not {'name','owner','type','val'}<=card.keys() or type(card['owner']) is not int or card['owner'] not in (1,2) or type(card['val']) is not int or not isinstance(card['name'],str) or not isinstance(card['type'],str): raise ValueError('table_card')
        if set(card)-{'name','type','owner','val','counter'} or len(card['name'])>64 or len(card['type'])>64 or not 0<=card['val']<=10000: raise ValueError('table_card_fields')
        if 'counter' in card and (type(card['counter']) is not int or not 0<=card['counter']<=10000):raise ValueError('counter')
    if any(not isinstance(v,str) for v in data['INSTANT_TYPES']): raise ValueError('instant_types')
    if type(data['result_timer']) not in (int,float): raise ValueError('result_timer')
    if 'network_paused' in data and type(data['network_paused']) is not bool: raise ValueError('pause')
    if 'end_reason' in data and not isinstance(data['end_reason'],str): raise ValueError('end_reason')
    if 'match_id' in data and (not isinstance(data['match_id'],str) or len(data['match_id'])>64): raise ValueError('match_id')
    if 'enabled_cards' in data and (not isinstance(data['enabled_cards'],dict) or any(type(v) is not bool for v in data['enabled_cards'].values())): raise ValueError('enabled_cards')
    if 'clock_config' in data:
        from match import timer_config
        if not isinstance(data['clock_config'],dict): raise ValueError('clock_config')
        data['clock_config']=timer_config(data['clock_config'])
    for key in ('clock_active','draw_offer','preparation_active'):
        if key in data and (type(data[key]) is not int or data[key] not in (0,1,2)): raise ValueError('player_index')
    result=data.get('last_result')
    if result is not None:
        if not isinstance(result,dict) or not {'round','hands','target','winner','damage','escape'}<=result.keys(): raise ValueError('result')
        if any(type(result[k]) is not int for k in ('round','target','winner','damage')) or type(result['escape']) is not bool: raise ValueError('result_types')
        if result['winner'] not in (0,1,2) or result['round']<1 or not 1<=result['target']<=999 or result['damage']<0:raise ValueError('result_range')
        hands=result['hands']
        if not isinstance(hands,list) or len(hands)!=2 or any(not isinstance(h,list) or len(h)>100 or any(type(n) is not int or not 1<=n<=100 for n in h) for h in hands): raise ValueError('result_hands')
    event_names={'round_start','result','gameover','hit','stay','trump','discard','timeout','preparation_timeout','rematch','draw_offer','draw_decline'}
    event_fields={'id','round','event','pid','opening','target','winner','damage','totals','hands','hp','reason','automatic','drawn','card','kind','value','before','after','target_before','locked'}
    for event in data.get('action_log',[]):
        if not isinstance(event,dict) or not {'id','round','event','pid'}<=event.keys() or any(type(event[k]) is not int for k in ('id','round','pid')) or event['pid'] not in (0,1,2) or not isinstance(event['event'],str): raise ValueError('log')
        if event['event'] not in event_names or set(event)-event_fields or event['id']<1 or event['round']<1:raise ValueError('log_fields')
        if len(json.dumps(event,ensure_ascii=True))>8192:raise ValueError('log_entry_limit')
        if event['event'] in ('hit','stay','trump','discard','timeout','preparation_timeout','rematch','draw_offer','draw_decline') and event['pid'] not in (1,2):raise ValueError('log_actor')
        if event['event'] in ('result','gameover') and (type(event.get('winner')) is not int or event['winner'] not in (0,1,2)):raise ValueError('log_winner')
        if event['event']=='trump' and not isinstance(event.get('card'),str): raise ValueError('log_card')
        if event['event']=='result' and (not isinstance(event.get('totals'),list) or len(event['totals'])!=2 or any(type(n) is not int for n in event['totals']) or type(event.get('damage')) is not int): raise ValueError('log_result')
        if 'drawn' in event and (not isinstance(event['drawn'],list) or any(type(n) is not int for n in event['drawn'])): raise ValueError('log_draw')
        for key in ('hands','before','after'):
            if key in event:
                hands=event[key]
                if not isinstance(hands,list) or len(hands)!=2 or any(not isinstance(h,list) or len(h)>100 or any(type(n) is not int or not 1<=n<=100 for n in h) for h in hands): raise ValueError('log_hands')
        for key in ('target','target_before','value'):
            if key in event and type(event[key]) is not int: raise ValueError('log_number')
        if 'kind' in event and not isinstance(event['kind'],str): raise ValueError('log_kind')
        if 'locked' in event and type(event['locked']) is not bool: raise ValueError('log_lock')
        if 'opening' in event:
            h=event['opening']
            if not isinstance(h,list) or len(h)!=2 or any(not isinstance(a,list) or not a or a[0] is not None or any(type(n) is not int or not 1<=n<=100 for n in a[1:]) for a in h): raise ValueError('log_opening')
    if 'opening_cards' in data:
        opening=data['opening_cards']
        if not isinstance(opening,dict) or any(not k.isdigit() or not isinstance(v,list) or len(v)!=2 or any(type(n) is not int or not 1<=n<=100 for n in v) for k,v in opening.items()): raise ValueError('private_opening')
    # __new__ of our local known class; only allowlisted data fields are installed.
    state=object.__new__(GameState)
    state.__dict__.update(data)
    return state

def encode(value):
    from re7_21 import GameState
    kind='state' if isinstance(value,GameState) else 'message'
    data=vars(value) if kind=='state' else value
    bounded(data)
    raw=json.dumps(dict(v=VERSION,kind=kind,data=data),ensure_ascii=True,allow_nan=False,separators=(',',':')).encode('ascii')
    if not 0<len(raw)<=MAX_FRAME: raise ValueError('frame_limit')
    return MAGIC+struct.pack('>I',len(raw))+raw

def decode(raw):
    if not 0<len(raw)<=MAX_FRAME: raise ValueError('frame_limit')
    try: envelope=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    except (RecursionError,UnicodeError,json.JSONDecodeError) as exc: raise ValueError('invalid_json') from exc
    bounded(envelope)
    if not isinstance(envelope,dict) or set(envelope)!={'v','kind','data'} or envelope['v']!=VERSION: raise ValueError('incompatible_version')
    if envelope['kind']=='state': return state_from(envelope['data'])
    if envelope['kind']!='message' or not isinstance(envelope['data'],(str,dict)): raise ValueError('message_type')
    return envelope['data']

def extract(buffer, limit=MAX_FRAME):
    if len(buffer)<8: return None
    if buffer[:4]!=MAGIC: raise ValueError('incompatible_version')
    length=struct.unpack('>I',buffer[4:8])[0]
    if not 0<length<=limit: raise ValueError('frame_limit')
    if len(buffer)<length+8: return None
    raw=bytes(buffer[8:length+8]);del buffer[:length+8]
    return decode(raw)

def send_msg(sock,value):
    try: sock.sendall(encode(value));return True
    except (OSError,ValueError,TypeError,AttributeError): return False

def recv_msg(sock,limit=MAX_FRAME):
    def exact(size):
        out=bytearray()
        while len(out)<size:
            chunk=sock.recv(size-len(out))
            if not chunk: raise ConnectionError('closed')
            out.extend(chunk)
        return bytes(out)
    try:
        header=exact(8)
        if header[:4]!=MAGIC: raise ValueError('incompatible_version')
        size=struct.unpack('>I',header[4:])[0]
        if not 0<size<=limit: raise ValueError('frame_limit')
        return decode(exact(size))
    except (OSError,ValueError,TypeError,KeyError,RecursionError): return None
