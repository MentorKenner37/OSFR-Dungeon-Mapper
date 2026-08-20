import tempfile, unittest
from pathlib import Path
import mapper

class MapperTests(unittest.TestCase):
    def test_playlist_count_and_unique_ids(self):
        self.assertEqual(len(mapper.PLAYLISTS), 28)
        self.assertEqual(len({x[1] for x in mapper.PLAYLISTS}), 28)
    def test_hamming_distance(self):
        self.assertEqual(mapper.distance(0b1010, 0b0011), 2)
    def test_exports_empty_graph(self):
        with tempfile.TemporaryDirectory() as td:
            old_db, old_export = mapper.DB, mapper.EXPORT_DIR
            mapper.DB, mapper.EXPORT_DIR = Path(td)/'test.db', Path(td)
            try:
                mapper.init_db(); out=mapper.export_graph('The Bat Cave','json')
                self.assertTrue(out.exists()); self.assertEqual(__import__('json').loads(out.read_text())['rooms'], [])
            finally: mapper.DB, mapper.EXPORT_DIR = old_db, old_export

if __name__ == '__main__': unittest.main()
