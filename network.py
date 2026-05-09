"""
network.py – shared socket client for teacher and student GUIs.
Handles RSA handshake + AES session encryption.
All messages are pipe-delimited strings (no JSON).
"""

import socket
import os
import struct
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


HOST = "127.0.0.1"
PORT = 9999


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
        self.send(message)
        return self.recv()

    def close(self):
        if self.sock:
            self.sock.close()
