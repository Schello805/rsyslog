import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch
import database
from config import config
from log_writer import LogWriter


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.previous = getattr(database._local, 'connection', None)
        database._local.connection = None
        self.override = patch.object(config, 'DATABASE_PATH', os.path.join(self.directory.name, 'batch.db'))
        self.override.start(); database.init_db()
        self.entry = dict(host='192.0.2.1', app='service', level='WARN', message='problem')

    def tearDown(self):
        database._local.connection.close()
        database._local.connection = self.previous
        self.override.stop(); self.directory.cleanup()

    def test_one_transaction_persists_logs_and_incidents(self):
        trace = []
        conn = database.get_db_connection(); conn.set_trace_callback(trace.append)
        results = database.insert_logs([self.entry] * 20)
        conn.set_trace_callback(None)
        self.assertEqual(sum(sql == 'COMMIT' for sql in trace), 1)
        self.assertEqual(len({row['id'] for row in results}), 20)
        self.assertEqual(database.get_logs()[1], 20)
        self.assertEqual(conn.execute('SELECT occurrences FROM incidents').fetchone()[0], 20)

    def test_failed_batch_rolls_back_before_individual_retry(self):
        results = database.insert_logs([self.entry, dict(self.entry, metadata={'bad': object()}), self.entry])
        self.assertIsInstance(results[1], TypeError)
        self.assertEqual(database.get_logs()[1], 2)
        self.assertEqual(database.get_db_connection().execute('SELECT occurrences FROM incidents').fetchone()[0], 2)
        self.assertNotEqual(results[0]['id'], results[2]['id'])

    def test_full_disk_batch_rolls_back_without_duplicate_records(self):
        import sqlite3
        original = database.insert_log
        calls = 0
        def full_once(**entry):
            nonlocal calls
            calls += 1
            if calls == 2:
                error = sqlite3.OperationalError('disk full')
                error.sqlite_errorcode = sqlite3.SQLITE_FULL
                raise error
            return original(**entry)
        with patch.object(database, 'insert_log', side_effect=full_once):
            results = database.insert_logs([self.entry, self.entry])
        self.assertEqual(database.get_logs()[1], 2)
        self.assertEqual(len({row['id'] for row in results}), 2)
        self.assertEqual(database.get_db_connection().execute('SELECT occurrences FROM incidents').fetchone()[0], 2)

    def test_cancelled_caller_does_not_cancel_accepted_write(self):
        async def check():
            writer = LogWriter(); await writer.start()
            task = asyncio.create_task(writer.submit(**self.entry))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            await writer.stop()
            self.assertEqual(database.get_logs()[1], 1)
        asyncio.run(check())

    def test_concurrent_submissions_commit_before_return_and_shutdown_drains(self):
        async def check():
            writer = LogWriter(); await writer.start()
            tasks = [asyncio.create_task(writer.submit(**self.entry)) for _ in range(12)]
            await asyncio.sleep(0)
            await writer.stop()
            results = await asyncio.gather(*tasks)
            self.assertEqual(len({row['id'] for row in results}), 12)
            self.assertEqual(database.get_logs()[1], 12)
            self.assertIsNone(writer.task)
        asyncio.run(check())


if __name__ == '__main__': unittest.main()
