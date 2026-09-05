"""Generate original, short, non-clipping PCM effects using only the standard library."""
import argparse
import array
import math
from pathlib import Path
import random
import sys
import wave

RATE = 44100


def tone(notes, length=.16, gap=.045, level=.38):
    samples = []
    for frequency in notes:
        for i in range(int(length*RATE)):
            t = i/RATE
            envelope = min(1, t/.008)*max(0, 1-t/length)**2
            value = math.sin(2*math.pi*frequency*t)+.2*math.sin(4*math.pi*frequency*t)
            samples.append(level*envelope*value)
        samples.extend([0.] * int(gap*RATE))
    return samples


def swipe(length, seed, direction=1, level=.32):
    rng = random.Random(seed)
    samples, previous = [], 0.
    for i in range(int(length*RATE)):
        t = i/RATE
        noise = rng.uniform(-1, 1)
        filtered = noise-previous
        previous = noise
        envelope = math.sin(math.pi*t/length)**2
        grain = .7+.3*math.sin(2*math.pi*32*t)
        samples.append(level*envelope*(filtered*.36*grain+.15*math.sin(2*math.pi*(240*t+direction*900*t*t))))
    return samples


def impact(length, frequency, seed):
    rng = random.Random(seed)
    return [(.48*math.sin(2*math.pi*frequency*(i/RATE))+.16*rng.uniform(-1, 1))*
            min(1, i/(RATE*.004))*math.exp(-22*i/RATE)*min(1, (length-i/RATE)/.015)
            for i in range(int(length*RATE))]


def generate(directory):
    directory.mkdir(parents=True, exist_ok=True)
    effects = {
        'ui_click': tone([720], .048, 0, .23),
        'card_select': tone([660, 880], .065, .012, .22),
        'connected': tone([440, 660], .14),
        'round_start': tone([220, 330, 440], .16),
        'card_draw': swipe(.2, 11),
        'card_move': swipe(.16, 12, -1),
        'trump_play': tone([220, 440, 660], .09, .015),
        'trump_change': tone([550, 660], .085, .015, .28),
        'card_discard': swipe(.26, 13, -1, .29),
        'stay': impact(.14, 155, 14),
        'your_turn': tone([440, 880], .13, .055, .29),
        'bust': tone([220, 185, 147], .16, .02, .36),
        'damage': impact(.28, 75, 15),
        'error': tone([233, 196], .14, .06, .28),
        'round_win': tone([440, 554.37, 659.25], .17),
        'round_loss': tone([329.63, 261.63, 220], .19),
        'round_draw': tone([392, 392], .18, .09, .3),
        'game_win': tone([330, 440, 554.37, 659.25, 880], .24, .055),
        'game_loss': tone([392, 329.63, 261.63, 196], .27, .05),
        'game_draw': tone([330, 440, 330], .26, .09, .3),
    }
    for name, values in effects.items():
        path = directory/(name+'.wav')
        if path.exists():
            raise FileExistsError(f'Preserve existing assets before regenerating: {path}')
        pcm = array.array('h', [int(max(-.95, min(.95, sample))*32767) for sample in values])
        if sys.byteorder != 'little':
            pcm.byteswap()
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(RATE)
            stream.writeframes(pcm.tobytes())
    print(f'Generated {len(effects)} original WAV effects in {directory}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1]/'sounds')
    generate(parser.parse_args().output)
