"""Execute provisioning and diagnostics against the local Robot Dog guest."""
from pathlib import Path
import argparse
import shlex
import subprocess

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('command', nargs='?')
parser.add_argument('--file', type=Path)
parser.add_argument('--upload', nargs=2, metavar=('LOCAL', 'REMOTE'))
parser.add_argument('--download', nargs=2, metavar=('REMOTE', 'LOCAL'))
args = parser.parse_args()
ssh = [r'C:\Windows\System32\OpenSSH\ssh.exe', '-i', str(root / 'robot_vm_key'),
       '-p', '2255', '-o', 'StrictHostKeyChecking=accept-new',
       '-o', 'BatchMode=yes', '-o', f'UserKnownHostsFile={root / "known_hosts"}',
       '-o', 'ConnectTimeout=8', 'robot@127.0.0.1']
if args.file:
    result = subprocess.run(ssh + ['bash -s'], input=args.file.read_text(encoding='utf-8').encode())
elif args.upload:
    local, remote = args.upload
    with open(local, 'rb') as source:
        result = subprocess.run(ssh + ['cat > ' + shlex.quote(remote)], stdin=source)
elif args.download:
    remote, local = args.download
    with open(local, 'wb') as target:
        result = subprocess.run(ssh + ['cat ' + shlex.quote(remote)], stdout=target)
else:
    result = subprocess.run(ssh + [args.command])
raise SystemExit(result.returncode)
