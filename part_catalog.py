"""Reusable part templates; folders never cascade-delete customer data."""
import json
from copy import deepcopy
from production import new_order, new_node, numeric, uid


class PartCatalog:
    def __init__(self, db):
        self.db = db
        self.conn = db.connection

    def folders(self):
        return [dict(r) for r in self.conn.execute('SELECT * FROM part_folders ORDER BY name COLLATE NOCASE')]

    def parts(self):
        return [dict(r, payload=json.loads(r['payload'])) for r in self.conn.execute('SELECT * FROM part_catalog ORDER BY name COLLATE NOCASE')]

    def save_folder(self, name, parent_id=None, folder_id=None):
        name = name.strip()
        if not name:
            raise ValueError('Укажите название папки')
        parents = {r['id']: r['parent_id'] for r in self.folders()}
        current = parent_id
        seen = set()
        while current is not None:
            if current == folder_id or current in seen:
                raise ValueError('Нельзя переместить папку в себя или вложенную папку')
            if current not in parents:
                raise ValueError('Папка не найдена')
            seen.add(current)
            current = parents[current]
        with self.conn:
            if folder_id is None:
                return self.conn.execute('INSERT INTO part_folders(name,parent_id) VALUES (?,?)',(name,parent_id)).lastrowid
            self.conn.execute('UPDATE part_folders SET name=?,parent_id=? WHERE id=?',(name,parent_id,folder_id))
            return folder_id

    def save_part(self, payload, folder_id=None, part_id=None, source_key=None):
        item = deepcopy(payload)
        item['name'] = item.get('name', '').strip()
        if not item['name']:
            raise ValueError('Укажите наименование детали')
        for key in ('width','height','thickness','area','mass','bends','laser_minutes','filing_minutes','waste_pct'):
            item[key] = numeric(item.get(key,0),key,integer=key=='bends')
        item['quantity'] = numeric(item.get('quantity',1),'Количество',positive=True,integer=True)
        item['kind'] = 'part'
        item['parent_id'] = ''
        with self.conn:
            data = json.dumps(item,ensure_ascii=False)
            if part_id is None:
                return self.conn.execute('INSERT INTO part_catalog(folder_id,name,payload,source_key) VALUES (?,?,?,?)',
                                         (folder_id,item['name'],data,source_key)).lastrowid
            self.conn.execute('UPDATE part_catalog SET folder_id=?,name=?,payload=? WHERE id=?',(folder_id,item['name'],data,part_id))
            return part_id

    def delete(self, key, folder=False):
        if folder:
            if self.conn.execute('SELECT 1 FROM part_folders WHERE parent_id=?',(key,)).fetchone() or self.conn.execute('SELECT 1 FROM part_catalog WHERE folder_id=?',(key,)).fetchone():
                raise ValueError('Папка не пуста. Сначала перенесите или удалите её содержимое.')
        with self.conn:
            self.conn.execute('DELETE FROM '+('part_folders' if folder else 'part_catalog')+' WHERE id=?',(key,))

    def move_parts(self, ids, folder_id):
        with self.conn:
            self.conn.executemany('UPDATE part_catalog SET folder_id=? WHERE id=?',[(folder_id,key) for key in ids])

    def import_parts(self, candidates, folder_id):
        existing = {r[0] for r in self.conn.execute('SELECT source_key FROM part_catalog WHERE source_key IS NOT NULL')}
        added = skipped = 0
        # Single transaction: invalid destination or record never leaves half an import.
        self.conn.execute('SAVEPOINT catalog_import')
        try:
            for key, item in candidates:
                if key in existing:
                    skipped += 1
                    continue
                self.conn.execute('INSERT INTO part_catalog(folder_id,name,payload,source_key) VALUES (?,?,?,?)',
                                  (folder_id,item['name'],json.dumps(item,ensure_ascii=False),key))
                existing.add(key)
                added += 1
            self.conn.execute('RELEASE catalog_import')
        except Exception:
            self.conn.execute('ROLLBACK TO catalog_import')
            self.conn.execute('RELEASE catalog_import')
            raise
        return added, skipped


def import_candidates(batch):
    from cam_import import apply_import
    if not batch['documents']:
        return []
    order,_ = apply_import(new_order('CATALOG'),batch['documents'])
    result = []
    for item in order['nodes']:
        if item['kind'] != 'part':
            continue
        item = deepcopy(item)
        key = item['cam_part_key']
        item['source_quantity'] = item['quantity']
        item['quantity'] = 1
        item['parent_id'] = ''
        item['note'] = 'Импорт: ' + ', '.join(d['filename'] for d in batch['documents'] if any(p['part_key']==key for p in d['parts']))
        item['catalog_warnings'] = list(dict.fromkeys(w for d in order.get('cam_imports',[]) if any(p['part_key']==key for p in d['parts']) for w in d['warnings']))
        if item['catalog_warnings']:
            item['note'] += '\n' + '\n'.join(item['catalog_warnings'])
        result.append((key,item))
    return result


def copy_to_order(records, parent_id=''):
    result = []
    for record in records:
        item = deepcopy(record['payload'])
        item['id'],item['parent_id'] = uid(),parent_id
        item['catalog_id'] = record['id']
        for key in ('cam_generated','cam_part_key','cam_initial_quantity'):
            item.pop(key,None)
        result.append(item)
    return result
