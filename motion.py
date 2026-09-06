"""Short non-blocking card motions. Only confirmed public state drives effects."""
import math
import time
import pygame as pg


class CardMotion:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.items = []

    def add(self, sprite, start, end, style='deal', key=None, delay=0, back=None):
        self.items.append(dict(sprite=sprite, start=start, end=end, style=style,
                               key=key, began=self.clock()+delay, duration=.44 if style == 'flip' else .38,
                               back=back))
        self.items = self.items[-32:]

    def prune(self):
        now = self.clock()
        self.items = [item for item in self.items if now < item['began']+item['duration']]

    def hides(self, key):
        return any(item['key'] == key for item in self.items)

    def draw(self, canvas):
        now = self.clock()
        for item in self.items:
            p = max(0, min(1, (now-item['began'])/item['duration']))
            if now < item['began']:
                continue
            ease = 1-(1-p)**3
            x, y = [a+(b-a)*ease for a,b in zip(item['start'],item['end'])]
            sprite = item['sprite']
            if item['style'] == 'flip':
                sprite = item['back'] if p < .5 and item['back'] is not None else sprite
                sprite = pg.transform.smoothscale(sprite, (max(1,int(sprite.get_width()*abs(math.cos(p*math.pi)))),sprite.get_height()))
                x += (item['sprite'].get_width()-sprite.get_width())/2
            else:
                y -= math.sin(p*math.pi)*28
                if item['style'] in ('play', 'discard'):
                    sprite = sprite.copy()
                    sprite.set_alpha(int(255*(1-max(0,(p-.55)/.45))))
                    if item['style'] == 'discard':
                        sprite = pg.transform.rotate(sprite, -18*p)
            canvas.blit(sprite,(int(x),int(y)))
