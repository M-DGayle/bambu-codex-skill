"""Local Bambu discovery, pinned TLS MQTT, and implicit FTPS. No cloud impersonation."""
from __future__ import annotations

import contextlib
import ftplib
import hashlib
import hmac
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import select
import socket
import ssl
import struct
import time
import uuid

import psutil

from common import artifact_dir, atomic_json, config, once, read_json, redact, require_write, sha256
from studio import data_directory


def lan_address(host: str) -> str:
    address = ipaddress.ip_address(host)
    if not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local:
        raise ValueError('Use the printer literal private LAN address, not a public host or URL.')
    return str(address)


def saved_devices() -> dict:
    path = data_directory() / 'BambuStudio.conf'
    if not path.exists():
        return {}
    return read_json(path).get('access_code', {})


def discovery(seconds: int = 6) -> dict:
    if not 1 <= seconds <= 20:
        raise ValueError('Discovery duration must be 1-20 seconds.')
    saved = saved_devices()
    devices = {}
    sockets = []
    errors = []
    interfaces = {'0.0.0.0'}
    for addresses in psutil.net_if_addrs().values():
        for address in addresses:
            if address.family == socket.AF_INET:
                try:
                    interfaces.add(lan_address(address.address))
                except ValueError:
                    pass
    try:
        for port in (1990, 2021):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('', port))
                for interface in interfaces:
                    try:
                        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                        socket.inet_aton('239.255.255.250') + socket.inet_aton(interface))
                    except OSError:
                        pass
                sockets.append(sock)
            except OSError:
                errors.append(f'UDP {port} listener unavailable')
                sock.close()
        for interface in interfaces:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            try:
                sock.bind((interface, 0))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                if interface != '0.0.0.0':
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
                request = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1990\r\n'
                           'MAN: "ssdp:discover"\r\nMX: 1\r\nST: urn:bambulab-com:device:3dprinter:1\r\n\r\n').encode()
                sock.sendto(request, ('239.255.255.250', 1990))
                sockets.append(sock)
            except OSError:
                sock.close()
        deadline = time.monotonic() + seconds
        while sockets and time.monotonic() < deadline:
            ready, _, _ = select.select(sockets, [], [], min(0.5, max(0, deadline - time.monotonic())))
            for sock in ready:
                payload, peer = sock.recvfrom(8192)
                text = payload.decode('utf-8', errors='replace')
                if 'bambu' not in text.lower():
                    continue
                headers = {}
                for line in text.splitlines()[1:]:
                    if ':' in line:
                        key, value = line.split(':', 1)
                        headers[key.strip().lower()] = value.strip()
                serial = headers.get('usn', '').removeprefix('uuid:').split('::')[0]
                if not re.fullmatch(r'[A-Za-z0-9-]{5,64}', serial):
                    continue
                try:
                    host = lan_address(peer[0])
                except ValueError:
                    continue
                devices[serial] = {'serial': serial, 'host': host, 'name': headers.get('devname'),
                                   'model': headers.get('devmodel.bambu.com'),
                                   'saved_access_code_available': bool(saved.get(serial)),
                                   'identity_verified': False, 'source': 'LAN SSDP announcement'}
    finally:
        for sock in sockets:
            sock.close()
    return {'devices': list(devices.values()), 'saved_printer_count': len(saved), 'listener_notes': errors,
            'note': 'Discovery is unauthenticated. Verify the printer identity/certificate before importing credentials. No subnet scan or cloud login.'}


def probe_certificate(host: str, port: int = 8883) -> dict:
    host = lan_address(host)
    if port not in (8883, 990):
        raise ValueError('Only MQTT 8883 and implicit FTPS 990 are supported.')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname=host) as secured:
            return {'host': host, 'port': port, 'sha256': hashlib.sha256(secured.getpeercert(binary_form=True)).hexdigest(),
                    'authenticated': False, 'note': 'Certificate observation only; no credentials sent.'}


def get_printer(alias: str) -> dict:
    data = config().get('printers', {}).get(alias)
    if not data:
        raise ValueError('Printer is not configured. Discover it and run private setup first.')
    lan_address(data['host'])
    if not re.fullmatch(r'[A-Za-z0-9-]{5,64}', data['serial']):
        raise ValueError('Invalid printer serial.')
    if not data.get('access_code'):
        raise ValueError('Printer access code is missing.')
    return data


def tls_context(data: dict) -> ssl.SSLContext:
    if data.get('ca_file'):
        return ssl.create_default_context(cafile=data['ca_file'])
    if not re.fullmatch('[0-9a-fA-F]{64}', data.get('certificate_sha256', '')):
        raise ValueError('Configure a trusted CA file or SHA-256 certificate fingerprint first.')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def check_pin(sock, data: dict) -> None:
    fingerprint = data.get('certificate_sha256')
    if fingerprint and not hmac.compare_digest(hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest(), fingerprint.lower()):
        raise ssl.SSLError('Printer certificate changed; no credentials sent. Re-verify the printer.')


def utf8(value: str) -> bytes:
    encoded = value.encode('utf-8')
    if len(encoded) > 65535 or '\x00' in value:
        raise ValueError('Invalid MQTT string.')
    return struct.pack('!H', len(encoded)) + encoded


def packet(kind: int, body: bytes) -> bytes:
    remaining = len(body)
    if remaining > 1024 * 1024:
        raise ValueError('MQTT packet exceeds 1 MiB.')
    header = bytearray([kind])
    while True:
        digit = remaining % 128
        remaining //= 128
        header.append(digit | (128 if remaining else 0))
        if not remaining:
            return bytes(header) + body


class MQTT:
    """A bounded MQTT 3.1.1 session; pin validation precedes the credential handshake."""
    def __init__(self, data: dict, timeout: int = 15):
        self.data = data
        self.timeout = timeout
        self.sock = None
        self.reports = []
        self.report_topic = f"device/{data['serial']}/report"
        self.request_topic = f"device/{data['serial']}/request"

    def __enter__(self):
        try:
            raw = socket.create_connection((self.data['host'], 8883), timeout=self.timeout)
            try:
                self.sock = tls_context(self.data).wrap_socket(raw, server_hostname=self.data['host'])
            except BaseException:
                raw.close()
                raise
            check_pin(self.sock, self.data)
            self.sock.sendall(packet(0x10, utf8('MQTT') + bytes([4, 0xC2]) + struct.pack('!H', 30)
                                     + utf8('bambu-bridge-' + uuid.uuid4().hex[:10]) + utf8('bblp') + utf8(self.data['access_code'])))
            kind, body = self.receive()
            if kind != 0x20 or body != b'\x00\x00':
                raise ConnectionError('Printer rejected MQTT authentication.')
            self.sock.sendall(packet(0x82, b'\x00\x01' + utf8(self.report_topic) + b'\x00'))
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                kind, body = self.receive()
                if kind == 0x90 and body[:2] == b'\x00\x01':
                    if len(body) != 3 or body[2] not in (0, 1):
                        raise ConnectionError('Printer rejected report subscription.')
                    return self
                self.capture(kind, body)
            raise TimeoutError('No subscription acknowledgement.')
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.sock:
            with contextlib.suppress(OSError):
                self.sock.sendall(b'\xe0\x00')
            self.sock.close()

    def read_exact(self, count: int) -> bytes:
        result = bytearray()
        while len(result) < count:
            remaining = self.packet_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('MQTT packet deadline exceeded.')
            self.sock.settimeout(remaining)
            block = self.sock.recv(count - len(result))
            if not block:
                raise ConnectionError('Printer closed MQTT connection.')
            result.extend(block)
        return bytes(result)

    def receive(self, budget: float | None = None) -> tuple[int, bytes]:
        self.packet_deadline = time.monotonic() + (self.timeout if budget is None else budget)
        kind = self.read_exact(1)[0]
        length = 0
        for index in range(4):
            byte = self.read_exact(1)[0]
            length += (byte & 127) * (128 ** index)
            if length > 1024 * 1024:
                raise ValueError('Printer MQTT report exceeds 1 MiB.')
            if not byte & 128:
                return kind, self.read_exact(length)
        raise ValueError('Malformed MQTT remaining length.')

    def capture(self, kind: int, body: bytes) -> None:
        if kind >> 4 != 3:
            return
        if len(body) < 2:
            raise ValueError('Truncated MQTT publish.')
        size = struct.unpack('!H', body[:2])[0]
        if len(body) < size + 2:
            raise ValueError('Truncated topic.')
        topic = body[2:2 + size].decode()
        offset = 2 + size
        qos = (kind >> 1) & 3
        if qos == 1:
            if len(body) < offset + 2:
                raise ValueError('Missing packet identifier.')
            self.sock.sendall(packet(0x40, body[offset:offset + 2]))
            offset += 2
        elif qos:
            raise ValueError('Unsupported incoming MQTT QoS.')
        if topic == self.report_topic and len(self.reports) < 200:
            value = json.loads(body[offset:])
            if isinstance(value, dict):
                self.reports.append({'received_at': time.time(), 'payload': redact(value)})

    def publish(self, value: dict) -> bool:
        payload = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
        self.sock.sendall(packet(0x32, utf8(self.request_topic) + b'\x00\x02' + payload))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            kind, body = self.receive()
            if kind == 0x40 and body == b'\x00\x02':
                return True
            self.capture(kind, body)
        raise TimeoutError('MQTT publish acknowledgement missing.')

    def collect(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                kind, body = self.receive(max(0.01, deadline - time.monotonic()))
                self.capture(kind, body)
            except socket.timeout:
                break


def merge_report(target: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_report(target[key], value)
        else:
            target[key] = value


def status(alias: str, seconds: int = 5) -> dict:
    if not 1 <= seconds <= 20:
        raise ValueError('Observation must be 1-20 seconds.')
    data = get_printer(alias)
    with MQTT(data) as session:
        session.publish({'pushing': {'sequence_id': '0', 'command': 'pushall'}})
        session.publish({'info': {'sequence_id': '1', 'command': 'get_version'}})
        session.collect(seconds)
        merged = {}
        for report in session.reports:
            merge_report(merged, report['payload'])
        telemetry = merged.get('print', {})
        has_telemetry = bool(set(telemetry) - {'command', 'sequence_id', 'result', 'reason'})
        return {'printer': alias, 'status': 'telemetry_received' if has_telemetry else 'no_print_telemetry',
                'observed_at': time.time(), 'report_count': len(session.reports), 'telemetry': merged,
                'complete_snapshot_guaranteed': False, 'note': 'Only fields received during this connection; missing fields are unknown.'}


def send(alias: str, payload: dict, request_id: str, confirmed: bool) -> dict:
    require_write(confirmed)
    data = get_printer(alias)
    if data.get('allow_control') is not True:
        raise ValueError('Printer writes are disabled. Enable allow_control in private setup after verifying LAN/Developer Mode.')
    if len(payload) != 1:
        raise ValueError('Send exactly one command section per request.')
    channel, command = next(iter(payload.items()))
    if not re.fullmatch('[a-z][a-z0-9_]{0,63}', channel) or not isinstance(command, dict) or not isinstance(command.get('command'), str):
        raise ValueError('Expected a command section, e.g. {"print":{"command":"pause"}}.')
    if 'sequence_id' in command:
        raise ValueError('The connector assigns sequence_id for response correlation.')
    # Validate before reserving a write or opening the network.
    json.dumps(payload, allow_nan=False)
    intent = {'alias': alias, 'serial': data['serial'], 'host': data['host'], 'payload': payload}

    def action():
        sequence = str(int(uuid.UUID(request_id)) % 2147483647)
        actual = {channel: {**command, 'sequence_id': sequence}}
        with MQTT(data) as session:
            session.publish(actual)
            session.collect(2)
            replies = [r for r in session.reports if str(r['payload'].get(channel, {}).get('sequence_id')) == sequence]
            return {'status': 'broker_acknowledged', 'sequence_id': sequence, 'printer_replies': replies,
                    'physical_execution_confirmed': False,
                    'note': 'MQTT acknowledgement is delivery evidence only. Inspect reply and fresh status; do not replay an uncertain action.'}

    return once(request_id, intent, action)


def control(alias: str, command: str, parameters: dict, request_id: str, confirmed: bool) -> dict:
    if command in ('pause', 'resume', 'stop'):
        if parameters:
            raise ValueError('This command has no parameters; use printer_command for advanced payloads.')
        payload = {'print': {'command': command, 'param': ''}}
    elif command == 'gcode':
        if set(parameters) != {'gcode'} or not isinstance(parameters['gcode'], str):
            raise ValueError('Provide the exact gcode string.')
        payload = {'print': {'command': 'gcode_line', 'param': parameters['gcode'].rstrip() + '\n'}}
    elif command == 'speed':
        speed = parameters.get('level')
        if type(speed) is not int or speed not in (1, 2, 3, 4) or set(parameters) != {'level'}:
            raise ValueError('Speed level must be 1 (silent), 2 (standard), 3 (sport), or 4 (ludicrous).')
        payload = {'print': {'command': 'print_speed', 'param': str(speed)}}
    else:
        raise ValueError('Use pause/resume/stop/speed/gcode, or printer_command for other firmware commands.')
    return send(alias, payload, request_id, confirmed)


def remote_path(value: str) -> str:
    if not isinstance(value, str) or not value or any(c in value for c in '\r\n\x00\\'):
        raise ValueError('Invalid printer path.')
    path = PurePosixPath(value)
    if '..' in path.parts:
        raise ValueError('Parent traversal is not allowed.')
    return str(path)


class ImplicitFTP(ftplib.FTP_TLS):
    def __init__(self, data: dict):
        self.data = data
        super().__init__(context=tls_context(data), timeout=15)

    def connect(self, host='', port=990, timeout=15, source_address=None):
        self.host = host
        self.port = port
        raw = socket.create_connection((host, port), timeout=timeout)
        try:
            self.sock = self.context.wrap_socket(raw, server_hostname=host)
            check_pin(self.sock, self.data)
            self.af = self.sock.family
            self.file = self.sock.makefile('r', encoding=self.encoding)
            self.welcome = self.getresp()
            return self.welcome
        except BaseException:
            raw.close()
            self.close()
            raise

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            try:
                conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
                check_pin(conn, self.data)
            except BaseException:
                conn.close()
                raise
        return conn, size


@contextlib.contextmanager
def ftp_connection(alias: str):
    data = get_printer(alias)
    with ImplicitFTP(data) as ftp:
        ftp.connect(data['host'])
        ftp.login('bblp', data['access_code'])
        ftp.prot_p()
        yield ftp


def files_list(alias: str, directory: str = '/') -> dict:
    directory = remote_path(directory)
    with ftp_connection(alias) as ftp:
        try:
            entries = [{'name': name, **facts} for name, facts in ftp.mlsd(directory)]
        except ftplib.error_perm as exc:
            if str(exc)[:3] not in ('500', '502', '504'):
                raise
            entries = [{'name': name} for name in ftp.nlst(directory)]
    return {'printer': alias, 'directory': directory, 'entries': entries}


def download_into(ftp, remote: str, local: Path, max_bytes: int = 1024**3) -> None:
    size = 0
    with local.open('xb') as stream:
        def consume(block):
            nonlocal size
            size += len(block)
            if size > max_bytes:
                raise ValueError('Printer file exceeds download limit.')
            stream.write(block)
        ftp.retrbinary('RETR ' + remote_path(remote), consume)


def file_download(alias: str, remote: str) -> dict:
    remote = remote_path(remote)
    local = artifact_dir() / PurePosixPath(remote).name
    with ftp_connection(alias) as ftp:
        download_into(ftp, remote, local)
    return {'path': str(local), 'bytes': local.stat().st_size, 'sha256': sha256(local)}


def file_upload(alias: str, local: str, remote: str, request_id: str, confirmed: bool) -> dict:
    require_write(confirmed)
    data = get_printer(alias)
    if data.get('allow_control') is not True:
        raise ValueError('Printer writes are disabled.')
    source = Path(local).expanduser().resolve(strict=True)
    remote = remote_path(remote)
    if not source.is_file() or source.stat().st_size > 1024**3:
        raise ValueError('Upload requires a file no larger than 1 GiB.')
    digest = sha256(source)

    def action():
        with ftp_connection(alias) as ftp:
            parent = str(PurePosixPath(remote).parent)
            names = ftp.nlst(parent)
            if PurePosixPath(remote).name in {PurePosixPath(n).name for n in names}:
                raise ValueError('Destination exists; overwriting printer files is refused.')
            temporary = remote + '.' + uuid.uuid4().hex + '.part'
            with source.open('rb') as stream:
                ftp.storbinary('STOR ' + temporary, stream)
            check = artifact_dir() / 'upload-verification.bin'
            download_into(ftp, temporary, check)
            if sha256(check) != digest:
                raise ValueError('Printer upload hash mismatch; temporary file retained for inspection.')
            # Recheck final name immediately before the rename. There is no atomic no-clobber FTP primitive.
            if PurePosixPath(remote).name in {PurePosixPath(n).name for n in ftp.nlst(parent)}:
                raise ValueError('Destination appeared during upload; temporary file retained.')
            ftp.rename(temporary, remote)
        return {'status': 'uploaded_and_readback_verified', 'remote': remote, 'sha256': digest,
                'print_started': False, 'temporary_remote': temporary}

    return once(request_id, {'upload': remote, 'sha256': digest, 'alias': alias, 'serial': data['serial']}, action)


def file_delete(alias: str, remote: str, expected_sha256: str, request_id: str, confirmed: bool) -> dict:
    require_write(confirmed)
    data = get_printer(alias)
    if data.get('allow_control') is not True:
        raise ValueError('Printer writes are disabled.')
    remote = remote_path(remote)

    def action():
        backup = artifact_dir() / PurePosixPath(remote).name
        with ftp_connection(alias) as ftp:
            download_into(ftp, remote, backup)
            if sha256(backup) != expected_sha256:
                raise ValueError('Printer file changed; backup preserved and delete refused.')
            ftp.delete(remote)
        return {'status': 'deleted', 'backup': str(backup), 'sha256': expected_sha256, 'remote': remote}

    return once(request_id, {'delete': remote, 'sha256': expected_sha256, 'alias': alias, 'serial': data['serial']}, action)
