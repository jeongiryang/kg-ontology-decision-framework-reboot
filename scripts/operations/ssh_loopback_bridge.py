"""Private Windows-to-WSL SSH stdio relay; both service ends stay on loopback."""
from __future__ import annotations
import argparse
import os
import re
import socket
import subprocess
import threading


def relay(client: socket.socket, command: list[str], slots: threading.BoundedSemaphore):
    child = None
    try:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        def upload():
            try:
                while data := client.recv(65536):
                    child.stdin.write(data)
                    child.stdin.flush()
            except (OSError, ValueError):
                pass
            finally:
                try:
                    child.stdin.close()
                except (OSError, ValueError):
                    pass
        threading.Thread(target=upload, daemon=True).start()
        while data := os.read(child.stdout.fileno(), 65536):
            client.sendall(data)
    except (OSError, ValueError):
        pass
    finally:
        client.close()
        if child is not None:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
        slots.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alias", required=True, help="existing own-account WSL SSH alias")
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--remote-port", type=int, required=True)
    parser.add_argument("--distro", default="Ubuntu-24.04")
    args = parser.parse_args()
    if (not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.alias)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args.distro)
            or not 1024 <= args.listen_port <= 65535 or not 1024 <= args.remote_port <= 65535):
        parser.error("invalid private relay configuration")
    command = ["wsl.exe", "-d", args.distro, "--", "ssh", "-o", "BatchMode=yes",
               "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=15",
               "-o", "ServerAliveCountMax=2", "-W", f"127.0.0.1:{args.remote_port}", args.alias]
    slots = threading.BoundedSemaphore(4)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", args.listen_port))
        listener.listen(4)
        while True:
            client, _ = listener.accept()
            if not slots.acquire(blocking=False):
                client.close()
                continue
            threading.Thread(target=relay, args=(client, command, slots), daemon=True).start()


if __name__ == "__main__":
    main()
