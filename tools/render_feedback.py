"""Render reviewable fixtures that obey unique number-card ownership."""
import os
os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main
from config_editor import ConfigEditor
app=main.App()
try:
    app.editor=ConfigEditor(main.ROOT,main.engine.GAME_CONFIG)
    app.overlay='config';app.editor.tab='presets';app.render()
    main.pg.image.save(app.canvas,main.ROOT/'docs/presets-v139.png')
    app.overlay=None;app.preview()
    gs=app.gs
    gs.p1_hand=[11,7,6];gs.p2_hand=[1,9,8]
    gs.deck=[n for n in range(1,12) if n not in gs.p1_hand+gs.p2_hand]
    assert sorted(gs.deck+gs.p1_hand+gs.p2_hand)==list(range(1,12))
    gs.p1_fingers=1;gs.p2_fingers=10
    gs.active_trumps=[]
    gs.resolve_round();gs.blood_loss={1:10,2:4}
    app.selected=None;app.render()
    main.pg.image.save(app.canvas,main.ROOT/'docs/table-v139.png')
finally:
    app.connection.close();main.pg.quit()
