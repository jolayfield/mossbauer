"""Opt-in compact evidence return through the user's existing OpenSSH configuration."""
from __future__ import annotations

import os
from pathlib import Path
import selectors
import shlex
import subprocess
import tempfile
import time

from . import compact, harvest, plan as P, project as J
from .site import SSH_HOST

TIMEOUT = 30
STDERR_LIMIT = 16_384


class RemoteError(J.ProjectError):
    """A failed SSH return; no downloaded evidence has been installed."""


def ssh_command(host: str, remote_path: str) -> list[str]:
    """Build fixed SSH arguments and a quoted, read-only remote command. No local shell."""
    if not isinstance(host, str) or len(host) > 253 or not SSH_HOST.fullmatch(host):
        raise RemoteError('Use an SSH host alias or user@hostname (configure ports and identities in ~/.ssh/config).')
    if (not isinstance(remote_path, str) or not remote_path.startswith('/') or remote_path.endswith('/')
            or len(remote_path) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in remote_path)):
        raise RemoteError('Give the absolute remote bundle filename, without control characters.')
    remote = f'head -c {harvest.MAX_BYTES + 1} < {shlex.quote(remote_path)}'
    return ['ssh', '-T', '-n', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'PermitLocalCommand=no', '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2', host, remote]


def _download(command: list[str], destination: Path) -> None:
    """Bound stdout, stderr and elapsed time; reap SSH on every exit (macOS/Linux pipes)."""
    if os.name != 'posix':
        raise RemoteError('SSH return currently supports macOS/Linux; use compact file import on this platform.')
    try:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise RemoteError('Could not start OpenSSH; install ssh and configure access outside the app.') from exc
    deadline = time.monotonic() + TIMEOUT
    errors = bytearray()
    total = 0
    try:
        with selectors.DefaultSelector() as selector, destination.open('wb') as output:
            selector.register(proc.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(proc.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteError(f'SSH return timed out after {TIMEOUT} seconds; check the host and remote file.')
                for key, _ in selector.select(min(remaining, 0.25)):
                    chunk = os.read(key.fd, 65_536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.data == 'stdout':
                        total += len(chunk)
                        if total > harvest.MAX_BYTES:
                            raise RemoteError('Remote compact bundle exceeds 5 MB; nothing imported.')
                        output.write(chunk)
                    else:
                        errors.extend(chunk)
                        if len(errors) > STDERR_LIMIT:
                            raise RemoteError('SSH produced excessive error output; nothing imported.')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RemoteError(f'SSH return timed out after {TIMEOUT} seconds.')
            try:
                code = proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                raise RemoteError(f'SSH return timed out after {TIMEOUT} seconds.') from exc
        if code:
            detail = ''.join(c for c in errors.decode('utf-8', errors='replace') if c.isprintable() or c == '\n').strip()
            raise RemoteError(f'SSH return failed (exit {code}): {detail[:1000] or "check SSH access and the remote filename"}')
        if not total:
            raise RemoteError('The remote file was empty; nothing imported.')
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def fetch_results(root, entry: str, version: int | None, host: str, remote_path: str, *, replace: bool = False) -> dict:
    """Fetch one compact bundle, then apply the ordinary all-record validation/import."""
    command = ssh_command(host, remote_path)
    vd = J.version_dir(root, entry, version)
    pinned_version = int(vd.name[1:])
    plan = J.load_version(root, entry, pinned_version)
    if not P.plan_integrity(plan):
        raise RemoteError('Plan integrity mismatch; review the local plan before fetching evidence.')
    plan_hash = P.compute_hash(plan)
    with tempfile.TemporaryDirectory(prefix='mossbauer-return-') as temp:
        source = Path(temp) / 'bundle.json'
        _download(command, source)
        if P.compute_hash(J.load_version(root, entry, pinned_version)) != plan_hash:
            raise RemoteError('The selected plan changed during transfer; nothing imported. Review it and retry.')
        try:
            return compact.import_bundle(root, entry, pinned_version, source, replace)
        except RecursionError as exc:
            raise RemoteError('Remote bundle has excessive JSON nesting; nothing imported.') from exc
