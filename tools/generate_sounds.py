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


def basement(length, frequency, seed, level):
    rng = random.Random(seed)
    values, low = [], 0.
    for i in range(int(length*RATE)):
        t = i/RATE
        noise = rng.uniform(-1, 1)
        low = .88*low+.12*noise
        attack = min(1., t/.003)
        tail = max(0., 1-t/length)**1.7
        body = math.sin(2*math.pi*frequency*t-2*t*t)*math.exp(-4*t)
        metal = sum(math.sin(2*math.pi*frequency*r*t)*math.exp(-d*t)
                    for r, d in ((2.73, 14), (4.19, 23), (6.37, 31)))/3
        grit = .55*low+.16*noise*math.exp(-38*t)
        values.append(level*attack*tail*(.62*body+.26*metal+grit))
    # Short, decaying room reflections preserve the initial tactile impact.
    dry = list(values)
    for delay, gain in ((.043, .16), (.079, .09)):
        offset = int(delay*RATE)
        for i in range(offset, len(values)):
            values[i] += dry[i-offset]*gain
    return values


def generate(directory):
    directory.mkdir(parents=True, exist_ok=True)
    # Inharmonic metal, worn paper and damped bass; no bright UI melodies.
    specs = {
        'ui_click': (.07, 185, 1, .35), 'card_select': (.12, 110, 2, .25),
        'connected': (.45, 78, 3, .30), 'round_start': (.70, 51, 4, .45),
        'trump_play': (.42, 72, 5, .55), 'trump_change': (.21, 140, 6, .30),
        'stay': (.17, 100, 7, .48), 'your_turn': (.32, 165, 8, .24),
        'bust': (.58, 43, 9, .48), 'damage': (.38, 46, 10, .65),
        'error': (.22, 94, 11, .28), 'round_win': (.64, 89, 12, .33),
        'round_loss': (.72, 39, 13, .43), 'round_draw': (.46, 66, 14, .3),
        'game_win': (1.3, 73, 15, .40), 'game_loss': (1.5, 32, 16, .48),
        'game_draw': (1., 58, 17, .32),
    }
    effects = {name: basement(*spec) for name, spec in specs.items()}
    effects.update(card_draw=swipe(.23, 31, level=.22),
                   card_move=swipe(.18, 32, -1, .20),
                   card_discard=swipe(.30, 33, -1, .27))
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
