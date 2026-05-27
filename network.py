"""
network.py – shared socket client for teacher and student GUIs.
Handles RSA handshake + AES session encryption.
All messages are pipe-delimited strings (no JSON).
"""

import socket
import os
import struct
import threading
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


HOST = "127.0.0.1"
PORT = 9998


# ─────────────────────────────────────────────
#  Crypto helpers (mirrors server.py)
# ─────────────────────────────────────────────

def aes_encrypt(key: bytes, plaintext: bytes) -> bytes:
    iv = os.urandom(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv))
    enc = cipher.encryptor()
    ciphertext = enc.update(plaintext) + enc.finalize()
    return iv + enc.tag + ciphertext


def aes_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    iv, tag, data = ciphertext[:12], ciphertext[12:28], ciphertext[28:]
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv, tag))
    dec = cipher.decryptor()
    try:
        return dec.update(data) + dec.finalize()
    except Exception as e:
        raise ValueError("Ciphertext tampered or invalid!") from e


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed")
        buf += chunk
    return buf


def send_frame(sock: socket.socket, data: bytes):
    length = struct.pack(">I", len(data))
    sock.sendall(length + data)


def recv_frame(sock: socket.socket) -> bytes:
    raw_len = _recv_exact(sock, 4)
    length = struct.unpack(">I", raw_len)[0]
    if length > 10_000_000:  # 10 MB limit
        raise ConnectionError("Payload exceeds max size (DoS protection)")
    return _recv_exact(sock, length)


# ─────────────────────────────────────────────
#  Network client
# ─────────────────────────────────────────────

class NetworkClient:
    def __init__(self):
        self.sock: socket.socket = None
        self.aes_key: bytes = None
        self._lock = threading.Lock()
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: threading.Thread = None
        self.disconnected: bool = False

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((HOST, PORT))
        self._handshake()

    def _handshake(self):
        """
        1. Receive server's RSA public key (PEM).
        2. Generate AES-256 session key.
        3. Send session key encrypted with server's RSA public key.
        """
        pub_pem = recv_frame(self.sock)
        server_pub = serialization.load_pem_public_key(pub_pem)

        self.aes_key = os.urandom(32)   # AES-256
        encrypted_key = server_pub.encrypt(
            self.aes_key,
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                         algorithm=hashes.SHA256(), label=None)
        )
        send_frame(self.sock, encrypted_key)

    def send(self, message: str):
        data = aes_encrypt(self.aes_key, message.encode())
        send_frame(self.sock, data)

    def recv(self) -> str:
        data = recv_frame(self.sock)
        return aes_decrypt(self.aes_key, data).decode()

    def request(self, message: str) -> str:
        """Send a command and return the server's response."""
        try:
            with self._lock:
                self.send(message)
                return self.recv()
        except (ConnectionError, socket.timeout, OSError) as e:
            self.disconnected = True
            raise

    def start_watchdog(self, interval: int = 5):
        """
        Background thread that PINGs the server every *interval* seconds.
        Sets self.disconnected=True if the server crashes or stops responding.
        Also detects a frozen server: if the main thread holds the socket lock
        for longer than *interval*+3 seconds, the request has hung.
        """
        self._watchdog_stop.clear()
        self.disconnected = False
        # Socket-level timeout so recv() never blocks forever
        try:
            self.sock.settimeout(interval + 3)
        except Exception:
            pass

        def _run():
            while not self._watchdog_stop.wait(interval):
                if self.disconnected:
                    return
                # Try to acquire the lock; if blocked for too long the main
                # thread is stuck waiting on a frozen server.
                acquired = self._lock.acquire(timeout=interval + 3)
                if not acquired:
                    self.disconnected = True
                    return
                try:
                    self.send("PING")
                    resp = self.recv()
                    if resp != "PONG":
                        self.disconnected = True
                        return
                except Exception:
                    self.disconnected = True
                    return
                finally:
                    self._lock.release()

        self._watchdog_thread = threading.Thread(
            target=_run, daemon=True, name="net-watchdog"
        )
        self._watchdog_thread.start()

    def stop_watchdog(self):
        self._watchdog_stop.set()

    def close(self):
        self.stop_watchdog()
        if self.sock:
            self.sock.close()
