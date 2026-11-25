import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ofdl.common import AppError, Cancelled, Control
from ofdl.duplicates import (DuplicateGroup, HashCache, delete_extras, find_duplicates,
                             hash_file, iter_media_files)


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class DuplicateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'library'
        self.root.mkdir()
        self.control = Control()
        self.cache = HashCache(Path(self.temp.name) / 'hashes.sqlite3')
        self.addCleanup(self.cache.close)
        self.logs = []

    def emit(self, kind, value):
        if kind == 'log':
            self.logs.append(value)

    def test_identical_content_across_folders_is_one_group(self):
        first = write(self.root / 'alice' / 'photos' / 'a.jpg', b'x' * 400)
        second = write(self.root / 'alice_old' / 'photos' / 'b.jpg', b'x' * 400)
        write(self.root / 'alice' / 'photos' / 'c.jpg', b'y' * 400)
        write(self.root / 'alice' / 'photos' / 'unique.jpg', b'z' * 13)
        groups = find_duplicates(self.root, self.control, cache=self.cache, emit=self.emit)
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(groups[0].files), sorted([first, second]))
        self.assertEqual(groups[0].reclaimable, 400)
        self.assertEqual(groups[0].size, 400)

    def test_hash_cache_avoids_rehashing_unchanged_files(self):
        write(self.root / 'a.jpg', b'x' * 100)
        write(self.root / 'b.jpg', b'x' * 100)
        find_duplicates(self.root, self.control, cache=self.cache)
        import ofdl.duplicates as module
        original = module.hash_file
        calls = []
        def counted(path, control):
            calls.append(path)
            return original(path, control)
        with patch('ofdl.duplicates.hash_file', side_effect=counted):
            groups = find_duplicates(self.root, self.control, cache=self.cache)
        self.assertEqual(calls, [])
        self.assertEqual(len(groups), 1)

    def test_cache_is_keyed_by_mtime_so_changed_files_are_rehashed(self):
        path = write(self.root / 'a.jpg', b'x' * 100)
        write(self.root / 'b.jpg', b'x' * 100)
        find_duplicates(self.root, self.control, cache=self.cache)
        write(path, b'y' * 100)
        groups = find_duplicates(self.root, self.control, cache=self.cache)
        # Same size, different content: no longer a duplicate.
        self.assertEqual(groups, [])

    def test_manifest_keeper_is_preferred(self):
        write(self.root / 'a.jpg', b'x' * 50)
        keeper = write(self.root / 'deep' / 'b.jpg', b'x' * 50)
        database = self.root / '.ofdl.sqlite3'
        db = sqlite3.connect(database)
        try:
            db.execute('CREATE TABLE media (identity TEXT PRIMARY KEY, path TEXT NOT NULL, '
                       'size INTEGER NOT NULL, completed_at TEXT NOT NULL)')
            db.execute('INSERT INTO media VALUES (?,?,?,?)',
                       ('1:2:full', 'deep/b.jpg', 50, '2026-01-01T00:00:00+00:00'))
            db.commit()
        finally:
            db.close()
        groups = find_duplicates(self.root, self.control, cache=self.cache)
        self.assertEqual(groups[0].keep, keeper)

    def test_keeper_falls_back_to_the_shortest_path(self):
        short = write(self.root / 'a.jpg', b'x' * 50)
        write(self.root / 'nested' / 'very' / 'long' / 'b.jpg', b'x' * 50)
        groups = find_duplicates(self.root, self.control, cache=self.cache)
        self.assertEqual(groups[0].keep, short)

    def test_non_media_and_partial_files_are_ignored(self):
        write(self.root / 'notes.txt', b'x' * 10)
        write(self.root / 'unfinished.jpg.part', b'x' * 10)
        write(self.root / '.hidden.jpg', b'x' * 10)
        self.assertEqual(list(iter_media_files(self.root, self.control)), [])

    def test_delete_keeps_one_copy_and_reports_savings(self):
        keep = write(self.root / 'a.jpg', b'x' * 70)
        extra = write(self.root / 'b.jpg', b'x' * 70)
        group = DuplicateGroup(digest=hash_file(keep, self.control), size=70,
                               files=[keep, extra], keep=keep)
        deleted, freed, errors = delete_extras(group, self.root, self.control, self.emit)
        self.assertEqual((deleted, freed, errors), (1, 70, []))
        self.assertTrue(keep.exists())
        self.assertFalse(extra.exists())

    def test_changed_extra_is_not_deleted(self):
        keep = write(self.root / 'a.jpg', b'x' * 70)
        extra = write(self.root / 'b.jpg', b'x' * 70)
        group = DuplicateGroup(digest=hash_file(keep, self.control), size=70,
                               files=[keep, extra], keep=keep)
        write(extra, b'y' * 70)   # same size, new content
        deleted, freed, errors = delete_extras(group, self.root, self.control, self.emit)
        self.assertEqual((deleted, freed), (0, 0))
        self.assertTrue(extra.exists())
        self.assertTrue(any('content changed' in error for error in errors))

    def test_missing_or_changed_keeper_preserves_every_extra(self):
        for change in ('missing', 'size', 'content'):
            with self.subTest(change=change):
                keep = write(self.root / 'keep.jpg', b'x' * 70)
                extras = [write(self.root / f'extra{i}.jpg', b'x' * 70) for i in range(2)]
                group = DuplicateGroup(hash_file(keep, self.control), 70, [keep, *extras], keep)
                if change == 'missing':
                    keep.unlink()
                else:
                    keep.write_bytes(b'y' * (70 if change == 'content' else 3))
                deleted, freed, errors = delete_extras(group, self.root, self.control)
                self.assertEqual((deleted, freed), (0, 0))
                self.assertTrue(all(path.exists() for path in extras))
                self.assertTrue(any('Keeper' in error for error in errors))

    def test_keeper_changed_while_hashing_extra_is_detected(self):
        keep = write(self.root / 'keep.jpg', b'x' * 70)
        extra = write(self.root / 'extra.jpg', b'x' * 70)
        group = DuplicateGroup(hash_file(keep, self.control), 70, [keep, extra], keep)
        def changing_hash(path, control):
            digest = hash_file(path, control)
            if path == extra:
                keep.write_bytes(b'y' * 70)
            return digest
        with patch('ofdl.duplicates.hash_file', side_effect=changing_hash):
            result = delete_extras(group, self.root, self.control)
        self.assertEqual(result[0], 0)
        self.assertEqual(extra.read_bytes(), b'x' * 70)

    def test_keeper_disappearing_between_deletions_preserves_remaining_copy(self):
        keep = write(self.root / 'keep.jpg', b'x' * 70)
        first = write(self.root / 'first.jpg', b'x' * 70)
        last = write(self.root / 'last.jpg', b'x' * 70)
        group = DuplicateGroup(hash_file(keep, self.control), 70, [keep, first, last], keep)
        def remove_keeper(kind, value):
            if kind == 'log':
                keep.unlink()
        result = delete_extras(group, self.root, self.control, remove_keeper)
        self.assertEqual(result[0], 1)
        self.assertTrue(last.exists())
        self.assertTrue(result[2])

    def test_keeper_outside_library_is_refused(self):
        keep = write(self.root.parent / 'outside.jpg', b'x' * 70)
        extra = write(self.root / 'extra.jpg', b'x' * 70)
        group = DuplicateGroup(hash_file(keep, self.control), 70, [keep, extra], keep)
        result = delete_extras(group, self.root, self.control)
        self.assertEqual(result[0], 0)
        self.assertTrue(extra.exists())

    def test_cancellation_during_keeper_verification_propagates(self):
        keep = write(self.root / 'keep.jpg', b'x' * 70)
        extra = write(self.root / 'extra.jpg', b'x' * 70)
        group = DuplicateGroup(hash_file(keep, self.control), 70, [keep, extra], keep)
        def stopped_hash(path, control):
            if path == keep:
                control.stop()
            return hash_file(path, control)
        with patch('ofdl.duplicates.hash_file', side_effect=stopped_hash):
            with self.assertRaises(Cancelled):
                delete_extras(group, self.root, self.control)
        self.assertTrue(extra.exists())

    def test_file_outside_the_library_is_refused(self):
        outside = write(Path(self.temp.name) / 'outside.jpg', b'x' * 70)
        keep = write(self.root / 'a.jpg', b'x' * 70)
        group = DuplicateGroup(digest=hash_file(keep, self.control), size=70,
                               files=[keep, outside], keep=keep)
        deleted, _, errors = delete_extras(group, self.root, self.control, self.emit)
        self.assertEqual(deleted, 0)
        self.assertTrue(outside.exists())
        self.assertTrue(errors)

    def test_missing_folder_is_rejected(self):
        with self.assertRaises(AppError):
            find_duplicates(Path(self.temp.name) / 'nope', self.control, cache=self.cache)

    def test_cache_roundtrip(self):
        path = write(self.root / 'a.jpg', b'x' * 10)
        info = path.stat()
        self.assertIsNone(self.cache.get(path, info.st_size, info.st_mtime_ns))
        self.cache.put(path, info.st_size, info.st_mtime_ns, 'digest')
        self.assertEqual(self.cache.get(path, info.st_size, info.st_mtime_ns), 'digest')
        self.assertIsNone(self.cache.get(path, info.st_size + 1, info.st_mtime_ns))


if __name__ == '__main__':
    unittest.main()
