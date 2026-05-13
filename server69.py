# -*- coding: utf-8 -*-
"""
Quizy Server
=================
Handles teacher and student connections using sockets + select.
All messages are pipe-delimited strings (no JSON).
Uses RSA encryption for the session key exchange, then AES for data.
SQLite3 stores all persistent data.
"""

import socket
import select
import sqlite3
import threading
import hashlib
import os
import re
import struct
import time
import smtplib
import random
from email.message import EmailMessage
from collections import defaultdict
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# ─────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────
HOST = "127.0.0.1"
PORT = 9999
DB_PATH = "exam_system.db"
BUFFER = 4096

# ── Email Settings (For Account Recovery) ──
# To use this, generate an "App Password" in your Google Account Settings.
# 1. Go to Google Account Settings → Security → 2-Step Verification → App passwords
# 2. Generate a new app password for "Mail" on "Other (custom name)"
# 3. Replace the values below with your email and app password
GMAIL_SENDER = "ophirtelner@gmail.com"
GMAIL_PASSWORD = "wjtfvwrguobdsarq"


# ─────────────────────────────────────────────
#  Crypto helpers
# ─────────────────────────────────────────────

def generate_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return private_key, public_key


def rsa_encrypt(public_key, data: bytes) -> bytes:
    return public_key.encrypt(
        data,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None)
    )


def rsa_decrypt(private_key, data: bytes) -> bytes:
    return private_key.decrypt(
        data,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                     algorithm=hashes.SHA256(), label=None)
    )


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


def hash_password(password: str, salt: bytes = None) -> tuple[str, str]:
    if salt is None:
        salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100_000)
    return key.hex(), salt.hex()


def validate_password(password: str) -> str | None:
    """
    Returns an error message string if the password is invalid,
    or None if the password passes all checks.
    Requirements: min 4 chars, at least one uppercase letter,
    one digit, and one special character.
    """
    if len(password) < 4:
        return "Password too short (min 4 chars)"
    if not re.search(r'[A-Z]', password):
        return "Password must contain at least one uppercase letter"
    if not re.search(r'[0-9]', password):
        return "Password must contain at least one number"
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};:\'"\\|,.<>/?`~]', password):
        return "Password must contain at least one special character"
    return None


# ─────────────────────────────────────────────
#  Wire protocol: length-prefixed frames
#  [4-byte big-endian length][payload bytes]
# ─────────────────────────────────────────────

def send_frame(sock: socket.socket, data: bytes):
    length = struct.pack(">I", len(data))
    sock.sendall(length + data)


def recv_frame(sock: socket.socket) -> bytes:
    raw_len = _recv_exact(sock, 4)
    if not raw_len:
        return b""
    length = struct.unpack(">I", raw_len)[0]
    if length > 10_000_000:  # 10 MB limit
        raise ConnectionError("Payload exceeds max size (DoS protection)")
    return _recv_exact(sock, length)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return b""
        buf += chunk
    return buf


# ─────────────────────────────────────────────
#  Database
# ─────────────────────────────────────────────

def init_db(path: str):
    conn = sqlite3.connect(path, check_same_thread=False)
    # ── CRITICAL: enforce foreign-key constraints in SQLite ──────────────────
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT    UNIQUE NOT NULL,
            email    TEXT    UNIQUE NOT NULL,
            password TEXT    NOT NULL,
            salt     TEXT    NOT NULL,
            role     TEXT    NOT NULL CHECK(role IN ('teacher','student','admin'))
        );

        CREATE TABLE IF NOT EXISTS classes (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT    UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS class_members (
            class_id INTEGER REFERENCES classes(id) ON DELETE CASCADE,
            user_id  INTEGER REFERENCES users(id)  ON DELETE CASCADE,
            PRIMARY KEY (class_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS tests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            teacher_id  INTEGER REFERENCES users(id),
            created_at  TEXT DEFAULT (datetime('now')),
            time_limit  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS questions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id     INTEGER REFERENCES tests(id) ON DELETE CASCADE,
            position    INTEGER NOT NULL,
            qtype       TEXT NOT NULL CHECK(qtype IN ('mc','tf','short')),
            prompt      TEXT NOT NULL,
            option_a    TEXT,
            option_b    TEXT,
            option_c    TEXT,
            option_d    TEXT,
            answer      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS submissions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id     INTEGER REFERENCES tests(id),
            student_id  INTEGER REFERENCES users(id),
            score       REAL,
            submitted_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS answers (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER REFERENCES submissions(id) ON DELETE CASCADE,
            question_id   INTEGER REFERENCES questions(id),
            student_ans   TEXT
        );

        CREATE TABLE IF NOT EXISTS activity_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER REFERENCES users(id),
            username    TEXT,
            action      TEXT NOT NULL,
            details     TEXT,
            ip_address  TEXT,
            timestamp   TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()

    # ── Migrations for older databases ────────────────────────────────────────
    cur.execute("PRAGMA table_info(tests)")
    test_cols = {row[1] for row in cur.fetchall()}
    if "time_limit" not in test_cols:
        cur.execute("ALTER TABLE tests ADD COLUMN time_limit INTEGER DEFAULT 0")
        conn.commit()
        print("[DB] Migrated: added 'time_limit' column to tests")

    # ── Seed default admin account (if not present) ───────────────────────────
    admin_pw, admin_salt = hash_password("Admin@123")
    cur.execute("SELECT id FROM users WHERE username='admin'")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (username, email, password, salt, role) VALUES ('admin','admin@quizy.local',?,?,?)",
            (admin_pw, admin_salt, 'admin')
        )
        conn.commit()
        print("[DB] Default admin account created: admin / Admin@123")

    return conn


# ─────────────────────────────────────────────
#  Activity Logging
# ─────────────────────────────────────────────

def log_activity(db_conn: sqlite3.Connection, user_id: int, username: str, action: str, details: str = "", ip_address: str = ""):
    """Log user activity to the activity_logs table."""
    try:
        cur = db_conn.cursor()
        cur.execute("""
            INSERT INTO activity_logs (user_id, username, action, details, ip_address)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, username, action, details, ip_address))
        db_conn.commit()
        print(f"[LOG] {username}: {action} - {details}")
    except Exception as e:
        print(f"[LOG ERROR] Failed to log activity: {e}")


# ─────────────────────────────────────────────
#  OOP User hierarchy
# ─────────────────────────────────────────────

class BaseUser:
    """
    Abstract base representing any authenticated user.
    Holds identity data shared by all roles.
    """
    def __init__(self, user_id: int, username: str, role: str):
        self.user_id = user_id
        self.username = username
        self.role = role

    def __repr__(self):
        return f"<{self.__class__.__name__} id={self.user_id} username={self.username!r}>"

    def can_create_test(self) -> bool:
        """Override in subclasses to grant permission."""
        return False

    def can_submit_test(self) -> bool:
        return False


class Teacher(BaseUser):
    """A teacher: can create, edit, and delete tests; view all results."""
    def __init__(self, user_id: int, username: str):
        super().__init__(user_id, username, role="teacher")

    def can_create_test(self) -> bool:
        return True

    def can_submit_test(self) -> bool:  # teachers don't submit
        return False


class Student(BaseUser):
    """A student: can browse tests, submit answers, and view own results."""
    def __init__(self, user_id: int, username: str):
        super().__init__(user_id, username, role="student")

    def can_create_test(self) -> bool:
        return False

    def can_submit_test(self) -> bool:
        return True


class Admin(BaseUser):
    """An admin: can manage classes and assign users to them."""
    def __init__(self, user_id: int, username: str):
        super().__init__(user_id, username, role="admin")

    def can_create_test(self) -> bool:
        return False

    def can_submit_test(self) -> bool:
        return False


# ─────────────────────────────────────────────
#  Security constants
# ─────────────────────────────────────────────

# Login brute-force
MAX_LOGIN_FAILURES  = 5
LOCKOUT_SECONDS     = 60

# Connection limits
MAX_CONNS_PER_IP    = 3    # simultaneous connections from one IP
MAX_TOTAL_CONNS     = 50   # global ceiling across all IPs

# Timeouts
HANDSHAKE_TIMEOUT   = 10   # seconds to complete RSA handshake
IDLE_TIMEOUT        = 300  # seconds of silence before disconnect

# Command rate limiting
MAX_CMDS_PER_MINUTE = 60   # commands per IP per minute before lockout

# ─────────────────────────────────────────────
#  Brute-force / rate-limit tracker
# ─────────────────────────────────────────────

# Maps IP -> (failure_count, lockout_until_timestamp)
_login_attempts: dict = defaultdict(lambda: [0, 0.0])


def record_login_failure(ip: str) -> bool:
    """
    Record a failed login attempt from 'ip'.
    Returns True if the IP is now locked out.
    """
    entry = _login_attempts[ip]
    entry[0] += 1
    if entry[0] >= MAX_LOGIN_FAILURES:
        entry[1] = time.time() + LOCKOUT_SECONDS
    return entry[0] >= MAX_LOGIN_FAILURES


def is_locked_out(ip: str) -> bool:
    """Returns True if the IP is currently locked out."""
    entry = _login_attempts[ip]
    if entry[1] > time.time():
        return True
    if entry[1] != 0.0 and entry[1] <= time.time():
        # Lockout expired — reset
        _login_attempts[ip] = [0, 0.0]
    return False


def reset_login_attempts(ip: str):
    """Clear failure count on successful login."""
    _login_attempts[ip] = [0, 0.0]


# ─────────────────────────────────────────────
#  Connection tracker  (thread-safe)
# ─────────────────────────────────────────────

_conn_lock              = threading.Lock()
_conns_per_ip: dict     = defaultdict(int)   # IP -> active count
_total_conns: list      = [0]                # [count]  (list so we can mutate in closure)


def _conn_acquire(ip: str) -> bool:
    """Try to register a new connection. Returns False if limits are exceeded."""
    with _conn_lock:
        if _conns_per_ip[ip] >= MAX_CONNS_PER_IP:
            return False
        if _total_conns[0] >= MAX_TOTAL_CONNS:
            return False
        _conns_per_ip[ip] += 1
        _total_conns[0]   += 1
        return True


def _conn_release(ip: str):
    """Unregister a connection when it closes."""
    with _conn_lock:
        _conns_per_ip[ip] = max(0, _conns_per_ip[ip] - 1)
        _total_conns[0]   = max(0, _total_conns[0] - 1)


# ─────────────────────────────────────────────
#  Command rate limiter  (per IP)
# ─────────────────────────────────────────────

_cmd_timestamps: dict = defaultdict(list)   # IP -> [timestamp, ...]
_cmd_lock             = threading.Lock()


def _is_command_flood(ip: str) -> bool:
    """Returns True if the IP has exceeded MAX_CMDS_PER_MINUTE."""
    now = time.time()
    with _cmd_lock:
        ts = _cmd_timestamps[ip]
        # Drop timestamps older than 60 s
        _cmd_timestamps[ip] = [t for t in ts if now - t < 60]
        _cmd_timestamps[ip].append(now)
        return len(_cmd_timestamps[ip]) > MAX_CMDS_PER_MINUTE


# ─────────────────────────────────────────────
#  Input sanitiser
# ─────────────────────────────────────────────

def _sanitise(value: str) -> str:
    """Strip null bytes and ASCII control characters from a field."""
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value)


_recovery_codes = {}  # target_email -> (code_string, expiry_timestamp)

def send_recovery_email(target_email: str, username: str, code: str):
    if "YOUR_" in GMAIL_SENDER:
        print(f"[SMTP] Disabled. Code for {username} is: {code}")
        return
    try:
        msg = EmailMessage()
        hebrew_content = f"היי {username},\n\nהתקבלה בקשה לשחזור חשבון ה-Quizy שלך.\n\nשם המשתמש שלך הוא: {username}\nקוד שחזור הסיסמה שלך (6 ספרות) הוא: {code}\n\nאם לא ביקשת זאת, אנא התעלם ממייל זה."
        msg.set_content(hebrew_content)
        msg['Subject'] = 'שחזור חשבון - Quizy'
        msg['From'] = GMAIL_SENDER
        msg['To'] = target_email

        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(GMAIL_SENDER, GMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"[SMTP] Recovery email sent to {target_email} (User: {username})")
    except Exception as e:
        print(f"[SMTP] Failed to send email to {target_email}: {e}")


# ─────────────────────────────────────────────
#  Client session
# ─────────────────────────────────────────────

class ClientSession:
    def __init__(self, sock: socket.socket, addr, db_conn: sqlite3.Connection,
                 server_private_key):
        self.sock = sock
        self.addr = addr
        self.ip = addr[0]           # used for rate-limiting
        self.db = db_conn
        self.server_priv = server_private_key
        self.aes_key: bytes = None
        self.user_id: int = None
        self.username: str = None
        self.role: str = None
        self.user_obj: BaseUser = None  # Teacher or Student instance

    # ── Encrypted send/recv ──────────────────

    def send(self, message: str):
        data = message.encode()
        if self.aes_key:
            data = aes_encrypt(self.aes_key, data)
        send_frame(self.sock, data)

    def recv(self) -> str:
        data = recv_frame(self.sock)
        if not data:
            return ""
        if self.aes_key:
            data = aes_decrypt(self.aes_key, data)
        return data.decode()

    # ── RSA handshake ────────────────────────

    def do_handshake(self) -> bool:
        """
        1. Server sends its RSA public key (PEM).
        2. Client sends an AES-256 session key encrypted with server pubkey.
        """
        pub_pem = self.server_priv.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo
        )
        send_frame(self.sock, pub_pem)

        encrypted_session_key = recv_frame(self.sock)
        if not encrypted_session_key:
            return False
        self.aes_key = rsa_decrypt(self.server_priv, encrypted_session_key)
        print(f"[Server] Handshake complete with {self.addr}")
        return True

    # ── Dispatch ─────────────────────────────

    def handle(self):
        try:
            # ── Handshake with strict timeout ────────────────────────────
            self.sock.settimeout(HANDSHAKE_TIMEOUT)
            if not self.do_handshake():
                return

            # ── Switch to idle timeout for the session ───────────────────
            self.sock.settimeout(IDLE_TIMEOUT)

            while True:
                msg = self.recv()
                if not msg:
                    break

                # ── Command flood check ──────────────────────────────────
                if _is_command_flood(self.ip):
                    self.send("ERROR|Rate limit exceeded. Slow down.")
                    print(f"[Security] Command flood from {self.ip} — disconnecting")
                    break

                # ── Sanitise every incoming field ────────────────────────
                parts = [_sanitise(p) for p in msg.split("|")]
                cmd   = parts[0]
                response = self.dispatch(cmd, parts[1:])
                if response:
                    self.send(response)

        except socket.timeout:
            print(f"[Security] {self.addr} timed out — disconnecting")
        except (ConnectionResetError, OSError):
            pass
        finally:
            print(f"[Server] Client {self.addr} disconnected")
            _conn_release(self.ip)
            self.sock.close()

    def dispatch(self, cmd: str, args: list) -> str:
        handlers = {
            "REGISTER":            self.cmd_register,
            "LOGIN":               self.cmd_login,
            "RECOVER_ACCOUNT":     self.cmd_recover_account,
            "RESET_PASSWORD":      self.cmd_reset_password,
            "CREATE_TEST":         self.cmd_create_test,
            "ADD_QUESTION":        self.cmd_add_question,
            "DELETE_TEST":         self.cmd_delete_test,
            "EDIT_TEST_TITLE":     self.cmd_edit_test_title,
            "SET_TIME_LIMIT":      self.cmd_set_time_limit,
            "DELETE_QUESTION":     self.cmd_delete_question,
            "LIST_TESTS":          self.cmd_list_tests,
            "GET_TEST":            self.cmd_get_test,
            "SUBMIT_TEST":         self.cmd_submit_test,
            "GET_RESULTS":         self.cmd_get_results,
            "TEACHER_RESULTS":     self.cmd_teacher_results,
            # ── Teacher collaboration ────────────────────────────────────
            "LIST_TEACHERS":       self.cmd_list_teachers,
            "SHARE_TEST":          self.cmd_share_test,
            # ── Admin commands ──────────────────────────────────────────
            "CREATE_CLASS":        self.cmd_create_class,
            "LIST_CLASSES":        self.cmd_list_classes,
            "ASSIGN_USER":         self.cmd_assign_user,
            "REMOVE_USER":         self.cmd_remove_user,
            "LIST_CLASS_MEMBERS":  self.cmd_list_class_members,
            "LIST_USERS":          self.cmd_list_users,
            "MY_CLASS":            self.cmd_my_class,
            "LIST_STUDENTS":       self.cmd_list_students,
            "GET_USER_DETAILS":    self.cmd_get_user_details,
            "CHECK_CLASS":         self.cmd_check_class,
            # ── Logging commands ───────────────────────────────────────────
            "GET_ACTIVITY_LOGS":   self.cmd_get_activity_logs,
        }
        fn = handlers.get(cmd)
        if fn:
            try:
                return fn(args)
            except Exception as e:
                return f"ERROR|{e}"
        return "ERROR|Unknown command"

    # ── Auth commands ─────────────────────────

    def cmd_register(self, args):
        # REGISTER|username|email|password|role
        if len(args) < 4:
            return "ERROR|Missing fields"
        username, email, password, role = args[0], args[1], args[2], args[3]
        if role not in ("teacher", "student"):
            return "ERROR|Invalid role"
        pw_error = validate_password(password)
        if pw_error:
            return f"ERROR|{pw_error}"
        pw_hash, pw_salt = hash_password(password)
        try:
            cur = self.db.cursor()
            cur.execute("INSERT INTO users (username, email, password, salt, role) VALUES (?,?,?,?,?)",
                        (username, email, pw_hash, pw_salt, role))
            self.db.commit()
            # Log successful registration
            user_id = cur.lastrowid
            log_activity(self.db, user_id, username, "REGISTER", f"Role: {role}, Email: {email}", self.ip)
            return "OK|Registered successfully"
        except sqlite3.IntegrityError as e:
            if "email" in str(e).lower():
                return "ERROR|Email already exists"
            return "ERROR|Username already exists"

    def cmd_login(self, args):
        # LOGIN|username|password
        # ── Rate-limit check ──────────────────────────────────────────────
        if is_locked_out(self.ip):
            return "ERROR|Too many failed attempts. Try again in 60 seconds."

        if len(args) < 2:
            return "ERROR|Missing credentials"
        username, password = args[0], args[1]
        cur = self.db.cursor()
        cur.execute("SELECT id, role, password, salt FROM users WHERE username=?", (username,))
        row = cur.fetchone()

        valid = False
        if row:
            db_pw, db_salt = row[2], row[3]
            calc_hash, _ = hash_password(password, bytes.fromhex(db_salt))
            if calc_hash == db_pw:
                valid = True

        if not valid:
            locked = record_login_failure(self.ip)
            if locked:
                print(f"[Security] IP {self.ip} locked out after {MAX_LOGIN_FAILURES} failures.")
                return "ERROR|Too many failed attempts. Locked out for 60 seconds."
            remaining = MAX_LOGIN_FAILURES - _login_attempts[self.ip][0]
            return f"ERROR|Invalid credentials ({remaining} attempts remaining)"

        # ── Success ───────────────────────────────────────────────────────
        reset_login_attempts(self.ip)
        self.user_id = row[0]
        self.role = row[1]
        self.username = username

        # Log successful login
        log_activity(self.db, self.user_id, self.username, "LOGIN", f"Role: {self.role}", self.ip)

        # Instantiate the appropriate OOP user object
        if self.role == "teacher":
            self.user_obj = Teacher(self.user_id, self.username)
        elif self.role == "admin":
            self.user_obj = Admin(self.user_id, self.username)
        else:
            self.user_obj = Student(self.user_id, self.username)

        # For students, include whether they are in a class (1 or 0)
        in_class = 0
        if self.role == "student":
            cur = self.db.cursor()
            cur.execute("SELECT 1 FROM class_members WHERE user_id=?", (self.user_id,))
            in_class = 1 if cur.fetchone() else 0

        print(f"[Auth] {self.user_obj} logged in from {self.ip}")
        return f"OK|{self.role}|{self.username}|{in_class}"

    def cmd_recover_account(self, args):
        # RECOVER_ACCOUNT|email
        if not args:
            return "ERROR|Missing email"
        email = args[0].strip()
        cur = self.db.cursor()
        cur.execute("SELECT username FROM users WHERE email=?", (email,))
        row = cur.fetchone()
        if not row:
            # Silently fake success to prevent email enum
            return "OK|If the email exists, a code was sent."

        username = row[0]
        code = str(random.randint(100000, 999999))
        _recovery_codes[email] = (code, time.time() + 600)  # 10 minute expiry
        # Log password recovery request
        log_activity(self.db, None, username, "PASSWORD_RECOVERY", f"Email: {email}", self.ip)
        threading.Thread(target=send_recovery_email, args=(email, username, code), daemon=True).start()
        return "OK|If the email exists, a code was sent."

    def cmd_reset_password(self, args):
        # RESET_PASSWORD|email|code|new_password
        if len(args) < 3:
            return "ERROR|Missing fields"
        email, code, new_pw = args[0].strip(), args[1].strip(), args[2].strip()

        # Verify code
        record = _recovery_codes.get(email)
        if not record:
            return "ERROR|Invalid or expired code"
        saved_code, expiry = record
        if time.time() > expiry or code != saved_code:
            return "ERROR|Invalid or expired code"

        pw_error = validate_password(new_pw)
        if pw_error:
            return f"ERROR|{pw_error}"

        pw_hash, pw_salt = hash_password(new_pw)
        cur = self.db.cursor()
        cur.execute("UPDATE users SET password=?, salt=? WHERE email=?", (pw_hash, pw_salt, email))
        self.db.commit()

        # Get username for logging
        cur.execute("SELECT username FROM users WHERE email=?", (email,))
        username_row = cur.fetchone()
        username = username_row[0] if username_row else "unknown"

        # Log password reset completion
        log_activity(self.db, None, username, "PASSWORD_RESET", f"Email: {email}", self.ip)

        # Invalidate code
        del _recovery_codes[email]
        return "OK|Password reset successfully."

    def _require_login(self):
        if not self.user_id:
            raise PermissionError("Not logged in")

    def _require_role(self, role):
        self._require_login()
        if self.role != role:
            raise PermissionError(f"Requires {role} role")

    # ── Teacher commands ──────────────────────

    def cmd_create_test(self, args):
        # CREATE_TEST|title[|time_limit_minutes]
        self._require_role("teacher")
        title = args[0]
        time_limit = 0
        if len(args) >= 2:
            try:
                time_limit = max(0, int(args[1]))
            except ValueError:
                time_limit = 0
        cur = self.db.cursor()
        cur.execute("INSERT INTO tests (title, teacher_id, time_limit) VALUES (?,?,?)",
                    (title, self.user_id, time_limit))
        self.db.commit()
        test_id = cur.lastrowid
        log_activity(self.db, self.user_id, self.username, "CREATE_TEST",
                     f"Test ID: {test_id}, Title: {title}, Time: {time_limit}m", self.ip)
        return f"OK|{test_id}"

    def cmd_set_time_limit(self, args):
        # SET_TIME_LIMIT|test_id|minutes
        self._require_role("teacher")
        if len(args) < 2:
            return "ERROR|Missing test_id or minutes"
        test_id = int(args[0])
        try:
            minutes = max(0, int(args[1]))
        except ValueError:
            return "ERROR|Invalid minutes value"
        cur = self.db.cursor()
        cur.execute("SELECT teacher_id FROM tests WHERE id=?", (test_id,))
        row = cur.fetchone()
        if not row or row[0] != self.user_id:
            return "ERROR|Not your test"
        cur.execute("UPDATE tests SET time_limit=? WHERE id=?", (minutes, test_id))
        self.db.commit()
        return "OK|Time limit updated"

    def cmd_add_question(self, args):
        # ADD_QUESTION|test_id|position|qtype|prompt|opt_a|opt_b|opt_c|opt_d|answer
        self._require_role("teacher")
        test_id, position, qtype, prompt = int(args[0]), int(args[1]), args[2], args[3]
        opt_a, opt_b, opt_c, opt_d = args[4], args[5], args[6], args[7]
        answer = args[8]
        cur = self.db.cursor()
        # Verify ownership
        cur.execute("SELECT teacher_id FROM tests WHERE id=?", (test_id,))
        row = cur.fetchone()
        if not row or row[0] != self.user_id:
            return "ERROR|Not your test"
        cur.execute("""
            INSERT INTO questions
              (test_id, position, qtype, prompt, option_a, option_b, option_c, option_d, answer)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (test_id, position, qtype, prompt, opt_a, opt_b, opt_c, opt_d, answer))
        self.db.commit()
        return f"OK|{cur.lastrowid}"

    def cmd_delete_test(self, args):
        # DELETE_TEST|test_id
        self._require_role("teacher")
        test_id = int(args[0])
        cur = self.db.cursor()
        cur.execute("SELECT teacher_id FROM tests WHERE id=?", (test_id,))
        row = cur.fetchone()
        if not row or row[0] != self.user_id:
            return "ERROR|Not your test"
        cur.execute("DELETE FROM tests WHERE id=?", (test_id,))
        self.db.commit()
        # Log test deletion
        log_activity(self.db, self.user_id, self.username, "DELETE_TEST", f"Test ID: {test_id}", self.ip)
        return "OK|Test deleted"

    def cmd_edit_test_title(self, args):
        # EDIT_TEST_TITLE|test_id|new_title
        self._require_role("teacher")
        test_id, new_title = int(args[0]), args[1]
        cur = self.db.cursor()
        cur.execute("SELECT teacher_id FROM tests WHERE id=?", (test_id,))
        row = cur.fetchone()
        if not row or row[0] != self.user_id:
            return "ERROR|Not your test"
        cur.execute("UPDATE tests SET title=? WHERE id=?", (new_title, test_id))
        self.db.commit()
        return "OK|Title updated"

    def cmd_delete_question(self, args):
        # DELETE_QUESTION|question_id
        self._require_role("teacher")
        q_id = int(args[0])
        cur = self.db.cursor()
        # Verify the question belongs to one of this teacher's tests
        cur.execute("""
            SELECT t.teacher_id FROM questions q
            JOIN tests t ON t.id = q.test_id
            WHERE q.id=?
        """, (q_id,))
        row = cur.fetchone()
        if not row or row[0] != self.user_id:
            return "ERROR|Not your question"
        cur.execute("DELETE FROM questions WHERE id=?", (q_id,))
        self.db.commit()
        return "OK|Question deleted"

    def cmd_teacher_results(self, args):
        # TEACHER_RESULTS|test_id
        self._require_role("teacher")
        test_id = int(args[0])
        cur = self.db.cursor()
        cur.execute("""
            SELECT u.username, s.score, s.submitted_at
            FROM submissions s
            JOIN users u ON u.id = s.student_id
            WHERE s.test_id=?
            ORDER BY s.submitted_at DESC
        """, (test_id,))
        rows = cur.fetchall()
        if not rows:
            return "RESULTS|"
        parts = ["RESULTS"]
        for r in rows:
            parts.append(f"{r[0]}~{r[1]:.1f}~{r[2]}")
        return "|".join(parts)

    # ── Shared / student commands ─────────────

    def cmd_list_tests(self, args):
        # LIST_TESTS  (students see only their class teacher's tests; teachers see own)
        self._require_login()
        cur = self.db.cursor()
        if self.role == "teacher":
            cur.execute("""
                SELECT t.id, t.title,
                       COUNT(q.id) as qcount,
                       t.created_at,
                       COALESCE(t.time_limit, 0)
                FROM tests t
                LEFT JOIN questions q ON q.test_id = t.id
                WHERE t.teacher_id=?
                GROUP BY t.id
                ORDER BY t.created_at DESC
            """, (self.user_id,))
        elif self.role == "student":
            # Only show tests from teachers in the same class as this student
            cur.execute("""
                SELECT t.id, t.title,
                       COUNT(q.id) as qcount,
                       t.created_at,
                       COALESCE(t.time_limit, 0)
                FROM tests t
                LEFT JOIN questions q ON q.test_id = t.id
                WHERE t.teacher_id IN (
                    SELECT cm_teacher.user_id
                    FROM class_members cm_student
                    JOIN class_members cm_teacher ON cm_teacher.class_id = cm_student.class_id
                    JOIN users u ON u.id = cm_teacher.user_id AND u.role = 'teacher'
                    WHERE cm_student.user_id = ?
                )
                GROUP BY t.id
                ORDER BY t.created_at DESC
            """, (self.user_id,))
        else:
            return "TESTS|"
        rows = cur.fetchall()
        if not rows:
            return "TESTS|"
        parts = ["TESTS"]
        for r in rows:
            parts.append(f"{r[0]}~{r[1]}~{r[2]}~{r[3]}~{r[4]}")
        return "|".join(parts)

    def cmd_get_test(self, args):
        # GET_TEST|test_id
        self._require_login()
        test_id = int(args[0])
        cur = self.db.cursor()
        cur.execute("SELECT title, COALESCE(time_limit, 0) FROM tests WHERE id=?", (test_id,))
        t = cur.fetchone()
        if not t:
            return "ERROR|Test not found"
        cur.execute("""
            SELECT id, position, qtype, prompt,
                   option_a, option_b, option_c, option_d, answer
            FROM questions WHERE test_id=? ORDER BY position
        """, (test_id,))
        questions = cur.fetchall()
        # Format: TEST_DATA|title|time_limit|q1_id~pos~qtype~prompt~a~b~c~d~ans|...
        parts = ["TEST_DATA", t[0], str(t[1])]
        for q in questions:
            # Students don't get the answer — send blank
            ans = q[8] if self.role == "teacher" else ""
            parts.append(f"{q[0]}~{q[1]}~{q[2]}~{q[3]}~{q[4]}~{q[5]}~{q[6]}~{q[7]}~{ans}")
        return "|".join(parts)

    def cmd_submit_test(self, args):
        # SUBMIT_TEST|test_id|q_id:answer|q_id:answer|...
        self._require_role("student")
        test_id = int(args[0])
        answer_pairs = args[1:]  # each is "qid:student_answer"

        cur = self.db.cursor()
        # Block retakes: one submission per (test, student)
        cur.execute("SELECT 1 FROM submissions WHERE test_id=? AND student_id=?",
                    (test_id, self.user_id))
        if cur.fetchone():
            return "ERROR|You have already submitted this test"
        # Fetch correct answers
        cur.execute("SELECT id, answer FROM questions WHERE test_id=?", (test_id,))
        correct = {row[0]: row[1].strip().lower() for row in cur.fetchall()}

        if not correct:
            return "ERROR|No questions in test"

        score_total = 0
        answers_to_insert = []
        for pair in answer_pairs:
            if ":" not in pair:
                continue
            qid_str, student_ans = pair.split(":", 1)
            qid = int(qid_str)
            student_ans_clean = student_ans.strip().lower()
            if correct.get(qid, "") == student_ans_clean:
                score_total += 1
            answers_to_insert.append((qid, student_ans))

        score_pct = (score_total / len(correct)) * 100 if correct else 0

        cur.execute("""
            INSERT INTO submissions (test_id, student_id, score)
            VALUES (?,?,?)
        """, (test_id, self.user_id, score_pct))
        sub_id = cur.lastrowid

        for qid, ans in answers_to_insert:
            cur.execute("""
                INSERT INTO answers (submission_id, question_id, student_ans)
                VALUES (?,?,?)
            """, (sub_id, qid, ans))

        self.db.commit()
        # Log test submission
        log_activity(self.db, self.user_id, self.username, "SUBMIT_TEST",
                    f"Test ID: {test_id}, Score: {score_pct:.1f}%, Correct: {score_total}/{len(correct)}", self.ip)
        return f"OK|{score_pct:.1f}|{score_total}|{len(correct)}"

    def cmd_get_results(self, args):
        # GET_RESULTS  - student sees their own past submissions
        self._require_role("student")
        cur = self.db.cursor()
        cur.execute("""
            SELECT t.title, s.score, s.submitted_at
            FROM submissions s
            JOIN tests t ON t.id = s.test_id
            WHERE s.student_id=?
            ORDER BY s.submitted_at DESC
        """, (self.user_id,))
        rows = cur.fetchall()
        if not rows:
            return "RESULTS|"
        parts = ["RESULTS"]
        for r in rows:
            parts.append(f"{r[0]}~{r[1]:.1f}~{r[2]}")
        return "|".join(parts)


    # ── Teacher collaboration ─────────────────────────────────────────────────

    def cmd_list_teachers(self, args):
        # LIST_TEACHERS  — returns all other teachers (so sender can pick a recipient)
        self._require_role("teacher")
        cur = self.db.cursor()
        cur.execute(
            "SELECT id, username FROM users WHERE role='teacher' AND id != ? ORDER BY username",
            (self.user_id,)
        )
        rows = cur.fetchall()
        if not rows:
            return "TEACHERS|"
        parts = ["TEACHERS"] + [f"{r[0]}~{r[1]}" for r in rows]
        return "|".join(parts)

    def cmd_share_test(self, args):
        # SHARE_TEST|test_id|target_teacher_id
        self._require_role("teacher")
        if len(args) < 2:
            return "ERROR|Missing test_id or target_teacher_id"
        test_id         = int(args[0])
        target_id       = int(args[1])
        cur = self.db.cursor()

        # Verify ownership
        cur.execute("SELECT title FROM tests WHERE id=? AND teacher_id=?",
                    (test_id, self.user_id))
        test_row = cur.fetchone()
        if not test_row:
            return "ERROR|Test not found or not yours"

        # Verify target is a teacher
        cur.execute("SELECT username FROM users WHERE id=? AND role='teacher'",
                    (target_id,))
        target_row = cur.fetchone()
        if not target_row:
            return "ERROR|Target user is not a teacher"

        new_title = f"{test_row[0]} [Shared by {self.username}]"

        # Copy the test
        cur.execute(
            "INSERT INTO tests (title, teacher_id) VALUES (?,?)",
            (new_title, target_id)
        )
        new_test_id = cur.lastrowid

        # Copy all questions
        cur.execute("""
            SELECT position, qtype, prompt, option_a, option_b, option_c, option_d, answer
            FROM questions WHERE test_id=? ORDER BY position
        """, (test_id,))
        for q in cur.fetchall():
            cur.execute("""
                INSERT INTO questions
                  (test_id, position, qtype, prompt,
                   option_a, option_b, option_c, option_d, answer)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (new_test_id, *q))

        self.db.commit()
        # Log test sharing
        log_activity(self.db, self.user_id, self.username, "SHARE_TEST",
                    f"Test '{test_row[0]}' shared with {target_row[0]} (New ID: {new_test_id})", self.ip)
        print(f"[Share] {self.username} → {target_row[0]}: test '{test_row[0]}'")
        return f"OK|Sent to {target_row[0]}"


    # ── Admin commands ────────────────────────────────────────────────────────

    def _require_admin(self):
        self._require_login()
        if self.role != "admin":
            raise PermissionError("Requires admin role")

    def cmd_create_class(self, args):
        # CREATE_CLASS|name
        self._require_admin()
        if not args or not args[0].strip():
            return "ERROR|Class name required"
        name = args[0].strip()
        try:
            cur = self.db.cursor()
            cur.execute("INSERT INTO classes (name) VALUES (?)", (name,))
            self.db.commit()
            class_id = cur.lastrowid
            # Log class creation
            log_activity(self.db, self.user_id, self.username, "CREATE_CLASS", f"Class ID: {class_id}, Name: {name}", self.ip)
            return f"OK|{class_id}"
        except sqlite3.IntegrityError:
            return "ERROR|Class name already exists"

    def cmd_list_classes(self, args):
        # LIST_CLASSES
        self._require_admin()
        cur = self.db.cursor()
        cur.execute("SELECT id, name FROM classes ORDER BY name")
        rows = cur.fetchall()
        if not rows:
            return "CLASSES|"
        parts = ["CLASSES"] + [f"{r[0]}~{r[1]}" for r in rows]
        return "|".join(parts)

    def _require_admin_or_own_class(self, class_id: int):
        """Allow admins freely; allow teachers only for their own class."""
        self._require_login()
        if self.role == "admin":
            return
        if self.role == "teacher":
            cur = self.db.cursor()
            cur.execute("SELECT 1 FROM class_members WHERE class_id=? AND user_id=?",
                        (class_id, self.user_id))
            if cur.fetchone():
                return
            raise PermissionError("Teachers can only manage their own class")
        raise PermissionError("Requires admin or teacher role")

    def cmd_assign_user(self, args):
        # ASSIGN_USER|user_id|class_id
        if len(args) < 2:
            return "ERROR|Missing user_id or class_id"
        user_id, class_id = int(args[0]), int(args[1])
        self._require_admin_or_own_class(class_id)
        cur = self.db.cursor()
        # Teachers can only assign students, not other teachers or admins
        if self.role == "teacher":
            cur.execute("SELECT role FROM users WHERE id=?", (user_id,))
            row = cur.fetchone()
            if not row:
                return "ERROR|User not found"
            if row[0] != "student":
                return "ERROR|Teachers can only assign students to their class"
        # Remove from any previous class first (one class per user)
        cur = self.db.cursor()
        cur.execute("DELETE FROM class_members WHERE user_id=?", (user_id,))
        cur.execute("INSERT OR IGNORE INTO class_members (class_id, user_id) VALUES (?,?)",
                    (class_id, user_id))
        self.db.commit()
        # Log user assignment
        cur.execute("SELECT username FROM users WHERE id=?", (user_id,))
        user_row = cur.fetchone()
        cur.execute("SELECT name FROM classes WHERE id=?", (class_id,))
        class_row = cur.fetchone()
        username = user_row[0] if user_row else "unknown"
        class_name = class_row[0] if class_row else "unknown"
        log_activity(self.db, self.user_id, self.username, "ASSIGN_USER",
                    f"User: {username} (ID: {user_id}) assigned to Class: {class_name} (ID: {class_id})", self.ip)
        return "OK|User assigned to class"

    def cmd_remove_user(self, args):
        # REMOVE_USER|user_id|class_id
        if len(args) < 2:
            return "ERROR|Missing user_id or class_id"
        user_id, class_id = int(args[0]), int(args[1])
        self._require_admin_or_own_class(class_id)
        cur = self.db.cursor()
        # Get user and class info for logging before deletion
        cur.execute("SELECT username FROM users WHERE id=?", (user_id,))
        user_row = cur.fetchone()
        cur.execute("SELECT name FROM classes WHERE id=?", (class_id,))
        class_row = cur.fetchone()
        username = user_row[0] if user_row else "unknown"
        class_name = class_row[0] if class_row else "unknown"

        cur.execute("DELETE FROM class_members WHERE user_id=? AND class_id=?",
                    (user_id, class_id))
        self.db.commit()
        # Log user removal
        log_activity(self.db, self.user_id, self.username, "REMOVE_USER",
                    f"User: {username} (ID: {user_id}) removed from Class: {class_name} (ID: {class_id})", self.ip)
        return "OK|User removed from class"

    def cmd_list_class_members(self, args):
        # LIST_CLASS_MEMBERS|class_id
        if not args:
            return "ERROR|Missing class_id"
        class_id = int(args[0])
        self._require_admin_or_own_class(class_id)
        cur = self.db.cursor()
        cur.execute("""
            SELECT u.id, u.username, u.role
            FROM class_members cm
            JOIN users u ON u.id = cm.user_id
            WHERE cm.class_id=?
            ORDER BY u.role, u.username
        """, (class_id,))
        rows = cur.fetchall()
        if not rows:
            return "MEMBERS|"
        parts = ["MEMBERS"] + [f"{r[0]}~{r[1]}~{r[2]}" for r in rows]
        return "|".join(parts)

    def cmd_list_users(self, args):
        # LIST_USERS
        self._require_admin()
        cur = self.db.cursor()
        cur.execute("""
            SELECT u.id, u.username, u.role,
                   COALESCE(c.name, '') as class_name
            FROM users u
            LEFT JOIN class_members cm ON cm.user_id = u.id
            LEFT JOIN classes c ON c.id = cm.class_id
            WHERE u.role != 'admin'
            ORDER BY u.role, u.username
        """)
        rows = cur.fetchall()
        if not rows:
            return "USERS|"
        parts = ["USERS"] + [f"{r[0]}~{r[1]}~{r[2]}~{r[3]}" for r in rows]
        return "|".join(parts)

    def cmd_my_class(self, args):
        # MY_CLASS  → returns the teacher's class id and name, or empty if none
        self._require_role("teacher")
        cur = self.db.cursor()
        cur.execute("""
            SELECT c.id, c.name
            FROM class_members cm
            JOIN classes c ON c.id = cm.class_id
            WHERE cm.user_id=?
            LIMIT 1
        """, (self.user_id,))
        row = cur.fetchone()
        if not row:
            return "MY_CLASS|"
        return f"MY_CLASS|{row[0]}~{row[1]}"

    def cmd_list_students(self, args):
        # LIST_STUDENTS  → for teachers: all student users with their current class
        self._require_role("teacher")
        cur = self.db.cursor()
        cur.execute("""
            SELECT u.id, u.username, COALESCE(c.name, '') as class_name
            FROM users u
            LEFT JOIN class_members cm ON cm.user_id = u.id
            LEFT JOIN classes c ON c.id = cm.class_id
            WHERE u.role = 'student'
            ORDER BY u.username
        """)
        rows = cur.fetchall()
        if not rows:
            return "STUDENTS|"
        parts = ["STUDENTS"] + [f"{r[0]}~{r[1]}~{r[2]}" for r in rows]
        return "|".join(parts)

    def cmd_get_user_details(self, args):
        # GET_USER_DETAILS  — admin sees full user info including email and password hash
        self._require_admin()
        cur = self.db.cursor()
        cur.execute("""
            SELECT u.id, u.username, u.email, u.role,
                   COALESCE(c.name, '') as class_name
            FROM users u
            LEFT JOIN class_members cm ON cm.user_id = u.id
            LEFT JOIN classes c ON c.id = cm.class_id
            ORDER BY u.role, u.username
        """)
        rows = cur.fetchall()
        if not rows:
            return "USER_DETAILS|"
        parts = ["USER_DETAILS"] + [f"{r[0]}~{r[1]}~{r[2]}~{r[3]}~{r[4]}" for r in rows]
        return "|".join(parts)

    def cmd_get_activity_logs(self, args):
        # GET_ACTIVITY_LOGS|limit|action_filter|user_filter
        self._require_login()
        # Only teachers and admins can view logs
        if self.role not in ("teacher", "admin"):
            return "ERROR|Permission denied"

        limit = 100  # default
        action_filter = ""
        user_filter = ""

        if args:
            limit = int(args[0]) if args[0].isdigit() else 100
            action_filter = args[1] if len(args) > 1 else ""
            user_filter = args[2] if len(args) > 2 else ""

        cur = self.db.cursor()
        query = """
            SELECT al.username, al.action, al.details, al.ip_address, al.timestamp
            FROM activity_logs al
            WHERE 1=1
        """
        params = []

        if action_filter:
            query += " AND al.action LIKE ?"
            params.append(f"%{action_filter}%")

        if user_filter:
            query += " AND al.username LIKE ?"
            params.append(f"%{user_filter}%")

        query += " ORDER BY al.timestamp DESC LIMIT ?"
        params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()

        if not rows:
            return "ACTIVITY_LOGS|"

        parts = ["ACTIVITY_LOGS"]
        for row in rows:
            # Format: username~action~details~ip_address~timestamp
            parts.append(f"{row[0]}~{row[1]}~{row[2]}~{row[3]}~{row[4]}")

        return "|".join(parts)

    def cmd_check_class(self, args):
        # CHECK_CLASS  — student polls to see if they've been placed in a class yet
        self._require_login()
        if self.role != "student":
            return "ERROR|Students only"
        cur = self.db.cursor()
        cur.execute("SELECT 1 FROM class_members WHERE user_id=?", (self.user_id,))
        in_class = 1 if cur.fetchone() else 0
        return f"CLASS_STATUS|{in_class}"


# ─────────────────────────────────────────────
#  Server main loop  (select-based)
# ─────────────────────────────────────────────

def run_server():
    db = init_db(DB_PATH)
    server_priv, _ = generate_rsa_keypair()
    print("[Server] RSA key pair generated")

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(10)
    server_sock.setblocking(False)
    print(f"[Server] Listening on {HOST}:{PORT}")

    inputs = [server_sock]

    while True:
        readable, _, _ = select.select(inputs, [], [], 1.0)
        for s in readable:
            if s is server_sock:
                client_sock, addr = server_sock.accept()
                ip = addr[0]

                # ── Connection-limit gate ────────────────────────────────
                if not _conn_acquire(ip):
                    print(f"[Security] Rejected {addr}: connection limit exceeded")
                    try:
                        client_sock.close()
                    except OSError:
                        pass
                    continue

                client_sock.setblocking(True)
                print(f"[Server] New connection from {addr} "
                      f"(IP total: {_conns_per_ip[ip]}, global: {_total_conns[0]})")
                session = ClientSession(client_sock, addr, db, server_priv)
                t = threading.Thread(target=session.handle, daemon=True)
                t.start()


if __name__ == "__main__":
    run_server()

