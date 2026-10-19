"""SQLite 人脸库：人员信息、多帧特征、模型指纹和事务写入。"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from experiments.access.jetson.features import normalized_feature

APPLICATION_ID = 0x4E424643


def match_templates(feature, templates, threshold, margin):
    """按人员汇总最佳相似度，避免同一人的不同模板成为第二候选。"""
    import numpy as np
    vector = normalized_feature(feature)
    people = {}
    for template in templates:
        score = float(np.clip(np.dot(vector, template["feature"]), -1, 1))
        key = template["person_id"]
        if key not in people or score > people[key]["score"]:
            people[key] = {"person_id": key, "name": template["name"], "score": score}
    candidates = sorted(people.values(), key=lambda item: item["score"], reverse=True)
    if not candidates:
        return {"recognized": False, "person_id": None, "name": None, "score": None, "note": "人脸库为空，请先录入"}
    best = candidates[0]
    separated = len(candidates) == 1 or best["score"] - candidates[1]["score"] >= margin
    recognized = best["score"] >= threshold and separated
    return {**best, "recognized": recognized, "note": "已匹配库内人员" if recognized else
            "候选人员相似度接近，拒绝确认" if not separated else "未匹配到库内人员"}


class FaceLibrary:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def _connect(self, readonly=False):
        if readonly:
            connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)
        else:
            connection = sqlite3.connect(str(self.path), timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _check_owner(connection):
        if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("该文件不是宁波人脸库，拒绝修改或读取为人脸数据")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("人脸库版本不支持")

    def initialize(self, signature, dimension=128):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists() and self.path.stat().st_size > 0
        with self._connect() as connection:
            if existed:
                self._check_owner(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS templates (id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE, vector BLOB NOT NULL)")
                expected = {"model_signature": signature, "dimension": str(dimension)}
                for key, value in expected.items():
                    row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
                    if row is not None and row[0] != value:
                        raise ValueError("人脸库与当前模型/特征格式不兼容，需重新使用相同模型")
                    connection.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES (?,?)", (key, value))
                connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                connection.execute("PRAGMA user_version=1")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def templates(self, signature):
        if not self.path.exists():
            return []
        import numpy as np
        with self._connect(readonly=True) as connection:
            self._check_owner(connection)
            row = connection.execute("SELECT value FROM metadata WHERE key='model_signature'").fetchone()
            if row is None or row[0] != signature:
                raise ValueError("人脸库模型指纹与当前模型不一致")
            rows = connection.execute("SELECT p.id,p.name,t.vector FROM templates t JOIN people p ON p.id=t.person_id ORDER BY p.id,t.id").fetchall()
        result = []
        for row in rows:
            try:
                vector = normalized_feature(np.frombuffer(row["vector"], dtype="<f4"))
            except Exception as exc:
                raise ValueError(f"人脸库中人员 {row['id']} 的特征损坏") from exc
            result.append({"person_id": row["id"], "name": row["name"], "feature": vector})
        return result

    def add_person(self, features, name=""):
        vectors = [normalized_feature(feature).tobytes() for feature in features]
        if not vectors:
            raise ValueError("录入人员至少需要一个有效特征")
        if not isinstance(name, str) or len(name.strip()) > 64:
            raise ValueError("人员姓名须为不超过 64 字的字符串")
        with self._connect() as connection:
            self._check_owner(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute("INSERT INTO people(name,created_at) VALUES (?,?)",
                                            (name.strip(), datetime.now(timezone.utc).isoformat()))
                person_id = cursor.lastrowid
                assigned = name.strip() or f"人员{person_id:04d}"
                connection.execute("UPDATE people SET name=? WHERE id=?", (assigned, person_id))
                connection.executemany("INSERT INTO templates(person_id,vector) VALUES (?,?)",
                                       [(person_id, vector) for vector in vectors])
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"person_id": person_id, "name": assigned, "samples": len(vectors)}

    def list_people(self):
        if not self.path.exists():
            return []
        with self._connect(readonly=True) as connection:
            self._check_owner(connection)
            return [dict(row) for row in connection.execute(
                "SELECT p.id,p.name,p.created_at,COUNT(t.id) AS samples FROM people p LEFT JOIN templates t ON p.id=t.person_id GROUP BY p.id ORDER BY p.id"
            )]

    def rename(self, person_id, name):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
            raise ValueError("姓名须为 1~64 字")
        with self._connect() as connection:
            self._check_owner(connection)
            with connection:
                cursor = connection.execute("UPDATE people SET name=? WHERE id=?", (name.strip(), person_id))
                if cursor.rowcount != 1:
                    raise ValueError("人员编号不存在")
