import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
import zipfile

import common
import native_client


class NativeTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'BAMBU_BRIDGE_NATIVE_DIR': str(self.root)})
        self.env.start()
        self.sid = str(uuid.uuid4())
        self.directory = self.root / self.sid
        (self.directory / 'requests').mkdir(parents=True)
        (self.directory / 'responses').mkdir()
        self.token = 'secret-session-token-' * 4
        common.atomic_json(self.directory / 'session.json', {'protocol': 1, 'backend': 'studio-native-wx',
            'session_id': self.sid, 'token': self.token, 'pid': os.getpid(), 'created_at': time.time()})

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def host(self, result):
        def respond():
            end = time.monotonic() + 3
            while time.monotonic() < end:
                queued = list((self.directory / 'requests').glob('*.json'))
                if queued:
                    request = json.loads(queued[0].read_text())
                    self.assertEqual(request['token'], self.token)
                    common.atomic_json(self.directory / 'responses' / queued[0].name, {
                        'ok': True, 'session_id': self.sid, 'request_id': queued[0].stem, 'result': result})
                    return
                time.sleep(.01)
        thread = threading.Thread(target=respond)
        thread.start()
        self.addCleanup(thread.join)

    def test_discovery_omits_token_and_excludes_dead_pid(self):
        result = native_client.sessions()
        self.assertEqual(result['sessions'][0]['session_id'], self.sid)
        self.assertNotIn(self.token, json.dumps(result))
        with patch('native_client.psutil.Process', side_effect=native_client.psutil.NoSuchProcess(42)):
            self.assertFalse(native_client.sessions()['live_backend_available'])

    def test_live_read_roundtrip_preserves_exact_serialized_values(self):
        self.host({'revision': 'abc', 'process': {'wall_loops': '4', 'sparse_infill_density': '9%'}})
        result = native_client.request(self.sid, 'read', timeout_seconds=2)
        self.assertEqual(result['process']['sparse_infill_density'], '9%')
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn(self.token, json.dumps(result))

    def test_unconfirmed_write_never_enters_queue(self):
        with self.assertRaises(ValueError):
            native_client.update(self.sid, 'abc', {'wall_loops': '4'}, str(uuid.uuid4()))
        self.assertEqual(list((self.directory / 'requests').iterdir()), [])

    def test_timeout_retry_reuses_intent_without_resubmission(self):
        rid = str(uuid.uuid4())
        parameters = {'expected_revision': 'abc'}
        first = native_client.request(self.sid, 'checkpoint', parameters, rid, .01)
        self.assertEqual(first['status'], 'pending_or_unknown')
        queue = self.directory / 'requests' / (rid + '.json')
        queue.rename(queue.with_suffix('.claimed'))
        second = native_client.request(self.sid, 'checkpoint', parameters, rid, .01)
        self.assertEqual(second['status'], 'pending_or_unknown')
        self.assertFalse(queue.exists())
        with self.assertRaisesRegex(ValueError, 'different operation'):
            native_client.request(self.sid, 'update', {}, rid, .01)

    def test_shared_filament_scope_is_explicit(self):
        with self.assertRaisesRegex(ValueError, 'all slots sharing'):
            native_client.update(self.sid, 'abc', {'nozzle_temperature': '220'}, str(uuid.uuid4()),
                                 'filament', 2, None, True)

    def test_forged_response_and_path_traversal_are_rejected(self):
        with self.assertRaises(ValueError):
            native_client.request('../elsewhere', 'read')
        rid = str(uuid.uuid4())
        common.atomic_json(self.directory / 'responses' / (rid + '.json'), {
            'ok': True, 'session_id': str(uuid.uuid4()), 'request_id': rid, 'result': {}})
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            native_client.operation_status(self.sid, rid)

    def test_checkpoint_hash_is_verified_from_actual_file(self):
        checkpoint = self.directory / 'saved.3mf'
        with zipfile.ZipFile(checkpoint, 'w') as archive:
            archive.writestr('3D/3dmodel.model', '<model/>')
        self.host({'path': str(checkpoint)})
        result = native_client.request(self.sid, 'checkpoint', {'expected_revision': 'abc'}, timeout_seconds=2)
        self.assertEqual(result['path_sha256'], common.sha256(checkpoint))

    def test_completed_receipt_survives_studio_exit(self):
        rid = str(uuid.uuid4())
        common.atomic_json(self.directory / 'responses' / (rid + '.json'), {
            'ok': True, 'session_id': self.sid, 'request_id': rid, 'result': {'applied': True}})
        (self.directory / 'session.json').unlink()
        self.assertTrue(native_client.operation_status(self.sid, rid)['applied'])

    def test_transport_fields_cannot_be_overridden(self):
        with self.assertRaisesRegex(ValueError, 'cannot be overridden'):
            native_client.request(self.sid, 'read', {'token': 'attacker'})


if __name__ == '__main__':
    unittest.main()
