"""讯飞比对用的本机 JPEG 样本库；与原 SFace 向量数据库隔离。"""
from datetime import datetime, timezone
import os
from experiments.access.jetson.library import FaceLibrary, APPLICATION_ID


class CloudFaceLibrary(FaceLibrary):
    @staticmethod
    def _check_owner(db):
        if (db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                or db.execute('PRAGMA user_version').fetchone()[0] != 2):
            raise ValueError('该文件不是讯飞照片库，请使用独立数据库并重新录入')

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists() and self.path.stat().st_size > 0
        with self._connect() as db, db:
            if existed:
                self._check_owner(db)
            db.execute('CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS photos (person_id INTEGER PRIMARY KEY REFERENCES people(id), jpeg BLOB NOT NULL)')
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute('PRAGMA user_version=2')
        os.chmod(self.path, 0o600)

    def samples(self):
        with self._connect(readonly=True) as db:
            self._check_owner(db)
            return [dict(r) for r in db.execute('SELECT p.id AS person_id,p.name,s.jpeg '
                    'FROM people p JOIN photos s ON p.id=s.person_id ORDER BY p.id')]

    def add_photo(self, jpeg, name=''):
        if not isinstance(name, str) or len(name.strip()) > 64 or not isinstance(jpeg, bytes) or not jpeg:
            raise ValueError('人员姓名或照片无效')
        with self._connect() as db, db:
            self._check_owner(db)
            cur = db.execute('INSERT INTO people(name,created_at) VALUES (?,?)',
                             (name.strip(), datetime.now(timezone.utc).isoformat()))
            person_id = cur.lastrowid
            assigned = name.strip() or f'人员{person_id:04d}'
            db.execute('UPDATE people SET name=? WHERE id=?', (assigned, person_id))
            db.execute('INSERT INTO photos VALUES (?,?)', (person_id, jpeg))
        return {'person_id': person_id, 'name': assigned, 'samples': 1}

    def list_people(self):
        if not self.path.exists():
            return []
        with self._connect(readonly=True) as db:
            self._check_owner(db)
            return [dict(r) for r in db.execute('SELECT p.id,p.name,p.created_at,COUNT(s.person_id) AS samples '
                    'FROM people p LEFT JOIN photos s ON p.id=s.person_id GROUP BY p.id ORDER BY p.id')]
