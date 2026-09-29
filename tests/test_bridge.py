import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid
import zipfile

import common
import printer
import studio
import worker


class Isolated(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {'BAMBU_BRIDGE_HOME': str(self.root / 'private')})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def json(self, name, data):
        path = self.root / name
        common.atomic_json(path, data)
        return path

    def project(self, unsafe=None):
        path = self.root / ('unsafe.3mf' if unsafe else 'project.3mf')
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('Metadata/project_settings.config', json.dumps({'layer_height': '0.2', 'wall_loops': '2'}))
            z.writestr('3D/3dmodel.model', '<model><resources/></model>')
            z.writestr('Metadata/plate_1.gcode', 'G1 X1 Y1')
            z.writestr('Metadata/plate_1.gcode.md5', 'old-checksum')
            z.writestr('Metadata/slice_info.config', '<config/>')
            z.writestr('Metadata/thumbnail.png', b'original-png')
            if unsafe:
                z.writestr(unsafe, 'unsafe')
        return path


class LiveTargetTests(Isolated):
    def test_open_project_rejected_before_any_file_or_process_action(self):
        with patch('studio.artifact_dir') as artifact, patch('studio.sha256') as digest, \
                patch('studio.subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError, 'Live project editing is unsupported'):
                studio.project_update('missing.3mf', {'wall_loops': '4'}, [], {}, '', True, 'open_project')
            artifact.assert_not_called()
            digest.assert_not_called()
            launch.assert_not_called()
        self.assertFalse((self.root / 'private').exists())

    def test_capabilities_do_not_invent_a_session_or_touch_devices(self):
        with patch('studio.psutil.process_iter') as processes, patch('studio.subprocess.Popen') as launch:
            value = studio.capabilities()
            self.assertIsNone(value['open_project']['project_identity'])
            self.assertIsNone(value['open_project']['unsaved_changes'])
            for action in ('read', 'update', 'save', 'slice'):
                self.assertFalse(value['open_project'][action])
            self.assertFalse(value['computer_use_fallback'])
            processes.assert_not_called()
            launch.assert_not_called()

    def test_saved_copy_evidence_preserves_paint_and_custom_members(self):
        source = self.project()
        with zipfile.ZipFile(source, 'a') as archive:
            archive.writestr('Metadata/model_settings.config', '<config><part paint="kept"/></config>')
            archive.writestr('Metadata/custom.json', '{"mapping":[4,1,2,3]}')
        original = source.read_bytes()
        result = studio.project_update(str(source), {'wall_loops': '4'}, [], {}, common.sha256(source), True)
        self.assertEqual(result['target'], 'saved_file')
        self.assertFalse(result['open_project_updated'])
        self.assertEqual(source.read_bytes(), original)
        with zipfile.ZipFile(source) as before, zipfile.ZipFile(result['path']) as after:
            for member in ('3D/3dmodel.model', 'Metadata/model_settings.config', 'Metadata/custom.json'):
                self.assertEqual(before.read(member), after.read(member))
            self.assertEqual(json.loads(after.read('Metadata/project_settings.config'))['wall_loops'], '4')

    def test_unknown_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Target must'):
            studio.project_update('missing.3mf', {}, [], {}, '', True, 'typo')


class StateTests(Isolated):
    def test_atomic_json_and_private_state(self):
        path = self.json('state.json', {'a': 1})
        common.atomic_json(path, {'b': 2})
        self.assertEqual(common.read_json(path), {'b': 2})
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_redact_nested_secrets_without_losing_settings(self):
        self.assertEqual(common.redact({'app': {'helio_pat_other': 'SECRET', 'layer_height': '0.2'}, 'access_code': {'sn': 'CODE'}}),
                         {'app': {'helio_pat_other': '<redacted>', 'layer_height': '0.2'}, 'access_code': '<redacted>'})

    def test_uncertain_action_is_not_replayed(self):
        call = Mock(side_effect=TimeoutError('password=DO_NOT_LEAK'))
        request = str(uuid.uuid4())
        first = common.once(request, {'pause': 1}, call)
        second = common.once(request, {'pause': 1}, call)
        self.assertEqual(first['status'], 'pending_or_unknown')
        self.assertTrue(second['deduplicated'])
        self.assertNotIn('DO_NOT_LEAK', json.dumps(first))
        call.assert_called_once()

    def test_request_id_reuse_with_other_intent_is_rejected(self):
        request = str(uuid.uuid4())
        common.once(request, {'a': 1}, lambda: {'status': 'done'})
        with self.assertRaises(ValueError):
            common.once(request, {'a': 2}, lambda: self.fail('must not execute'))

    def test_concurrent_requests_execute_once(self):
        request = str(uuid.uuid4())
        action = Mock(return_value={'status': 'done'})
        # Initialize the schema before concurrent access; connections still contend on reservation.
        with common.ledger():
            pass
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: common.once(request, {'a': 1}, action), range(12)))
        self.assertEqual(action.call_count, 1)
        self.assertTrue(all(x['request_id'] == request for x in results))

    def test_confirmation_and_uuid_are_required(self):
        with self.assertRaises(ValueError):
            common.require_write(False)
        with self.assertRaises(ValueError):
            common.once('../bad', {}, lambda: self.fail('must not execute'))


class ProfileTests(Isolated):
    def test_inheritance_preserves_unknown_vendor_settings(self):
        base = self.json('machine/base.json', {'name': 'base', 'type': 'machine', 'vendor_future_key': ['a'], 'x': 1})
        child = self.json('machine/child.json', {'name': 'child', 'type': 'machine', 'inherits': 'base', 'x': 2})
        with patch('studio.profile_files', return_value=iter([(base, common.read_json(base)), (child, common.read_json(child))])):
            result = studio.profile_read(str(child), True)
        self.assertEqual(result['settings']['vendor_future_key'], ['a'])
        self.assertEqual(result['settings']['x'], 2)
        self.assertNotIn('inherits', result['settings'])

    def test_inheritance_cycle_and_ambiguity_fail_closed(self):
        a = self.json('a.json', {'name': 'a', 'type': 'machine', 'inherits': 'b'})
        b = self.json('b.json', {'name': 'b', 'type': 'machine', 'inherits': 'a'})
        records = [(a, common.read_json(a)), (b, common.read_json(b))]
        with patch('studio.profile_files', return_value=iter(records)), self.assertRaises(ValueError):
            studio.profile_read(str(a), True)
        with patch('studio.profile_files', return_value=iter([])), self.assertRaises(ValueError):
            studio.profile_read(str(a), True)

    def test_profile_copy_keeps_original_and_can_remove_keys(self):
        path = self.json('profile.json', {'name': 'profile', 'unknown': 7, 'remove_me': 4})
        digest = common.sha256(path)
        result = studio.json_update(str(path), {'unknown': 8}, ['remove_me'], digest, True)
        self.assertEqual(common.sha256(path), digest)
        self.assertEqual(common.read_json(Path(result['path']))['unknown'], 8)
        self.assertNotIn('remove_me', common.read_json(Path(result['path'])))
        self.assertEqual(common.sha256(Path(result['backup'])), digest)

    def test_stale_hash_prevents_write(self):
        path = self.json('profile.json', {'a': 1})
        with self.assertRaises(ValueError):
            studio.json_update(str(path), {'a': 2}, [], 'bad', True)
        self.assertEqual(common.read_json(path), {'a': 1})

    def test_preferences_preserve_secrets_and_unknown_values(self):
        path = self.json('BambuStudio.conf', {'access_code': {'serial': 'SECRET'}, 'app': {'language': 'en', 'future': 42}})
        path.write_text(path.read_text() + '# checksum comment\n')
        digest = common.sha256(path)
        with patch('studio.data_directory', return_value=self.root), patch('studio.assert_studio_closed'):
            result = studio.preferences_update('app', {'language': 'fr'}, [], digest, True)
        self.assertEqual(common.read_json(path)['access_code']['serial'], 'SECRET')
        self.assertEqual(common.read_json(path)['app']['future'], 42)
        self.assertTrue(Path(result['backup']).exists())

    def test_running_studio_and_credential_edits_are_refused(self):
        path = self.json('BambuStudio.conf', {'app': {'language': 'en'}})
        with patch('studio.data_directory', return_value=self.root), patch('studio.assert_studio_closed'):
            with self.assertRaises(ValueError):
                studio.preferences_update('app', {'password': 'secret'}, [], common.sha256(path), True)
        process = Mock(info={'name': 'bambu-studio.exe'})
        with patch('studio.psutil.process_iter', return_value=[process]), self.assertRaises(ValueError):
            studio.assert_studio_closed()


class ProjectTests(Isolated):
    def test_update_preserves_original_and_invalidates_slices(self):
        source = self.project()
        digest = common.sha256(source)
        result = studio.project_update(str(source), {'layer_height': '0.12'}, ['wall_loops'], {}, digest, True)
        self.assertEqual(common.sha256(source), digest)
        with zipfile.ZipFile(result['path']) as z:
            self.assertNotIn('Metadata/plate_1.gcode', z.namelist())
            self.assertNotIn('Metadata/slice_info.config', z.namelist())
            self.assertEqual(z.read('Metadata/thumbnail.png'), b'original-png')
            self.assertEqual(json.loads(z.read('Metadata/project_settings.config')), {'layer_height': '0.12'})

    def test_archive_traversal_is_rejected(self):
        for index, member in enumerate(('../escape', '/absolute')):
            with self.subTest(member=member), self.assertRaises(ValueError):
                studio.project_inspect(str(self.project(member)))
        entry = zipfile.ZipInfo('safe')
        entry.filename = 'bad\\windows'
        archive = Mock()
        archive.infolist.return_value = [entry]
        with self.assertRaises(ValueError):
            studio.archive_members(archive)

    def test_xml_external_entity_is_rejected(self):
        source = self.project()
        xml = '<!DOCTYPE a [<!ENTITY x SYSTEM "file:///secret">]><model>&x;</model>'
        with self.assertRaises(Exception):
            studio.project_update(str(source), {}, [], {'3D/3dmodel.model': xml}, common.sha256(source), True)

    def test_only_allowed_existing_members_can_be_written(self):
        source = self.project()
        with self.assertRaises(ValueError):
            studio.project_update(str(source), {}, [], {'Metadata/new.gcode': 'G28'}, common.sha256(source), True)

    def test_slice_validation_requires_real_gcode(self):
        source = self.project()
        self.assertTrue(worker.verify_slice(source)['verified'])
        empty = self.root / 'empty.3mf'
        with zipfile.ZipFile(empty, 'w') as z:
            z.writestr('Metadata/plate_1.gcode', '')
        self.assertFalse(worker.verify_slice(empty)['verified'])
        self.assertFalse(worker.verify_slice(self.root / 'missing.3mf')['verified'])

    def test_mesh_slicing_needs_explicit_profiles(self):
        model = self.root / 'test.stl'
        model.write_text('solid test\nendsolid test\n')
        with self.assertRaises(ValueError):
            studio.slice_project([str(model)], None, None, [], {}, 0, True)

    def test_worker_zero_exit_without_toolpath_is_failure(self):
        job_id = str(uuid.uuid4())
        directory = common.state_dir() / 'jobs' / job_id
        common.atomic_json(directory / 'spec.json', {'executable': 'test', 'arguments': ['--outputdir', str(self.root)],
                                                    'timeout_seconds': 1, 'expect_toolpaths': True})
        process = Mock()
        process.poll.return_value = 0
        process.returncode = 0
        process.pid = 123
        with patch('worker.subprocess.Popen', return_value=process):
            worker.run(job_id)
        self.assertEqual(studio.job_status(job_id)['status'], 'failed_output_validation')


class FakeSocket:
    def __init__(self, incoming=b'', certificate=b'certificate'):
        self.incoming = incoming
        self.sent = []
        self.certificate = certificate
        self.closed = False

    def recv(self, count):
        # Deliberately fragment each read to verify framing.
        value, self.incoming = self.incoming[:min(count, 2)], self.incoming[min(count, 2):]
        return value

    def sendall(self, value):
        self.sent.append(value)

    def getpeercert(self, binary_form):
        return self.certificate

    def close(self):
        self.closed = True

    def settimeout(self, timeout):
        pass


class MQTTTests(Isolated):
    def data(self):
        return {'host': '192.168.1.2', 'serial': 'EXAMPLE123', 'access_code': 'TOPSECRET',
                'certificate_sha256': hashlib.sha256(b'certificate').hexdigest(), 'allow_control': True}

    def test_certificate_mismatch_sends_no_credentials(self):
        sock = FakeSocket(certificate=b'wrong')
        context = Mock()
        context.wrap_socket.return_value = sock
        with patch('printer.socket.create_connection', return_value=sock), patch('printer.tls_context', return_value=context):
            with self.assertRaises(ssl.SSLError):
                with printer.MQTT(self.data()):
                    self.fail('must fail before login')
        self.assertNotIn(b'TOPSECRET', b''.join(sock.sent))
        self.assertTrue(sock.closed)

    def test_fragmented_handshake_subscribe_and_publish_ack(self):
        incoming = printer.packet(0x20, b'\x00\x00') + printer.packet(0x90, b'\x00\x01\x00') + printer.packet(0x40, b'\x00\x02')
        sock = FakeSocket(incoming)
        context = Mock()
        context.wrap_socket.return_value = sock
        with patch('printer.socket.create_connection', return_value=sock), patch('printer.tls_context', return_value=context):
            with printer.MQTT(self.data()) as session:
                self.assertTrue(session.publish({'pushing': {'command': 'pushall'}}))
        self.assertTrue(sock.closed)
        self.assertIn(b'TOPSECRET', sock.sent[0])

    def test_qos_one_report_is_acknowledged_and_secret_redacted(self):
        session = printer.MQTT(self.data())
        session.sock = FakeSocket()
        body = printer.utf8(session.report_topic) + b'\x00\x09' + json.dumps({'print': {'access_code': 'SECRET', 'nozzle_temper': 20}}).encode()
        session.capture(0x32, body)
        self.assertEqual(session.sock.sent, [printer.packet(0x40, b'\x00\x09')])
        self.assertEqual(session.reports[0]['payload']['print']['access_code'], '<redacted>')

    def test_oversize_and_truncated_packets_fail(self):
        session = printer.MQTT(self.data())
        session.sock = FakeSocket(b'\x30\xff\xff\xff\x7f')
        with self.assertRaises(ValueError):
            session.receive()
        session.sock = FakeSocket(b'\x30\x04x')
        with self.assertRaises(ConnectionError):
            session.receive()

    def test_unconfirmed_or_disabled_writes_never_connect(self):
        data = self.data()
        data['allow_control'] = False
        with patch('printer.get_printer', return_value=data), patch('printer.MQTT') as network:
            with self.assertRaises(ValueError):
                printer.send('test', {'print': {'command': 'pause'}}, str(uuid.uuid4()), False)
            with self.assertRaises(ValueError):
                printer.send('test', {'print': {'command': 'pause'}}, str(uuid.uuid4()), True)
            network.assert_not_called()

    def test_control_deduplicates_and_never_claims_execution(self):
        session = Mock()
        session.reports = []
        session.publish.return_value = True
        network = Mock()
        network.__enter__ = Mock(return_value=session)
        network.__exit__ = Mock(return_value=False)
        request = str(uuid.uuid4())
        with patch('printer.get_printer', return_value=self.data()), patch('printer.MQTT', return_value=network):
            first = printer.control('test', 'pause', {}, request, True)
            second = printer.control('test', 'pause', {}, request, True)
        self.assertEqual(first['status'], 'broker_acknowledged')
        self.assertFalse(first['physical_execution_confirmed'])
        self.assertTrue(second['deduplicated'])
        session.publish.assert_called_once()

    def test_bad_remote_paths_and_public_hosts_are_refused(self):
        for path in ('../x', 'file\r\nDELE /x', 'a\\b', ''):
            with self.subTest(path=path), self.assertRaises(ValueError):
                printer.remote_path(path)
        for host in ('8.8.8.8', '127.0.0.1', 'https://printer', '0.0.0.0'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                printer.lan_address(host)

    def test_delete_hash_mismatch_preserves_printer_file(self):
        ftp = Mock()
        connection = Mock()
        connection.__enter__ = Mock(return_value=ftp)
        connection.__exit__ = Mock(return_value=False)
        def download(_, remote, local):
            local.write_bytes(b'changed')
        with patch('printer.get_printer', return_value=self.data()), patch('printer.ftp_connection', return_value=connection), patch('printer.download_into', side_effect=download):
            result = printer.file_delete('test', '/file.3mf', 'wrong-hash', str(uuid.uuid4()), True)
        self.assertEqual(result['status'], 'pending_or_unknown')
        ftp.delete.assert_not_called()


if __name__ == '__main__':
    unittest.main()
