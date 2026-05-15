"""
teacher_client.py – Teacher GUI
================================
CustomTkinter-based interface for:
  • Register / Login
  • Create tests with a question editor (MC, True/False, Short Answer)
  • Delete tests
  • View per-test student results
"""

import customtkinter as ctk
from tkinter import messagebox
from network import NetworkClient
import os
from PIL import Image
import sounds

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

# ── Teacher brand colours ────────────────────────────────────────
_PRI   = "#2E7D32"   # deep green
_ACC   = "#66BB6A"   # light green
_DBG   = "#0A1F0A"   # dark background
_HOV   = "#1B5E20"   # hover
_DIM   = "#A5D6A7"   # dimmed text
_CARD  = "#0F1F0F"   # card bg


# ─────────────────────────────────────────────
#  OS-level hardware access: webcam snapshot
# ─────────────────────────────────────────────

def capture_webcam_snapshot(username: str) -> str:
    """
    Capture a frame from the system webcam (OS camera driver = hardware API).
    Returns the saved filepath, or empty string on failure.
    """
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[Camera] No webcam detected — skipping snapshot.")
            return ""
        ret, frame = cap.read()
        cap.release()
        if ret:
            filename = f"{username}_snapshot.jpg"
            cv2.imwrite(filename, frame)
            print(f"[Camera] Snapshot saved: {filename}")
            return filename
        return ""
    except ImportError:
        print("[Camera] opencv-python not installed. Run: pip install opencv-python")
        return ""
    except Exception as e:
        print(f"[Camera] Snapshot error: {e}")
        return ""


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def parse_tests(response: str):
    """TESTS|id~title~qcount~date~time_limit|..."""
    parts = response.split("|")
    if parts[0] != "TESTS" or len(parts) < 2 or parts[1] == "":
        return []
    tests = []
    for item in parts[1:]:
        fields = item.split("~")
        tests.append({
            "id":         int(fields[0]),
            "title":      fields[1],
            "qcount":     int(fields[2]),
            "date":       fields[3],
            "time_limit": int(fields[4]) if len(fields) > 4 and fields[4].isdigit() else 0,
        })
    return tests


def parse_results(response: str):
    """RESULTS|username~score~date|..."""
    parts = response.split("|")
    if parts[0] != "RESULTS" or len(parts) < 2 or parts[1] == "":
        return []
    results = []
    for item in parts[1:]:
        fields = item.split("~")
        results.append({
            "username": fields[0],
            "score":    float(fields[1]),
            "date":     fields[2],
        })
    return results


# ─────────────────────────────────────────────
#  Account Recovery
# ─────────────────────────────────────────────

class ForgotPasswordDialog(ctk.CTkToplevel):
    def __init__(self, parent, net):
        super().__init__(parent)
        self.title("Account Recovery")
        self.geometry("350x320")
        self.resizable(False, False)
        self.net = net
        self.email = ""
        self.grab_set()
        self.email_stage()

    def email_stage(self):
        for widget in self.winfo_children():
            widget.destroy()
        ctk.CTkLabel(self, text="Recovery", font=("Arial", 20, "bold")).pack(pady=(20, 10))
        self.email_entry = ctk.CTkEntry(self, width=250, placeholder_text="Enter your registered email")
        self.email_entry.pack(pady=10)
        ctk.CTkButton(self, text="Send Code", command=self.send_code).pack(pady=20)

    def send_code(self):
        email = self.email_entry.get().strip()
        if "@" not in email:
            messagebox.showerror("Error", "Enter a valid email")
            return
        self.email = email
        resp = self.net.request(f"RECOVER_ACCOUNT|{email}")
        messagebox.showinfo("Recovery", resp.split("|", 1)[1])
        self.code_stage()

    def code_stage(self):
        for widget in self.winfo_children():
            widget.destroy()
        ctk.CTkLabel(self, text="Reset Password", font=("Arial", 20, "bold")).pack(pady=(20, 10))
        self.code_entry = ctk.CTkEntry(self, width=250, placeholder_text="6-digit Code from Email")
        self.code_entry.pack(pady=5)
        self.new_pw_entry = ctk.CTkEntry(self, width=250, show="*", placeholder_text="New Password")
        self.new_pw_entry.pack(pady=5)
        ctk.CTkButton(self, text="Reset Password", command=self.reset_pw).pack(pady=20)

    def reset_pw(self):
        code = self.code_entry.get().strip()
        new_pw = self.new_pw_entry.get().strip()
        if not code or not new_pw:
            messagebox.showerror("Error", "Fill in all fields")
            return
        resp = self.net.request(f"RESET_PASSWORD|{self.email}|{code}|{new_pw}")
        parts = resp.split("|")
        if parts[0] == "OK":
            messagebox.showinfo("Success", "Password reset successfully!")
            self.destroy()
        else:
            messagebox.showerror("Failed", parts[1] if len(parts) > 1 else "Unknown error")

# ─────────────────────────────────────────────
#  Register window
# ─────────────────────────────────────────────

class RegisterWindow(ctk.CTkToplevel):
    def __init__(self, parent, net):
        super().__init__(parent)
        self.title("Quizy – Teacher Register")
        self.geometry("660x480")
        self.resizable(False, False)
        self.configure(fg_color=_DBG)
        self.net = net
        self.grab_set()
        self._build_ui()

    def _build_ui(self):
        brand = ctk.CTkFrame(self, width=210, fg_color=_PRI, corner_radius=0)
        brand.pack(side="left", fill="y")
        brand.pack_propagate(False)
        ctk.CTkLabel(brand, text="Q", font=ctk.CTkFont(size=64, weight="bold"),
                     text_color="white").pack(pady=(55, 0))
        ctk.CTkLabel(brand, text="Quizy", font=ctk.CTkFont(size=20, weight="bold"),
                     text_color="white").pack()
        ctk.CTkLabel(brand, text="Teacher Portal", font=ctk.CTkFont(size=12),
                     text_color=_DIM).pack(pady=(4, 0))

        form = ctk.CTkFrame(self, fg_color=_DBG, corner_radius=0)
        form.pack(side="left", fill="both", expand=True)
        inner = ctk.CTkFrame(form, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(inner, text="Create Account",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color="white").pack(pady=(0, 4))
        ctk.CTkLabel(inner, text="Join Quizy as a Teacher",
                     font=ctk.CTkFont(size=13), text_color=_DIM).pack(pady=(0, 18))

        for label, attr, ph, kw in [
            ("Username", "username_entry", "teacher1", {}),
            ("Email",    "email_entry",    "teacher@email.com", {}),
            ("Password", "password_entry", "Enter password", {"show": "•"}),
        ]:
            ctk.CTkLabel(inner, text=label, font=ctk.CTkFont(size=12),
                         text_color=_DIM).pack(anchor="w")
            e = ctk.CTkEntry(inner, width=290, height=40, placeholder_text=ph,
                             border_color=_PRI, border_width=2, **kw)
            e.pack(pady=(3, 10))
            setattr(self, attr, e)
        self.password_entry.bind("<Return>", lambda _: self.do_register())

        ctk.CTkButton(inner, text="Create Account", width=290, height=42,
                      fg_color=_PRI, hover_color=_HOV,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      command=self.do_register).pack(pady=(8, 8))
        ctk.CTkButton(inner, text="← Back to Login",
                      fg_color="transparent", text_color=_ACC,
                      hover_color=_DBG, height=24,
                      command=self.destroy).pack()

    def do_register(self):
        u = self.username_entry.get().strip()
        e = self.email_entry.get().strip()
        p = self.password_entry.get().strip()
        if not u or not e or not p:
            messagebox.showwarning("Input Error", "Please fill in all fields.")
            return
        if "@" not in e:
            messagebox.showwarning("Input Error", "Please enter a valid email.")
            return
        resp = self.net.request(f"REGISTER|{u}|{e}|{p}|teacher")
        parts = resp.split("|")
        if parts[0] == "OK":
            sounds.success()
            capture_webcam_snapshot(u)
            messagebox.showinfo("Success",
                                "Account created! A profile picture was taken via webcam.\nYou can now log in.")
            self.destroy()
        else:
            sounds.error()
            messagebox.showerror("Registration Failed",
                                 parts[1] if len(parts) > 1 else "Unknown error")


# ─────────────────────────────────────────────
#  Login window
# ─────────────────────────────────────────────

class LoginWindow(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Quizy – Teacher Login")
        self.geometry("660x420")
        self.resizable(False, False)
        self.configure(fg_color=_DBG)
        self.net = NetworkClient()

        try:
            self.net.connect()
        except Exception as e:
            messagebox.showerror("Connection Error",
                                 f"Cannot connect to server:\n{e}")
            self.destroy()
            return

        self._build_ui()

    def _build_ui(self):
        brand = ctk.CTkFrame(self, width=210, fg_color=_PRI, corner_radius=0)
        brand.pack(side="left", fill="y")
        brand.pack_propagate(False)
        ctk.CTkLabel(brand, text="Q", font=ctk.CTkFont(size=64, weight="bold"),
                     text_color="white").pack(pady=(55, 0))
        ctk.CTkLabel(brand, text="Quizy", font=ctk.CTkFont(size=20, weight="bold"),
                     text_color="white").pack()
        ctk.CTkLabel(brand, text="Teacher Portal", font=ctk.CTkFont(size=12),
                     text_color=_DIM).pack(pady=(4, 0))

        form = ctk.CTkFrame(self, fg_color=_DBG, corner_radius=0)
        form.pack(side="left", fill="both", expand=True)
        inner = ctk.CTkFrame(form, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(inner, text="Welcome back!",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color="white").pack(pady=(0, 4))
        ctk.CTkLabel(inner, text="Sign in to your account",
                     font=ctk.CTkFont(size=13), text_color=_DIM).pack(pady=(0, 22))

        ctk.CTkLabel(inner, text="Username", font=ctk.CTkFont(size=12),
                     text_color=_DIM).pack(anchor="w")
        self.username_entry = ctk.CTkEntry(inner, width=290, height=40,
                                           placeholder_text="teacher1",
                                           border_color=_PRI, border_width=2)
        self.username_entry.pack(pady=(3, 12))

        ctk.CTkLabel(inner, text="Password", font=ctk.CTkFont(size=12),
                     text_color=_DIM).pack(anchor="w")
        self.password_entry = ctk.CTkEntry(inner, width=290, height=40, show="•",
                                           placeholder_text="Enter password",
                                           border_color=_PRI, border_width=2)
        self.password_entry.pack(pady=(3, 20))
        self.password_entry.bind("<Return>", lambda _: self.do_login())

        ctk.CTkButton(inner, text="Sign In", width=290, height=42,
                      fg_color=_PRI, hover_color=_HOV,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      command=self.do_login).pack(pady=(0, 10))
        ctk.CTkButton(inner, text="Don't have an account? Register →",
                      fg_color="transparent", text_color=_ACC,
                      hover_color=_DBG, height=24,
                      command=lambda: RegisterWindow(self, self.net)).pack(pady=(0, 4))
        ctk.CTkButton(inner, text="Forgot Password / Username?",
                      fg_color="transparent", text_color="gray",
                      hover_color=_DBG, height=24,
                      command=lambda: ForgotPasswordDialog(self, self.net)).pack()

    def do_login(self):
        u = self.username_entry.get().strip()
        p = self.password_entry.get().strip()
        if not u or not p:
            messagebox.showwarning("Input Error", "Please fill in all fields.")
            return
        resp = self.net.request(f"LOGIN|{u}|{p}")
        parts = resp.split("|")
        if parts[0] == "OK":
            role = parts[1]  # OK|role|username|in_class (extra parts ignored safely)
            if role != "teacher":
                sounds.error()
                messagebox.showerror("Access Denied",
                                     "This portal is for teachers only.")
                return
            sounds.login()
            self.withdraw()
            app = TeacherApp(self.net, u)
            app.mainloop()
            self.destroy()
        else:
            sounds.error()
            messagebox.showerror("Login Failed", parts[1] if len(parts) > 1
                                 else "Unknown error")


# ─────────────────────────────────────────────
#  Main Teacher application
# ─────────────────────────────────────────────

class TeacherApp(ctk.CTkToplevel):
    def __init__(self, net: NetworkClient, username: str):
        super().__init__()
        self.net = net
        self.username = username
        self.title(f"Quizy – Teacher: {username}")
        self.geometry("900x620")
        self.minsize(800, 500)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self.refresh_tests()

    def _on_close(self):
        self.net.close()
        self.quit()   # exits the mainloop() called in LoginWindow.do_login
        self.destroy()

    def _build_ui(self):
        # ── Sidebar ────────────────────────
        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color="#071407")
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        logo_bar = ctk.CTkFrame(self.sidebar, height=65, fg_color=_PRI, corner_radius=0)
        logo_bar.pack(fill="x")
        logo_bar.pack_propagate(False)
        ctk.CTkLabel(logo_bar, text="Quizy",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color="white").pack(expand=True)

        self.pic_label = ctk.CTkLabel(self.sidebar, text="")
        self.pic_label.pack(pady=(18, 4))
        self.load_profile_picture()

        ctk.CTkLabel(self.sidebar, text=f"👤 {self.username}",
                     font=ctk.CTkFont(size=12), text_color=_DIM).pack(pady=(0, 4))
        ctk.CTkLabel(self.sidebar, text="Teacher",
                     font=ctk.CTkFont(size=11), text_color="gray").pack(pady=(0, 10))
        ctk.CTkButton(self.sidebar, text="📷  Update Picture",
                      fg_color="#0f2010", hover_color=_PRI, height=34,
                      command=self.update_picture).pack(padx=14, pady=(0, 12), fill="x")

        sep = ctk.CTkFrame(self.sidebar, height=1, fg_color="#1a401a")
        sep.pack(fill="x", padx=14, pady=(0, 10))

        for icon, label, color, cmd in [
            ("➕",  "New Test",      _PRI,      self.open_test_editor),
            ("✏️",  "Edit Test",     "#5D4037", self.edit_selected_test),
            ("🗑",  "Delete Test",   "#7f0000", self.delete_selected_test),
            ("📤",  "Share Test",    "#0D4D6B", self.share_selected_test),
            ("📊",  "View Results",  "#3a3a00", self.view_results),
            ("👥",  "My Class",      "#1f4d2c", self.open_class_manager),
            ("📋",  "Activity Logs", "#3d1f6e", self.view_activity_logs),
        ]:
            ctk.CTkButton(self.sidebar, text=f"{icon}  {label}",
                          fg_color=color, hover_color=_HOV,
                          anchor="w", height=36,
                          command=cmd).pack(padx=14, pady=3, fill="x")

        ctk.CTkButton(self.sidebar, text="🔄  Refresh",
                      fg_color="#1a2a1a", hover_color="#253025",
                      anchor="w", height=34,
                      command=self.refresh_tests).pack(padx=14, pady=(12, 3), fill="x")

        ctk.CTkButton(self.sidebar, text="🚪  Sign Out",
                      fg_color="#3a1010", hover_color="#5a1a1a",
                      anchor="w", height=34,
                      command=self._on_close).pack(padx=14, pady=(6, 14), fill="x")

        # ── Main area ────────────────────────
        main = ctk.CTkFrame(self, fg_color=_DBG)
        main.pack(side="left", fill="both", expand=True, padx=0)

        hdr = ctk.CTkFrame(main, height=50, fg_color=_PRI, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="📝  My Tests",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="white").pack(side="left", padx=20, expand=True)

        self.test_list = ctk.CTkScrollableFrame(main, fg_color=_DBG)
        self.test_list.pack(fill="both", expand=True, padx=16, pady=16)

        self.selected_test_id = None
        self.test_frames = []

    def load_profile_picture(self):
        filename = f"{self.username}_snapshot.jpg"
        if os.path.exists(filename):
            try:
                img = Image.open(filename)
                self.profile_img = ctk.CTkImage(light_image=img, dark_image=img, size=(100, 100))
                self.pic_label.configure(image=self.profile_img, text="")
            except Exception as e:
                print(f"Error loading picture: {e}")
                self.pic_label.configure(text="📷 No picture")
        else:
            self.pic_label.configure(text="📷 No picture")

    def update_picture(self):
        msg = messagebox.askyesno("Update Picture", "Take a new profile picture with your webcam?")
        if msg:
            res = capture_webcam_snapshot(self.username)
            if res:
                self.load_profile_picture()
                messagebox.showinfo("Updated", "Profile picture updated!")
            else:
                messagebox.showerror("Error", "Could not capture webcam.")

    def refresh_tests(self):
        resp = self.net.request("LIST_TESTS")
        self.tests = parse_tests(resp)
        self._render_test_list()

    def _render_test_list(self):
        for w in self.test_list.winfo_children():
            w.destroy()
        self.test_frames = []
        self.selected_test_id = None

        if not self.tests:
            ctk.CTkLabel(self.test_list,
                         text="No tests yet. Create one with ➕  New Test.",
                         text_color="gray").pack(pady=40)
            return

        for test in self.tests:
            self._make_test_card(test)

    def _make_test_card(self, test: dict):
        card = ctk.CTkFrame(self.test_list, fg_color=_CARD, corner_radius=10, cursor="hand2")
        card.pack(fill="x", pady=4, padx=2)

        accent = ctk.CTkFrame(card, width=5, fg_color=_ACC, corner_radius=0)
        accent.pack(side="left", fill="y")

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(side="left", fill="x", expand=True, padx=12, pady=12)

        title_lbl = ctk.CTkLabel(inner, text=f"📝  {test['title']}",
                                 font=ctk.CTkFont(size=14, weight="bold"),
                                 text_color="white", anchor="w")
        title_lbl.pack(anchor="w")

        info = ctk.CTkLabel(inner,
                            text=f"{test['qcount']} question(s)  •  Created: {test['date'][:10]}",
                            font=ctk.CTkFont(size=11), text_color=_DIM, anchor="w")
        info.pack(anchor="w", pady=(2, 0))

        def select(event, tid=test["id"], c=card):
            self._select_card(tid, c)

        for widget in (card, accent, inner, title_lbl, info):
            widget.bind("<Button-1>", select)

    def _select_card(self, test_id, card_widget):
        for w in self.test_list.winfo_children():
            try:
                w.configure(fg_color=_CARD)
            except Exception:
                pass
        card_widget.configure(fg_color="#1a3d1a")
        self.selected_test_id = test_id

    # ── Editor ──────────────────────────────────

    def open_test_editor(self):
        editor = TestEditorWindow(self, self.net)
        editor.grab_set()

    # ── Edit ────────────────────────────────────

    def edit_selected_test(self):
        if not self.selected_test_id:
            messagebox.showwarning("No Selection", "Please select a test first.")
            return

        resp = self.net.request(f"GET_TEST|{self.selected_test_id}")
        parts = resp.split("|")
        if parts[0] == "ERROR":
            messagebox.showerror("Error", parts[1])
            return

        # FORMAT: TEST_DATA|title|time_limit|q1_id~pos~qtype~prompt~a~b~c~d~ans|...
        title = parts[1]
        try:
            time_limit = int(parts[2])
        except (ValueError, IndexError):
            time_limit = 0
        questions = []
        for q_str in parts[3:]:
            if not q_str: continue
            q_parts = q_str.split("~")
            q = {
                "id": int(q_parts[0]),
                "pos": int(q_parts[1]),
                "qtype": q_parts[2],
                "prompt": q_parts[3],
                "answer": q_parts[8],
                "opt_a": q_parts[4],
                "opt_b": q_parts[5],
                "opt_c": q_parts[6],
                "opt_d": q_parts[7]
            }
            questions.append(q)

        editor = TestEditorWindow(self, self.net, test_id=self.selected_test_id,
                                  title=title, questions=questions, time_limit=time_limit)
        editor.grab_set()

    # ── Delete ──────────────────────────────────

    def delete_selected_test(self):
        if not self.selected_test_id:
            messagebox.showwarning("No Selection", "Please select a test first.")
            return
        confirm = messagebox.askyesno("Delete Test",
                                      "Delete this test and all its data?")
        if not confirm:
            return
        resp = self.net.request(f"DELETE_TEST|{self.selected_test_id}")
        parts = resp.split("|")
        if parts[0] == "OK":
            sounds.delete()
            messagebox.showinfo("Deleted", "Test deleted.")
            self.refresh_tests()
        else:
            sounds.error()
            messagebox.showerror("Error", parts[1])

    # ── Share ────────────────────────────────────────────────────────────────

    def share_selected_test(self):
        if not self.selected_test_id:
            messagebox.showwarning("No Selection", "Please select a test first.")
            return

        # Fetch other teachers from server
        resp = self.net.request("LIST_TEACHERS")
        parts = resp.split("|")
        if parts[0] != "TEACHERS" or len(parts) < 2 or parts[1] == "":
            messagebox.showinfo("No Other Teachers",
                                "There are no other teacher accounts to share with.")
            return

        teachers = []
        for item in parts[1:]:
            f = item.split("~")
            teachers.append({"id": int(f[0]), "username": f[1]})

        # Build a picker dialog
        win = ctk.CTkToplevel(self)
        win.title("Share Test")
        win.geometry("340x300")
        win.grab_set()

        ctk.CTkLabel(win, text="Send a copy to:",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(20, 12))

        scroll = ctk.CTkScrollableFrame(win, height=180)
        scroll.pack(fill="x", padx=20)

        def do_share(teacher_id, teacher_name):
            resp2 = self.net.request(
                f"SHARE_TEST|{self.selected_test_id}|{teacher_id}")
            p2 = resp2.split("|")
            if p2[0] == "OK":
                messagebox.showinfo("Shared", f"Test sent to {teacher_name}!")
                win.destroy()
            else:
                messagebox.showerror("Error", p2[1] if len(p2) > 1 else resp2)

        for t in teachers:
            ctk.CTkButton(
                scroll,
                text=f"👨‍🏫  {t['username']}",
                font=ctk.CTkFont(size=13),
                fg_color="#1a5c7a", hover_color="#0e3d52",
                command=lambda tid=t["id"], tname=t["username"]:
                    do_share(tid, tname)
            ).pack(fill="x", pady=4, padx=4)

        ctk.CTkButton(win, text="Cancel", fg_color="gray30", hover_color="gray20",
                      command=win.destroy).pack(pady=12)

    # ── Results ─────────────────────────────────

    def view_results(self):
        if not self.selected_test_id:
            messagebox.showwarning("No Selection", "Please select a test first.")
            return
        resp = self.net.request(f"TEACHER_RESULTS|{self.selected_test_id}")
        results = parse_results(resp)

        win = ctk.CTkToplevel(self)
        win.title("Student Results")
        win.geometry("500x400")
        win.grab_set()

        ctk.CTkLabel(win, text="Student Results",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=12)

        scroll = ctk.CTkScrollableFrame(win)
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        if not results:
            ctk.CTkLabel(scroll, text="No submissions yet.", text_color="gray").pack(pady=20)
        else:
            # Header
            hdr = ctk.CTkFrame(scroll, fg_color="gray25")
            hdr.pack(fill="x", pady=(0, 4))
            for col, w in [("Student", 180), ("Score", 80), ("Date", 180)]:
                ctk.CTkLabel(hdr, text=col, width=w,
                             font=ctk.CTkFont(weight="bold")).pack(side="left", padx=4, pady=6)

            for r in results:
                row = ctk.CTkFrame(scroll)
                row.pack(fill="x", pady=2)
                colour = "#2a5e1e" if r["score"] >= 50 else "#5e1e1e"
                ctk.CTkLabel(row, text=r["username"], width=180, anchor="w").pack(side="left", padx=4, pady=6)
                ctk.CTkLabel(row, text=f"{r['score']:.1f}%", width=80,
                             text_color=colour).pack(side="left", padx=4, pady=6)
                ctk.CTkLabel(row, text=r["date"][:16], width=180, anchor="w").pack(side="left", padx=4, pady=6)

    def view_activity_logs(self):
        """Open activity log viewer window"""
        log_viewer = ActivityLogViewer(self, self.net)
        log_viewer.grab_set()

    def open_class_manager(self):
        """Open the teacher's class roster manager."""
        mgr = ClassManagerWindow(self, self.net)
        mgr.grab_set()


# ─────────────────────────────────────────────
#  Class Manager window  (teacher's own class roster)
# ─────────────────────────────────────────────

class ClassManagerWindow(ctk.CTkToplevel):
    def __init__(self, parent: TeacherApp, net: NetworkClient):
        super().__init__(parent)
        self.net = net
        self.title("My Class")
        self.geometry("720x560")
        self.class_id = None
        self.class_name = None
        self._build_ui()
        self._load()

    def _build_ui(self):
        self.header_lbl = ctk.CTkLabel(self, text="Loading…",
                                       font=ctk.CTkFont(size=16, weight="bold"))
        self.header_lbl.pack(pady=(14, 8))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=8)

        # Left: roster
        left = ctk.CTkFrame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        ctk.CTkLabel(left, text="Students in your class",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(pady=8)
        self.roster_scroll = ctk.CTkScrollableFrame(left)
        self.roster_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Right: all students
        right = ctk.CTkFrame(body)
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))
        ctk.CTkLabel(right, text="All students",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(pady=8)
        self.all_scroll = ctk.CTkScrollableFrame(right)
        self.all_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        ctk.CTkButton(self, text="🔄  Refresh", command=self._load).pack(pady=(0, 12))

    def _load(self):
        # Find my class
        resp = self.net.request("MY_CLASS")
        parts = resp.split("|")
        if len(parts) < 2 or not parts[1]:
            self.class_id = None
            self.class_name = None
            self.header_lbl.configure(
                text="You are not assigned to a class. Ask the admin to add you.",
                text_color="#ff7777")
            for w in list(self.roster_scroll.winfo_children()) + list(self.all_scroll.winfo_children()):
                w.destroy()
            return
        cid_name = parts[1].split("~")
        self.class_id = int(cid_name[0])
        self.class_name = cid_name[1]
        self.header_lbl.configure(text=f"Class: {self.class_name}", text_color="#4dff88")

        # Roster
        resp = self.net.request(f"LIST_CLASS_MEMBERS|{self.class_id}")
        members = []
        if resp.startswith("MEMBERS|") and len(resp) > len("MEMBERS|"):
            for item in resp.split("|")[1:]:
                if not item: continue
                f = item.split("~")
                if f[2] == "student":
                    members.append({"id": int(f[0]), "username": f[1]})
        self._render_roster(members)

        # All students
        resp = self.net.request("LIST_STUDENTS")
        students = []
        if resp.startswith("STUDENTS|") and len(resp) > len("STUDENTS|"):
            for item in resp.split("|")[1:]:
                if not item: continue
                f = item.split("~")
                students.append({"id": int(f[0]), "username": f[1], "class": f[2]})
        member_ids = {m["id"] for m in members}
        self._render_all(students, member_ids)

    def _render_roster(self, members):
        for w in self.roster_scroll.winfo_children():
            w.destroy()
        if not members:
            ctk.CTkLabel(self.roster_scroll, text="(no students yet)",
                         text_color="gray").pack(pady=20)
            return
        for m in members:
            row = ctk.CTkFrame(self.roster_scroll)
            row.pack(fill="x", pady=2, padx=4)
            ctk.CTkLabel(row, text=f"👤 {m['username']}",
                         font=ctk.CTkFont(size=12)).pack(side="left", padx=10, pady=6)
            ctk.CTkButton(row, text="Remove", width=80, height=26,
                          fg_color="#7f0000", hover_color="#9f1010",
                          command=lambda uid=m["id"]: self._remove(uid)).pack(side="right", padx=8)

    def _render_all(self, students, member_ids):
        for w in self.all_scroll.winfo_children():
            w.destroy()
        if not students:
            ctk.CTkLabel(self.all_scroll, text="(no students registered)",
                         text_color="gray").pack(pady=20)
            return
        for s in students:
            row = ctk.CTkFrame(self.all_scroll)
            row.pack(fill="x", pady=2, padx=4)
            in_my_class = s["id"] in member_ids
            current = s["class"] if s["class"] else "(no class)"
            label_color = "#4dff88" if in_my_class else "gray"
            ctk.CTkLabel(row, text=f"👤 {s['username']}",
                         font=ctk.CTkFont(size=12)).pack(side="left", padx=10, pady=6)
            ctk.CTkLabel(row, text=current, text_color=label_color,
                         font=ctk.CTkFont(size=10)).pack(side="left", padx=4)
            if in_my_class:
                ctk.CTkLabel(row, text="✓ in class", text_color="#4dff88",
                             font=ctk.CTkFont(size=10)).pack(side="right", padx=8)
            else:
                ctk.CTkButton(row, text="Add", width=60, height=26,
                              command=lambda uid=s["id"], uname=s["username"], cls=s["class"]:
                                  self._add(uid, uname, cls)).pack(side="right", padx=8)

    def _add(self, user_id, username, current_class):
        if current_class:
            if not messagebox.askyesno("Move student?",
                                       f"{username} is in '{current_class}'. Moving them to your class will remove them from there. Continue?"):
                return
        resp = self.net.request(f"ASSIGN_USER|{user_id}|{self.class_id}")
        parts = resp.split("|")
        if parts[0] == "OK":
            self._load()
        else:
            messagebox.showerror("Error", parts[1] if len(parts) > 1 else "Failed")

    def _remove(self, user_id):
        if not messagebox.askyesno("Remove student?", "Remove this student from your class?"):
            return
        resp = self.net.request(f"REMOVE_USER|{user_id}|{self.class_id}")
        parts = resp.split("|")
        if parts[0] == "OK":
            self._load()
        else:
            messagebox.showerror("Error", parts[1] if len(parts) > 1 else "Failed")


# ─────────────────────────────────────────────
#  Activity Log Viewer window
# ─────────────────────────────────────────────

class ActivityLogViewer(ctk.CTkToplevel):
    def __init__(self, parent: TeacherApp, net: NetworkClient):
        super().__init__(parent)
        self.parent_app = parent
        self.net = net
        self.title("Activity Logs")
        self.geometry("900x600")
        self._build_ui()
        self.refresh_logs()

    def _build_ui(self):
        # Header with filters
        header = ctk.CTkFrame(self)
        header.pack(fill="x", padx=16, pady=12)

        ctk.CTkLabel(header, text="Activity Logs",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=12)

        # Filter controls
        filter_frame = ctk.CTkFrame(header, fg_color="transparent")
        filter_frame.pack(side="right", padx=12)

        ctk.CTkLabel(filter_frame, text="Limit:").pack(side="left", padx=4)
        self.limit_entry = ctk.CTkEntry(filter_frame, width=60)
        self.limit_entry.insert(0, "100")
        self.limit_entry.pack(side="left", padx=4)

        ctk.CTkLabel(filter_frame, text="Action:").pack(side="left", padx=(12, 4))
        self.action_entry = ctk.CTkEntry(filter_frame, width=100)
        self.action_entry.pack(side="left", padx=4)

        ctk.CTkLabel(filter_frame, text="User:").pack(side="left", padx=(12, 4))
        self.user_entry = ctk.CTkEntry(filter_frame, width=100)
        self.user_entry.pack(side="left", padx=4)

        ctk.CTkButton(filter_frame, text="🔍 Filter", command=self.refresh_logs).pack(side="left", padx=8)
        ctk.CTkButton(filter_frame, text="🔄 Refresh", command=self.refresh_logs).pack(side="left", padx=4)

        # Log display area
        self.log_scroll = ctk.CTkScrollableFrame(self)
        self.log_scroll.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    def refresh_logs(self):
        """Fetch and display activity logs"""
        try:
            limit = self.limit_entry.get().strip() or "100"
            action_filter = self.action_entry.get().strip()
            user_filter = self.user_entry.get().strip()
            
            # Always send all three positional args so user_filter doesn't shift
            # into the action_filter slot when action_filter is empty
            request_parts = ["GET_ACTIVITY_LOGS", limit, action_filter, user_filter]
            
            resp = self.net.request("|".join(request_parts))
            self._render_logs(resp)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to fetch logs: {e}")

    def _render_logs(self, response: str):
        """Render the logs in the scrollable frame"""
        # Clear existing content
        for widget in self.log_scroll.winfo_children():
            widget.destroy()

        parts = response.split("|")
        if parts[0] != "ACTIVITY_LOGS" or len(parts) < 2:
            ctk.CTkLabel(self.log_scroll, text="No logs found or error occurred.",
                         text_color="gray").pack(pady=20)
            return

        # Header row
        header = ctk.CTkFrame(self.log_scroll, fg_color="gray25")
        header.pack(fill="x", pady=(0, 8))
        
        headers = ["User", "Action", "Details", "IP Address", "Timestamp"]
        widths = [120, 120, 300, 120, 160]
        
        for header_text, width in zip(headers, widths):
            ctk.CTkLabel(header, text=header_text, width=width,
                        font=ctk.CTkFont(weight="bold")).pack(side="left", padx=4, pady=8)

        # Log entries
        for entry_str in parts[1:]:
            if not entry_str:
                continue
            fields = entry_str.split("~")
            if len(fields) < 5:
                continue

            username, action, details, ip_address, timestamp = fields[:5]
            
            row = ctk.CTkFrame(self.log_scroll)
            row.pack(fill="x", pady=2)

            # Color code by action type
            action_color = self._get_action_color(action)

            ctk.CTkLabel(row, text=username, width=120, anchor="w").pack(side="left", padx=4, pady=4)
            ctk.CTkLabel(row, text=action, width=120, text_color=action_color, anchor="w").pack(side="left", padx=4, pady=4)
            ctk.CTkLabel(row, text=details[:50] + "..." if len(details) > 50 else details, 
                        width=300, anchor="w", wraplength=280).pack(side="left", padx=4, pady=4)
            ctk.CTkLabel(row, text=ip_address, width=120, anchor="w").pack(side="left", padx=4, pady=4)
            ctk.CTkLabel(row, text=timestamp[:16], width=160, anchor="w").pack(side="left", padx=4, pady=4)

    def _get_action_color(self, action: str) -> str:
        """Get color for different action types"""
        colors = {
            "LOGIN": "#4dff88",
            "REGISTER": "#4d9eff", 
            "CREATE_TEST": "#ffcc4d",
            "SUBMIT_TEST": "#ff6b6b",
            "DELETE_TEST": "#ff4444",
            "SHARE_TEST": "#9b59b6"
        }
        return colors.get(action, "white")


# ─────────────────────────────────────────────
#  Test Editor window
# ─────────────────────────────────────────────

class TestEditorWindow(ctk.CTkToplevel):
    def __init__(self, parent: TeacherApp, net: NetworkClient, test_id=None, title="", questions=None, time_limit=0):
        super().__init__(parent)
        self.parent_app = parent
        self.net = net
        self.title("Test Editor")
        self.geometry("760x680")
        self.questions = questions if questions is not None else []
        self.test_id = test_id
        self.initial_title = title
        self.initial_time_limit = time_limit
        self._build_ui()

    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=12)

        ctk.CTkLabel(top, text="Test Title:").pack(side="left")
        self.title_entry = ctk.CTkEntry(top, width=260, placeholder_text="My Test Title")
        if self.initial_title:
            self.title_entry.insert(0, self.initial_title)
        self.title_entry.pack(side="left", padx=8)

        ctk.CTkLabel(top, text="Time (min, 0=∞):").pack(side="left", padx=(8, 4))
        self.time_limit_entry = ctk.CTkEntry(top, width=60)
        self.time_limit_entry.insert(0, str(self.initial_time_limit))
        self.time_limit_entry.pack(side="left", padx=4)

        btn_text = "Save" if self.test_id else "Create Test"
        self.create_btn = ctk.CTkButton(top, text=btn_text, command=self.create_test)
        self.create_btn.pack(side="left", padx=4)

        self.status_lbl = ctk.CTkLabel(self, text="", text_color="gray")
        if self.test_id:
            self.status_lbl.configure(text=f"Editing Test ID: {self.test_id}", text_color="#4dff88")
        self.status_lbl.pack()

        # ── Question editor ──────────────────────
        qframe = ctk.CTkFrame(self)
        qframe.pack(fill="x", padx=16, pady=8)

        ctk.CTkLabel(qframe, text="Add Question",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(
                         row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(12, 6))

        ctk.CTkLabel(qframe, text="Type:").grid(row=1, column=0, padx=12, pady=4, sticky="e")
        self.qtype_var = ctk.StringVar(value="mc")
        type_menu = ctk.CTkOptionMenu(qframe, variable=self.qtype_var,
                                       values=["mc", "tf", "short"],
                                       command=self._on_type_change)
        type_menu.grid(row=1, column=1, padx=8, pady=4, sticky="w")

        ctk.CTkLabel(qframe, text="Prompt:").grid(row=2, column=0, padx=12, pady=4, sticky="ne")
        self.prompt_entry = ctk.CTkTextbox(qframe, width=500, height=60)
        self.prompt_entry.grid(row=2, column=1, columnspan=3, padx=8, pady=4, sticky="w")

        # Multiple choice options
        self.mc_frame = ctk.CTkFrame(qframe, fg_color="transparent")
        self.mc_frame.grid(row=3, column=0, columnspan=4, padx=12, pady=4, sticky="w")
        labels = ["A", "B", "C", "D"]
        self.option_entries = []
        for i, lbl in enumerate(labels):
            ctk.CTkLabel(self.mc_frame, text=f"Option {lbl}:").grid(
                row=i, column=0, padx=4, pady=2, sticky="e")
            e = ctk.CTkEntry(self.mc_frame, width=380)
            e.grid(row=i, column=1, padx=8, pady=2)
            self.option_entries.append(e)

        ctk.CTkLabel(qframe, text="Answer:").grid(row=4, column=0, padx=12, pady=4, sticky="e")
        self.answer_entry = ctk.CTkEntry(qframe, width=300,
                                          placeholder_text="For MC: A/B/C/D  |  TF: True/False  |  Short: exact text")
        self.answer_entry.grid(row=4, column=1, columnspan=2, padx=8, pady=4, sticky="w")

        ctk.CTkButton(qframe, text="➕ Add Question",
                      command=self.add_question).grid(
                          row=5, column=1, padx=8, pady=12, sticky="w")

        # ── Question list ────────────────────────
        ctk.CTkLabel(self, text="Questions in this test:",
                     font=ctk.CTkFont(size=13)).pack(anchor="w", padx=16, pady=(4, 0))

        self.q_scroll = ctk.CTkScrollableFrame(self, height=160)
        self.q_scroll.pack(fill="x", padx=16, pady=8)

        ctk.CTkButton(self, text="✅  Finish Test",
                      fg_color="#1a4d1a", hover_color="#2a6a2a",
                      height=38, font=ctk.CTkFont(size=14, weight="bold"),
                      command=self.finish).pack(pady=12, padx=16, fill="x")
        
        if self.questions:
            self._render_q_list()

    def _on_type_change(self, value):
        if value == "mc":
            self.mc_frame.grid()
        else:
            self.mc_frame.grid_remove()

    def create_test(self):
        title = self.title_entry.get().strip()
        if not title:
            messagebox.showwarning("Input Error", "Please enter a test title.")
            return

        try:
            time_limit = max(0, int(self.time_limit_entry.get().strip() or "0"))
        except ValueError:
            messagebox.showwarning("Input Error", "Time limit must be a whole number of minutes.")
            return

        if self.test_id:
            resp = self.net.request(f"EDIT_TEST_TITLE|{self.test_id}|{title}")
            parts = resp.split("|")
            if parts[0] != "OK":
                messagebox.showerror("Error", parts[1])
                return
            resp2 = self.net.request(f"SET_TIME_LIMIT|{self.test_id}|{time_limit}")
            parts2 = resp2.split("|")
            if parts2[0] != "OK":
                messagebox.showerror("Error", parts2[1])
                return
            self.status_lbl.configure(text="✅ Test updated", text_color="#4dff88")
            return

        resp = self.net.request(f"CREATE_TEST|{title}|{time_limit}")
        parts = resp.split("|")
        if parts[0] == "OK":
            sounds.success()
            self.test_id = int(parts[1])
            self.status_lbl.configure(
                text=f"✅ Test created (ID: {self.test_id}). Now add questions.",
                text_color="#4dff88")
            self.title_entry.configure(state="disabled")
            self.create_btn.configure(state="disabled")
        else:
            sounds.error()
            messagebox.showerror("Error", parts[1])

    def add_question(self):
        if not self.test_id:
            messagebox.showwarning("No Test", "Create a test first.")
            return
        qtype = self.qtype_var.get()
        prompt = self.prompt_entry.get("1.0", "end").strip()
        answer = self.answer_entry.get().strip()

        if not prompt or not answer:
            messagebox.showwarning("Input Error", "Prompt and answer are required.")
            return

        opt_a = opt_b = opt_c = opt_d = ""
        if qtype == "mc":
            opt_a = self.option_entries[0].get().strip()
            opt_b = self.option_entries[1].get().strip()
            opt_c = self.option_entries[2].get().strip()
            opt_d = self.option_entries[3].get().strip()
            if not all([opt_a, opt_b, opt_c, opt_d]):
                messagebox.showwarning("Input Error", "Fill in all 4 options for MC.")
                return

        position = len(self.questions) + 1
        # Pipe-safe: replace | with ‖ in user text
        prompt_safe = prompt.replace("|", "‖")
        msg = (f"ADD_QUESTION|{self.test_id}|{position}|{qtype}|{prompt_safe}"
               f"|{opt_a}|{opt_b}|{opt_c}|{opt_d}|{answer}")
        resp = self.net.request(msg)
        parts = resp.split("|")
        if parts[0] == "OK":
            sounds.click()
            q = {"id": int(parts[1]), "pos": position, "qtype": qtype,
                 "prompt": prompt[:50], "answer": answer}
            self.questions.append(q)
            self._render_q_list()
            # Clear fields
            self.prompt_entry.delete("1.0", "end")
            self.answer_entry.delete(0, "end")
            for e in self.option_entries:
                e.delete(0, "end")
        else:
            sounds.error()
            messagebox.showerror("Error", parts[1])

    def _render_q_list(self):
        for w in self.q_scroll.winfo_children():
            w.destroy()
        for q in self.questions:
            row = ctk.CTkFrame(self.q_scroll)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row,
                         text=f"Q{q['pos']} [{q['qtype'].upper()}]  {q['prompt']}…",
                         anchor="w").pack(side="left", padx=12, pady=6)

            del_cmd = lambda qid=q.get("id"): self.delete_question(qid)
            if q.get("id"):
                ctk.CTkButton(row, text="🗑", width=30, fg_color="#8B0000",
                              hover_color="#600000", command=del_cmd).pack(side="right", padx=12)

            ctk.CTkLabel(row, text=f"Ans: {q['answer']}",
                         text_color="gray", anchor="e").pack(side="right", padx=12)

    def delete_question(self, q_id):
        if not q_id: return
        confirm = messagebox.askyesno("Delete", "Remove this question?")
        if not confirm: return
        resp = self.net.request(f"DELETE_QUESTION|{q_id}")
        if resp.startswith("OK|"):
            sounds.delete()
            self.questions = [q for q in self.questions if q.get("id") != q_id]
            self._render_q_list()
        else:
            sounds.error()
            messagebox.showerror("Error", resp.split("|")[1] if "|" in resp else resp)

    def finish(self):
        self.parent_app.refresh_tests()
        self.destroy()


# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    app = LoginWindow()
    app.mainloop()
