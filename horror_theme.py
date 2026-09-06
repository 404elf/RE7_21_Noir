"""Deterministic procedural surfaces; never consumes the game's random stream."""
import random
import pygame as pg


class HorrorTheme:
    def __init__(self, size):
        self.cache = {}
        self.blood_key = None
        self.table_blood = pg.Surface((978, 479), pg.SRCALPHA)
        self.background = pg.Surface(size)
        width, height = size
        rng = random.Random(721)
        for y in range(height):
            light = int(10*(1-abs(y-height*.45)/height))
            pg.draw.line(self.background, (12+light, 9+light//2, 8+light//3), (0, y), (width, y))
        for _ in range(24000):
            x, y = rng.randrange(width), rng.randrange(height)
            color = rng.choice([(29, 24, 21), (6, 5, 5), (35, 26, 21)])
            self.background.set_at((x, y), color)
        for x in range(0, width, 180):
            pg.draw.line(self.background, (7, 6, 5), (x, 95), (x-35, height), 3)
            for _ in range(12):
                offset = rng.randrange(170)
                pg.draw.line(self.background, (30, 22, 18), (x+offset, 95), (x+offset-30, height), 1)
        self.blood(self.background, (18, height-35), 110, 9)
        self.blood(self.background, (width-16, 106), 83, 12)
        # Rusted chain at the far edge, away from interactive elements.
        for y in range(111, height-90, 27):
            pg.draw.ellipse(self.background, (70, 58, 43), (8, y, 14, 36), 2)
            pg.draw.ellipse(self.background, (15, 12, 10), (12, y+3, 9, 27), 1)
        self.scan = pg.Surface(size, pg.SRCALPHA)
        for y in range(0, height, 4):
            pg.draw.line(self.scan, (0, 0, 0, 13), (0, y), (width, y))

    @staticmethod
    def blood(surface, center, spread, seed):
        rng = random.Random(seed)
        for _ in range(55):
            x = int(rng.gauss(center[0], spread*.45))
            y = int(rng.gauss(center[1], spread*.30))
            radius = rng.choice([1, 2, 3, 5, 8, 14])
            pg.draw.circle(surface, rng.choice([(64, 15, 12), (80, 19, 15), (48, 13, 11)]), (x, y), radius)
            if radius > 8:
                pg.draw.line(surface, (57, 13, 11), (x, y), (x+2, y+rng.randint(15, 50)), 2)

    def accumulated_blood(self, losses, pid):
        # Cached transparent layer drawn below cards/text, with deterministic positions.
        key = (min(400, int(losses.get(pid, 0))), min(400, int(losses.get(3-pid, 0))))
        if self.blood_key != key:
            self.blood_key = key
            self.table_blood.fill((0, 0, 0, 0))
            self.table_blood.set_clip((194, 0, 585, 479))
            for side, amount in enumerate(key):
                rng = random.Random(72133+side)
                for point in range(amount):
                    center = (rng.randint(265, 705), rng.randint(280, 475) if side == 0 else rng.randint(10, 165))
                    self.blood(self.table_blood, center, rng.randint(24, 58), side*1000+point)
            self.table_blood.set_clip(None)
        return self.table_blood

    def panel(self, size, fill, border):
        key = (tuple(size), fill, border)
        if key in self.cache:
            return self.cache[key]
        width, height = size
        surface = pg.Surface(size)
        surface.fill(fill)
        rng = random.Random(width*311+height*17+sum(fill))
        light = sum(fill) > 420
        for _ in range(min(3400, width*height//45)):
            x, y = rng.randrange(width), rng.randrange(height)
            shade = rng.randrange(-20, 5) if light else rng.randrange(-5, 8)
            color = tuple(max(0, min(255, c+shade)) for c in fill)
            surface.set_at((x, y), color)
        for _ in range(max(1, width//60)):
            x, y = rng.randrange(width), rng.randrange(height)
            color = tuple(max(0, c-(25 if light else 5)) for c in fill)
            pg.draw.line(surface, color, (x, y), (min(width-1, x+rng.randrange(8, 60)), y+1))
        if border:
            pg.draw.rect(surface, border, (0, 0, width, height), 1)
            if width > 90 and height > 90 and not light:
                for x, y in ((7, 7), (width-8, 7), (7, height-8), (width-8, height-8)):
                    pg.draw.circle(surface, (75, 62, 49), (x, y), 2)
                    pg.draw.line(surface, (11, 9, 8), (x-1, y), (x+1, y))
        if light and height > 60:
            pg.draw.rect(surface, (127, 107, 77), (3, 3, width-6, height-6), 1)
            self.blood(surface, (width+9, height-12), min(24, width*.35), width+height)
        if width > 700 and height > 350:
            self.blood(surface, (width-15, height-15), 55, width)
        if len(self.cache) >= 160:
            self.cache.clear()
        self.cache[key] = surface
        return surface
