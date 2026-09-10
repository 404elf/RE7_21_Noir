import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import main
from config_editor import ConfigEditor


class EditorTests(unittest.TestCase):
    def make(self, root):
        for file in ('config.json','timer.json','audio.json'):
            (Path(root)/file).write_bytes((main.ROOT/file).read_bytes())
        return ConfigEditor(root,main.engine.GAME_CONFIG)

    def test_save_preserves_unknown_fields_and_verifies_backup(self):
        with tempfile.TemporaryDirectory() as root:
            editor=self.make(root)
            original=(Path(root)/'config.json').read_bytes()
            editor.docs['config.json']['custom']={'keep':True}
            editor.action(('cfg','field',0));editor.buffer='15'
            editor.save()
            saved=json.loads((Path(root)/'config.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['game_settings']['max_hp'],15)
            self.assertTrue(saved['custom']['keep'])
            self.assertEqual(next((Path(root)/'config-backups').glob('*')).read_bytes(),original)

    def test_validation_rejects_invalid_ranges_across_tabs(self):
        with tempfile.TemporaryDirectory() as root:
            editor=self.make(root)
            editor.docs['config.json']['game_settings']['deck_range_end']=2
            editor.tab='weights'
            with self.assertRaises(ValueError): editor.save()
            editor.tab='timer';editor.action(('cfg','field',2));editor.buffer='nan'
            with self.assertRaises(ValueError): editor.save()

    def test_external_edit_and_write_failure_do_not_destroy_original(self):
        with tempfile.TemporaryDirectory() as root:
            editor=self.make(root)
            path=Path(root)/'config.json';original=path.read_bytes()
            editor.docs['config.json']['game_settings']['max_hp']=20
            with patch('config_editor.os.replace',side_effect=OSError('disk error')):
                with self.assertRaises(OSError): editor.save()
            self.assertEqual(path.read_bytes(),original)
            path.write_bytes(original+b' ')
            with self.assertRaises(ValueError): editor.save()
            self.assertEqual(path.read_bytes(),original+b' ')

    def test_tabs_fields_and_timer_save(self):
        with tempfile.TemporaryDirectory() as root:
            editor=self.make(root)
            editor.action(('cfg','tab','timer'))
            editor.action(('cfg','field',0))
            editor.action(('cfg','field',1))
            editor.action(('cfg','field',next(i for i,r in enumerate(editor.rows()) if r[0]=='settlement_seconds')));editor.buffer='0'
            editor.save()
            saved=json.loads((Path(root)/'timer.json').read_text(encoding='utf-8'))
            self.assertTrue(saved['enabled'])
            self.assertEqual(saved['mode'],'round')
            self.assertEqual(saved['settlement_seconds'],0)
            editor.action(('cfg','tab','weights'))
            editor.action(('cfg','zero',0))
            self.assertEqual(editor.get(editor.rows()[0]),0)

    def test_all_editor_pages_and_keyboard_input(self):
        with tempfile.TemporaryDirectory() as root:
            app=main.App()
            try:
                app.editor=self.make(root);app.overlay='config'
                for tab in ('game','weights','timer','audio'):
                    app.editor.tab=tab
                    for page in range((len(app.editor.rows())+5)//6):
                        app.editor.page=page;app.render()
                app.editor.tab='game';app.editor.page=0
                app.editor.action(('cfg','field',0))
                for char in '23':
                    app.editor.key(main.pg.event.Event(main.pg.KEYDOWN,key=ord(char),unicode=char,mod=0))
                app.editor.key(main.pg.event.Event(main.pg.KEYDOWN,key=main.pg.K_RETURN,unicode='',mod=0))
                self.assertEqual(app.editor.get(app.editor.rows()[0]),23)
                app.action('close_overlay')
                self.assertEqual(app.editor.get(app.editor.rows()[0]),23)
            finally:
                main.pg.quit()
