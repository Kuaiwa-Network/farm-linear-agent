"""No CLI executes until the host has assigned this gate to its Windows job."""
import os
import subprocess
import sys

if __name__ == '__main__':
    # Unbuffered one-byte handshake: leave the complete prompt for the real CLI.
    if os.read(sys.stdin.fileno(), 1) != b'G':
        raise SystemExit(125)
    # argv[1] is the attempt directory, retaining inspectable ownership in the gate command line.
    process = subprocess.Popen(sys.argv[2:], stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
    raise SystemExit(process.wait())
