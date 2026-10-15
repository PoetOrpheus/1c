import tempfile
import unittest
from pathlib import Path
import sqlite3
from database import Database
from production import new_node
from part_catalog import PartCatalog, copy_to_order, import_candidates


class PartsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'orders.db'
        self.db=Database(self.path)
        self.catalog=PartCatalog(self.db)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_folders_rename_move_and_cycle(self):
        a=self.catalog.save_folder('Шкаф')
        b=self.catalog.save_folder('Полки',a)
        self.catalog.save_folder('Шкаф заказчика',None,a)
        with self.assertRaises(ValueError): self.catalog.save_folder('Шкаф',b,a)
        with self.assertRaises(ValueError): self.catalog.save_folder('Шкаф',a,a)
        self.catalog.save_folder('Полки',None,b)
        self.assertEqual(self.catalog.folders()[0]['parent_id'],None)

    def test_nonempty_folder_is_protected(self):
        folder=self.catalog.save_folder('Шкаф')
        ident=self.catalog.save_part(new_node('part'),folder)
        with self.assertRaises(ValueError): self.catalog.delete(folder,True)
        self.catalog.delete(ident)
        self.catalog.delete(folder,True)
        self.assertEqual(self.catalog.folders(),[])

    def test_part_moves_and_order_uses_independent_copy(self):
        a=self.catalog.save_folder('A'); b=self.catalog.save_folder('B')
        p=new_node('part'); p['name']='Полка'
        ident=self.catalog.save_part(p,a)
        self.catalog.save_part(p,b,ident)
        records=self.catalog.parts()
        self.assertEqual(records[0]['folder_id'],b)
        copies=copy_to_order(records,'assembly-id')
        self.assertEqual(copies[0]['parent_id'],'assembly-id')
        self.assertNotEqual(copies[0]['id'],p['id'])
        copies[0]['name']='Другая полка'
        self.assertEqual(self.catalog.parts()[0]['name'],'Полка')

    def test_import_idempotent_and_manual_changes_preserved(self):
        p=new_node('part')
        self.assertEqual(self.catalog.import_parts([('source',p)],None),(1,0))
        r=self.catalog.parts()[0]
        p['name']='Ручное имя'
        self.catalog.save_part(p,None,r['id'])
        self.assertEqual(self.catalog.import_parts([('source',new_node('part'))],None),(0,1))
        self.assertEqual(self.catalog.parts()[0]['name'],'Ручное имя')

    def test_import_rollback(self):
        p=new_node('part')
        with self.assertRaises(sqlite3.IntegrityError):
            self.catalog.import_parts([('source',p)],999)
        self.assertEqual(self.catalog.parts(),[])

    def test_validation(self):
        for value in ('nan',-1):
            p=new_node('part');p['width']=value
            with self.assertRaises(ValueError): self.catalog.save_part(p)
        with self.assertRaises(ValueError): self.catalog.save_folder(' ')

    def test_reopen_keeps_library(self):
        folder=self.catalog.save_folder('Шкаф')
        self.catalog.save_part(new_node('part'),folder)
        self.db.close(); self.db=Database(self.path)
        self.assertEqual(len(PartCatalog(self.db).parts()),1)
        self.assertEqual(PartCatalog(self.db).folders()[0]['name'],'Шкаф')

    def test_supplied_files_become_templates(self):
        from cam_import import read_batch
        folder=Path(__file__).resolve().parents[1]/'1c_файлы_мишы'
        if not folder.exists(): self.skipTest('No local CAM fixtures')
        batch=read_batch(list(folder.iterdir()))
        rows=import_candidates(batch)
        self.assertEqual(len(rows),41)
        self.assertTrue(all(p['quantity']==1 for _,p in rows))
        self.assertEqual(self.catalog.import_parts(rows,None),(41,0))
        self.assertEqual(self.catalog.import_parts(rows,None),(0,41))


if __name__=='__main__': unittest.main()
