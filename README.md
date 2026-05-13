# Quizy — Secure Online Exam System

A client/server exam platform with strong cryptography, role-based access control, and proctoring features. Built in Python with custom socket networking, SQLite persistence, and CustomTkinter GUIs.

---

## What it does

Three distinct GUI applications talk to one server:

| Client | Who uses it | What they can do |
|--------|-------------|------------------|
| **Admin** | School administrator | Create classes, assign teachers, view all users and activity logs |
| **Teacher** | Instructors | Create timed tests, edit/delete tests, share tests with colleagues, view student results, manage their class roster |
| **Student** | Test takers | Browse available tests, take tests in kiosk mode under a countdown timer, view personal results |

---

## Security features

Quizy is a cybersecurity project — security is the point, not a side concern.

### Network layer
- **Hybrid RSA + AES encryption.** On connect, the server sends a 2048-bit RSA public key. The client generates a fresh AES-256 session key, encrypts it with the server's public key, and sends it back. From that point on, every message is encrypted with **AES-256-GCM** (authenticated encryption — tampering is detected).
- **Length-prefixed framing** prevents message-boundary attacks.
- **Per-IP connection limit** (3 simultaneous connections) blocks basic DoS.

### Authentication
- **Salted password hashes** (PBKDF2-SHA256). Plaintext passwords are never stored or logged.
- **Login rate limiting**: 5 failed attempts → 60-second cooldown per IP.
- **Account recovery** via emailed reset code (uses Gmail SMTP with an app password).

### Authorization
- **Role-based access control.** Each command on the server is gated by role (`_require_role("teacher")`, `_require_admin()`, etc.).
- **Resource-level checks.** Teachers can only edit/delete *their own* tests and manage *their own* class — verified by SQL ownership checks before every mutation.

### Test integrity
- **Kiosk mode**: when a student starts a test, the window is forced fullscreen and other applications are blocked (platform-aware: Windows uses keyboard hooks, macOS/Linux use `wm_attributes`).
- **Webcam snapshots**: a photo is captured at login as a deterrent against impersonation. Stored locally as `<username>_snapshot.jpg`.
- **Retake prevention**: the server rejects any second submission of the same test by the same student.
- **Test timer**: teachers set a per-test time limit; students see a live countdown and the test auto-submits when time expires. Enforced both client-side (countdown) and server-side (the answer key is fetched only once).
- **Answer key withholding**: students fetching a test get the questions with blanked-out answers; only teachers receive correct answers (for editing).

### Auditing
- **Activity log table** records every meaningful action (login, register, create test, submit, assign user, etc.) with username, IP, action, details, and timestamp. Teachers and admins can view filtered logs in-app.

---

## Architecture

```
┌────────────┐   ┌────────────┐   ┌────────────┐
│  Admin GUI │   │ Teacher GUI│   │ Student GUI│
└──────┬─────┘   └──────┬─────┘   └──────┬─────┘
       │                 │                 │
       │ RSA handshake → AES-256-GCM session
       └────────────────┬┴─────────────────┘
                        │
                  ┌─────▼─────┐
                  │  Server   │   sockets + select
                  │           │   length-prefixed protocol
                  │  RBAC     │   per-IP connection cap
                  └─────┬─────┘
                        │
                  ┌─────▼─────┐
                  │  SQLite   │   users, classes, tests,
                  │           │   questions, submissions,
                  │           │   answers, activity_logs
                  └───────────┘
```

### Wire protocol
Pipe-delimited commands, AES-encrypted in transit:

```
LOGIN|username|password
REGISTER|username|email|password|role
CREATE_TEST|title|time_limit_minutes
SUBMIT_TEST|test_id|qid:ans|qid:ans|...
LIST_TESTS
GET_TEST|test_id
ASSIGN_USER|user_id|class_id
...
```

Responses use the same format: `OK|...` or `ERROR|reason`.

### Database
SQLite with foreign keys and cascading deletes. Tables:

- `users` — credentials and role
- `classes`, `class_members` — class roster
- `tests`, `questions` — test content, with `time_limit` per test
- `submissions`, `answers` — graded submissions and per-question answers
- `activity_logs` — full audit trail

The server auto-migrates older databases on startup.

---

## Running it

### Prerequisites
- Python 3.10+
- `pip install cryptography customtkinter pillow opencv-python`

### Start the server
```bash
python3 server69.py
```
The server listens on `127.0.0.1:9999`. On first run, it creates `exam_system.db` and a default admin account:

```
Username: admin
Password: Admin@123
```

### Start any of the clients (in separate terminals)
```bash
python3 admin_client69.py
python3 teacher_client69.py
python3 student_client69.py
```

### First-time setup walkthrough
1. **Admin**: log in with the default credentials. Create a class (e.g., "10-A"). Register or wait for users to register.
2. **Admin**: assign a teacher to the class.
3. **Teacher**: log in. Open *My Class* and add students to the roster.
4. **Teacher**: create a test, set a time limit, add questions.
5. **Student**: log in. The test will appear in their list. Click *Start* — kiosk mode engages, the countdown starts.

---

## File layout

```
.
├── server69.py            # Server (sockets, crypto, RBAC, SQLite)
├── admin_client69.py      # Admin GUI
├── teacher_client69.py    # Teacher GUI (test editor, results, class manager, logs)
├── student_client69.py    # Student GUI (test taking with timer + kiosk)
├── network.py             # Shared client networking + crypto helpers
├── sounds.py              # UI sound effects
└── exam_system.db         # SQLite database (auto-created)
```

---

## Known limitations / future work

- Profile pictures are stored as local files keyed by username, not in the database — they don't follow the user across machines.
- Kiosk mode is client-side only; a determined attacker could bypass it by writing their own client.
- Short-answer grading is exact-match (case- and whitespace-insensitive), not fuzzy.
- No automated test suite yet.
- Deployment is local-loopback only.
