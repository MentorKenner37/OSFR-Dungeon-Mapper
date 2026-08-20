import tempfile, unittest
from pathlib import Path
import mapper

class MapperTests(unittest.TestCase):
    def test_playlist_count_and_unique_ids(self):
        self.assertEqual(len(mapper.PLAYLISTS), 28)
        self.assertEqual(len({x[1] for x in mapper.PLAYLISTS}), 28)
    def test_hamming_distance(self):
        self.assertEqual(mapper.distance(0b1010, 0b0011), 2)
    def test_multisignal_distance(self):
        same = mapper.fingerprint_distance(0b1010, (10,) * 24, 0b1010, (10,) * 24)
        changed = mapper.fingerprint_distance(0b1010, (10,) * 24, 0b0101, (100,) * 24)
        self.assertEqual(same, 0)
        self.assertGreater(changed, same)
    def test_exports_empty_graph(self):
        with tempfile.TemporaryDirectory() as td:
            old_db, old_export = mapper.DB, mapper.EXPORT_DIR
            mapper.DB, mapper.EXPORT_DIR = Path(td)/'test.db', Path(td)
            try:
                mapper.init_db(); out=mapper.export_graph('The Bat Cave','json')
                self.assertTrue(out.exists()); self.assertEqual(__import__('json').loads(out.read_text())['rooms'], [])
            finally: mapper.DB, mapper.EXPORT_DIR = old_db, old_export
    def test_consensus_and_room_merge(self):
        with tempfile.TemporaryDirectory() as td:
            old_db, old_export = mapper.DB, mapper.EXPORT_DIR
            mapper.DB, mapper.EXPORT_DIR = Path(td)/'test.db', Path(td)
            try:
                mapper.init_db()
                with mapper.db() as c:
                    did=c.execute("SELECT id FROM dungeons WHERE name='The Bat Cave'").fetchone()[0]
                    v1=c.execute("INSERT INTO videos(dungeon_id,title) VALUES(?,?)",(did,'Run 1')).lastrowid
                    v2=c.execute("INSERT INTO videos(dungeon_id,title) VALUES(?,?)",(did,'Run 2')).lastrowid
                    a=c.execute("INSERT INTO rooms(dungeon_id,label) VALUES(?,?)",(did,'Entrance')).lastrowid
                    b=c.execute("INSERT INTO rooms(dungeon_id,label) VALUES(?,?)",(did,'Hall')).lastrowid
                    duplicate=c.execute("INSERT INTO rooms(dungeon_id,label) VALUES(?,?)",(did,'Hall copy')).lastrowid
                    c.executemany("INSERT INTO transitions(dungeon_id,from_room,to_room,video_id,timestamp) VALUES(?,?,?,?,?)",[(did,a,b,v1,5),(did,a,b,v2,6)])
                graph=mapper.graph_data('The Bat Cave')
                self.assertEqual(graph['edges'][0]['supporting_videos'],2)
                layout=mapper.auto_layout('The Bat Cave')
                self.assertEqual(layout['entrance_room_id'],a)
                with mapper.db() as c:
                    ax=c.execute("SELECT x FROM rooms WHERE id=?",(a,)).fetchone()[0]
                    bx=c.execute("SELECT x FROM rooms WHERE id=?",(b,)).fetchone()[0]
                    self.assertGreater(bx,ax)
                mapper.merge_rooms(duplicate,b)
                with mapper.db() as c: self.assertIsNone(c.execute("SELECT id FROM rooms WHERE id=?",(duplicate,)).fetchone())
            finally: mapper.DB, mapper.EXPORT_DIR = old_db, old_export
    def test_weak_rooms_are_hidden_not_deleted(self):
        with tempfile.TemporaryDirectory() as td:
            old_db=mapper.DB; mapper.DB=Path(td)/'test.db'
            try:
                mapper.init_db()
                with mapper.db() as c:
                    did=c.execute("SELECT id FROM dungeons WHERE name='The Bat Cave'").fetchone()[0]
                    rid=c.execute("INSERT INTO rooms(dungeon_id,label) VALUES(?,?)",(did,'One frame')).lastrowid
                result=mapper.classify_uncertain_rooms('The Bat Cave')
                self.assertEqual(result['uncertain_rooms'],1)
                with mapper.db() as c: self.assertEqual(c.execute("SELECT status FROM rooms WHERE id=?",(rid,)).fetchone()[0],'uncertain')
            finally: mapper.DB=old_db

if __name__ == '__main__': unittest.main()
