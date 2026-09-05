"""Display-only card catalog. Wire names, types and rules stay in re7_21.py."""
# name: Chinese name, category, Chinese reference text, English reference text
CARDS = {}


def add(name, zh, category, description, english):
    CARDS[name] = (zh, category, description, english)


for number, word in enumerate(('Two', 'Three', 'Four', 'Five', 'Six', 'Seven'), 2):
    add(word, f'数字 {number}', 'draw', f'抽取牌堆中的 {number} 点牌。牌已离开牌堆或抽牌被封锁时无效。', f'Draw {number} if available and drawing is not locked.')
    add(word + '+', f'数字 {number}+', 'draw', f'获得 1 张王牌，再尝试抽取 {number} 点牌。', f'Gain 1 trump, then try to draw {number}.')
for n in (1, 2):
    add(f'Add {n}', f'增加{("一", "二")[n-1]}', 'attack', f'图鉴效果：对手受到的伤害增加 {n}。', f'Reference: increase damage to your opponent by {n}.')
for name, zh, n in [('Shield', '护盾', 1), ('Shield+', '护盾+', 2)]:
    add(name, zh, 'guard', f'图鉴效果：抵消 {n} 点伤害，可叠加。', f'Reference: reduce incoming damage by {n}; stacks.')
for n in (17, 24, 27):
    add(f'Go {n}', f'目标 {n}', 'target', f'图鉴效果：本局目标点数改为 {n}。', f'Reference: change the round target to {n}.')

for row in [
    ('Destroy', '破坏', 'control', '图鉴效果：移除对手最近打出的王牌。', 'Reference: remove the opponent’s most recent trump.'),
    ('Destroy+', '破坏+', 'control', '图鉴效果：移除对手所有场上王牌。', 'Reference: remove all opponent trumps on the table.'),
    ('Destroy++', '破坏++', 'control', '图鉴效果：清除对手场上王牌并封锁其王牌。', 'Reference: clear and lock opponent trumps.'),
    ('Return', '归还', 'control', '将自己的最后一张明牌放回牌堆随机位置。', 'Return your last face-up number card to the deck.'),
    ('ADD2+', '归还+', 'attack', '将对手最后一张明牌放回牌堆。图鉴另有增伤 +2。', 'Return the opponent’s last face-up card. Reference also lists +2 damage.'),
    ('Remove', '移除', 'control', '将对手最后一张明牌放回牌堆随机位置。', 'Return the opponent’s last face-up number card to the deck.'),
    ('Perfect', '完美抽牌', 'draw', '抽取最接近目标且不爆牌的牌；若必爆，则抽最小的牌。', 'Draw the best safe number; if none is safe, draw the smallest.'),
    ('Perfect+', '完美抽牌+', 'draw', '执行完美抽牌。图鉴另有场上增伤 +5。', 'Perform a perfect draw. Reference also lists +5 damage.'),
    ('Change', '变换', 'control', '交换双方最后一张明牌；双方都需有明牌。', 'Swap the last face-up number cards; both players need one.'),
    ('Trump+', '王牌变换', 'resource', '消耗此牌，再随机替换 2 张手中王牌，获得 3 张新王牌。', 'Consume this trump and replace 2 other trumps with 3 new ones.'),
    ('Trump++', '王牌变换+', 'resource', '消耗此牌，随机弃 1 张王牌，再获得 4 张王牌。', 'Consume this card, discard 1 random trump, then gain 4.'),
    ('S-Attack', '盾牌攻击', 'attack', '图鉴效果：消耗场上 3 张护盾，提供 +3 伤害。', 'Reference: consume 3 table shields for +3 damage.'),
    ('S-Attack+', '盾牌攻击+', 'attack', '图鉴效果：消耗场上 2 张护盾，提供 +5 伤害。', 'Reference: consume 2 table shields for +5 damage.'),
    ('Waste', '强迫消耗', 'control', '图鉴效果：对手使用 2 张王牌可解除；未解除则结算时失去一半王牌。', 'Reference: opponent must play 2 trumps or lose half at settlement.'),
    ('Waste+', '强迫消耗+', 'control', '图鉴效果：对手使用 3 张王牌可解除；未解除则结算时失去全部王牌。', 'Reference: opponent must play 3 trumps or lose all at settlement.'),
    ('Desire', '欲望', 'attack', '图鉴效果：增伤等于对手王牌数量的一半，向下取整。', 'Reference: add half the opponent’s trump count, rounded down, to damage.'),
    ('Desire+', '欲望+', 'attack', '图鉴效果：增伤等于对手的王牌数量。', 'Reference: add the opponent’s trump count to damage.'),
    ('Love', '爱你的敌人', 'draw', '令未爆牌的对手执行一次完美抽牌，受抽牌封锁限制。', 'Force a perfect draw for a non-busted opponent, unless drawing is locked.'),
    ('Gamble', '生死一搏', 'attack', '图鉴效果：伤害 +100，并封锁对手抽牌。', 'Reference: +100 damage and lock opponent drawing.'),
    ('D-Destroy', '死亡破坏', 'attack', '弃掉剩余王牌的一半并执行完美抽牌。图鉴另有增伤 +10。', 'Discard half your remaining trumps and perform a perfect draw. Reference: +10 damage.'),
    ('Add 21', '增加二十一', 'attack', '图鉴效果：自己恰好 21 点时，对手受到的伤害 +21。', 'Reference: +21 damage to the opponent when your total is exactly 21.'),
    ('Happiness', '幸福', 'resource', '双方各获得 1 张王牌。', 'Both players gain 1 trump.'),
    ('Curse', '诅咒', 'control', '随机弃 1 张剩余王牌，令未爆牌的对手抽牌堆中最大的牌。', 'Discard 1 random remaining trump; a non-busted opponent draws the largest number.'),
    ('M-Draw', '魔法抽牌', 'resource', '获得 3 张王牌。图鉴未标出的场上效果：自身受伤 +1。', 'Gain 3 trumps. Table effect in source: +1 incoming damage.'),
    ('Silence', '沉默', 'control', '图鉴效果：封锁对手抽牌。', 'Reference: lock opponent number draws.'),
    ('Oblivion', '遗忘', 'control', '跳过本局结算，清空牌桌并重新发牌。', 'Skip settlement, clear the table and deal a new round.'),
    ('Harvest', '收割', 'resource', '图鉴效果：使用王牌后获得 1 张王牌。', 'Reference: gain 1 trump after playing a trump.'),
    ('Escape', '逃脱', 'control', '图鉴效果：平局结束游戏，不扣血。', 'Reference: end the game in a draw without damage.'),
    ('U-Draw', '终极抽牌', 'draw', '获得 2 张王牌，再执行一次完美抽牌。', 'Gain 2 trumps, then perform a perfect draw.'),
]:
    add(*row)

CATEGORIES = {
    'attack': ('进攻', 'ATTACK', (210, 116, 99), '+'),
    'guard': ('防御', 'GUARD', (111, 170, 159), '◇'),
    'draw': ('抽牌', 'DRAW', (203, 180, 125), '↗'),
    'control': ('干扰', 'CONTROL', (161, 143, 189), '×'),
    'resource': ('资源', 'RESOURCE', (121, 156, 187), '∞'),
    'target': ('目标', 'TARGET', (203, 180, 125), '◎'),
}


def info(name):
    return CARDS.get(name, (name, 'control', '按原版规则执行。', 'Uses the original rules.'))
