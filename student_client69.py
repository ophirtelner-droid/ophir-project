"""
student_client.py – Student GUI
================================
CustomTkinter-based interface for:
  • Register / Login
  • Browse available tests
  • Take a test (one question per page)
  • View personal result history
"""

import customtkinter as ctk
from tkinter import messagebox
from network import NetworkClient
import os
import platform
from PIL import Image
import sounds

try:
    from AppKit import NSApp, NSApplicationPresentationOptions as Opt
    _APPKIT_AVAILABLE = True
except ImportError:
    _APPKIT_AVAILABLE = False

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Student brand colours ──────────────────────────────────────────
_PRI   = "#1565C0"   # deep blue
_ACC   = "#42A5F5"   # sky blue
_DBG   = "#0D1B2A"   # dark background
_HOV   = "#0D47A1"   # hover
_DIM   = "#90CAF9"   # dimmed text
_CARD  = "#111E2E"   # card bg


# ─────────────────────────────────────────────
#  macOS kiosk helpers  (OS-level API via AppKit)
# ─────────────────────────────────────────────

def _kiosk_enter(window=None):
    """
    Enter exam lockdown mode.
    - Fullscreen + always-on-top on both platforms via Tkinter.
    - macOS: also hides Dock/menu bar and disables Cmd+Q/Tab via AppKit when available.
    - Grabs all input focus so the student cannot switch to another window.
    The Tkinter lock is applied via window.after() so it fires once the
    window is actually mapped; grab_set() silently fails on unmapped windows.
    """
    system = platform.system()

    if system == "Darwin" and _APPKIT_AVAILABLE:
        try:
            options = (
                Opt.NSApplicationPresentationHideDock
                | Opt.NSApplicationPresentationHideMenuBar
                | Opt.NSApplicationPresentationDisableForceQuit
                | Opt.NSApplicationPresentationDisableSessionTermination
                | Opt.NSApplicationPresentationDisableHideApplication
            )
            NSApp.setPresentationOptions_(options)
            NSApp.activateIgnoringOtherApps_(True)
            print("[Kiosk] Entered macOS AppKit kiosk mode")
        except Exception as e:
            print(f"[Kiosk] AppKit kiosk error: {e}")

    if not window:
        return

    # Block the close button immediately (before the window renders)
    window.protocol("WM_DELETE_WINDOW", lambda: None)

    def _apply_lock():
        try:
            window.attributes("-fullscreen", True)
            window.attributes("-topmost", True)
            window.lift()
            window.focus_force()
            window.grab_set()
            print(f"[Kiosk] Window locked ({system})")
        except Exception as e:
            print(f"[Kiosk] Window lockdown error: {e}")

    # Recapture focus whenever the OS tries to hand it to another window.
    # Use after() to avoid re-entering the FocusOut handler recursively.
    def _refocus(_event=None):
        try:
            window.after(10, window.focus_force)
        except Exception:
            pass

    window.bind("<FocusOut>", _refocus)

    # Defer the actual fullscreen + grab until the window is rendered
    window.after(150, _apply_lock)


def _kiosk_exit(window=None):
    """Restore normal presentation options and release the input grab."""
    system = platform.system()

    if system == "Darwin" and _APPKIT_AVAILABLE:
        try:
            NSApp.setPresentationOptions_(Opt.NSApplicationPresentationDefault)
            print("[Kiosk] Exited macOS AppKit kiosk mode")
        except Exception:
            pass

    if window:
        try:
            window.unbind("<FocusOut>")
            window.grab_release()
            window.attributes("-fullscreen", False)
            window.attributes("-topmost", False)
            print(f"[Kiosk] Window unlocked ({system})")
        except Exception:
            pass


# ─────────────────────────────────────────────
#  OS-level hardware access: webcam snapshot
# ─────────────────────────────────────────────

def capture_webcam_snapshot(username: str) -> str:
    """
    Capture a single frame from the system webcam using OpenCV.
    This uses the OS camera driver (OS-level hardware API call).
    Saves the image to '<username>_snapshot.jpg'.
    Returns the saved filepath, or an empty string on failure.
    """
    try:
        import cv2  # pip install opencv-python
        cap = cv2.VideoCapture(0)  # 0 = default system camera
        if not cap.isOpened():
            print("[Camera] No webcam detected — skipping snapshot.")
            return ""
        # Discard the first ~20 frames so auto-exposure can settle;
        # without this the captured frame is nearly black.
        for _ in range(20):
            cap.read()
        ret, frame = cap.read()
        cap.release()
        if ret:
            filename = f"{username}_snapshot.jpg"
            cv2.imwrite(filename, frame)
            print(f"[Camera] Snapshot saved: {filename}")
            return filename
        return ""
    except ImportError:
        print("[Camera] opencv-python not installed — skipping snapshot.")
        print("         Run: pip install opencv-python")
        return ""
    except Exception as e:
        print(f"[Camera] Snapshot error: {e}")
        return ""


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def parse_tests(response: str):
    parts = response.split("|")
    if parts[0] != "TESTS" or len(parts) < 2 or parts[1] == "":
        return []
    tests = []
    for item in parts[1:]:
        f = item.split("~")
        tests.append({"id": int(f[0]), "title": f[1],
                       "qcount": int(f[2]), "date": f[3],
                       "time_limit": int(f[4]) if len(f) > 4 and f[4].isdigit() else 0})
    return tests


def parse_test_data(response: str):
    """
    TEST_DATA|title|time_limit|qid~pos~qtype~prompt~optA~optB~optC~optD~ans|...
    Returns (title, time_limit_minutes, [question_dicts])
    """
    parts = response.split("|")
    if parts[0] != "TEST_DATA":
        return None, 0, []
    title = parts[1]
    try:
        time_limit = int(parts[2])
    except (ValueError, IndexError):
        time_limit = 0
    questions = []
    for raw in parts[3:]:
        f = raw.split("~")
        questions.append({
            "id":      int(f[0]),
            "pos":     int(f[1]),
            "qtype":   f[2],
            "prompt":  f[3].replace("‖", "|"),
            "opt_a":   f[4], "opt_b": f[5],
            "opt_c":   f[6], "opt_d": f[7],
            "answer":  f[8],
        })
    return title, time_limit, questions


def parse_results(response: str):
    parts = response.split("|")
    if parts[0] != "RESULTS" or len(parts) < 2 or parts[1] == "":
        return []
    results = []
    for item in parts[1:]:
        f = item.split("~")
        results.append({"title": f[0], "score": float(f[1]), "date": f[2]})
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
        self.title("Quizy – Student Register")
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
        ctk.CTkLabel(brand, text="Student Portal", font=ctk.CTkFont(size=12),
                     text_color=_DIM).pack(pady=(4, 0))

        form = ctk.CTkFrame(self, fg_color=_DBG, corner_radius=0)
        form.pack(side="left", fill="both", expand=True)
        inner = ctk.CTkFrame(form, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(inner, text="Create Account",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color="white").pack(pady=(0, 4))
        ctk.CTkLabel(inner, text="Join Quizy today",
                     font=ctk.CTkFont(size=13), text_color=_DIM).pack(pady=(0, 18))

        for label, attr, ph, kw in [
            ("Username", "username_entry", "student1", {}),
            ("Email",    "email_entry",    "student@email.com", {}),
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
        resp = self.net.request(f"REGISTER|{u}|{e}|{p}|student")
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
        self.title("Quizy – Student Login")
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
        # Left brand panel
        brand = ctk.CTkFrame(self, width=210, fg_color=_PRI, corner_radius=0)
        brand.pack(side="left", fill="y")
        brand.pack_propagate(False)
        ctk.CTkLabel(brand, text="Q", font=ctk.CTkFont(size=64, weight="bold"),
                     text_color="white").pack(pady=(55, 0))
        ctk.CTkLabel(brand, text="Quizy", font=ctk.CTkFont(size=20, weight="bold"),
                     text_color="white").pack()
        ctk.CTkLabel(brand, text="Student Portal", font=ctk.CTkFont(size=12),
                     text_color=_DIM).pack(pady=(4, 0))

        # Right form panel
        form = ctk.CTkFrame(self, fg_color=_DBG, corner_radius=0)
        form.pack(side="left", fill="both", expand=True)
        inner = ctk.CTkFrame(form, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(inner, text="Welcome back!",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color="white").pack(pady=(0, 4))
        ctk.CTkLabel(inner, text="Sign in to continue",
                     font=ctk.CTkFont(size=13), text_color=_DIM).pack(pady=(0, 22))

        ctk.CTkLabel(inner, text="Username", font=ctk.CTkFont(size=12),
                     text_color=_DIM).pack(anchor="w")
        self.username_entry = ctk.CTkEntry(inner, width=290, height=40,
                                           placeholder_text="student1",
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
            role = parts[1]
            if role != "student":
                sounds.error()
                messagebox.showerror("Access Denied",
                                     "This portal is for students only.")
                return
            sounds.login()
            # parts[3] is the in_class flag (1 = assigned, 0 = waiting)
            in_class = int(parts[3]) if len(parts) > 3 else 0
            self.withdraw()
            if in_class:
                app = StudentApp(self.net, u)
                app.mainloop()
            else:
                # Run waiting room; when assigned it sets _transitioned=True and quits
                app = WaitingRoomApp(self.net, u)
                app.mainloop()
                # If user was assigned to a class while waiting, open the full app
                if getattr(app, '_transitioned', False):
                    student_app = StudentApp(self.net, u)
                    student_app.mainloop()

            # Reconnect and show login again so the student can log in as someone else
            try:
                self.net = NetworkClient()
                self.net.connect()
            except Exception:
                pass
            self.username_entry.delete(0, "end")
            self.password_entry.delete(0, "end")
            self.deiconify()
        else:
            sounds.error()
            messagebox.showerror("Login Failed",
                                 parts[1] if len(parts) > 1 else "Unknown error")


# ─────────────────────────────────────────────
#  Waiting Room  (student not yet in a class)
# ─────────────────────────────────────────────

class WaitingRoomApp(ctk.CTkToplevel):
    """
    Shown when a student has logged in but hasn't been placed in a class yet.
    Polls the server every 10 s and transitions to StudentApp when assigned.
    """
    _DOTS = ["⏳ Waiting", "⏳ Waiting.", "⏳ Waiting..", "⏳ Waiting..."]

    def __init__(self, net: NetworkClient, username: str):
        super().__init__()
        self.net = net
        self.username = username
        self.title("Quizy – Waiting Room")
        self.geometry("480x320")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._dot_idx = 0
        self._polling = True
        self._build_ui()
        self._animate()
        self._auto_poll()

    def _on_close(self):
        self._polling = False
        self.net.close()
        self.quit()   # exits the mainloop() called in LoginWindow.do_login
        self.destroy()

    def _build_ui(self):
        self.configure(fg_color=_DBG)
        # Top brand bar
        top = ctk.CTkFrame(self, height=70, fg_color=_PRI, corner_radius=0)
        top.pack(fill="x")
        top.pack_propagate(False)
        ctk.CTkLabel(top, text="Quizy – Waiting Room",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="white").pack(side="left", padx=24, expand=True)
        ctk.CTkLabel(top, text=f"👤 {self.username}",
                     font=ctk.CTkFont(size=12), text_color=_DIM).pack(side="right", padx=20)

        self.status_lbl = ctk.CTkLabel(
            self, text="⏳ Waiting",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color="#FFD54F"
        )
        self.status_lbl.pack(pady=(40, 8))

        ctk.CTkLabel(
            self,
            text="You have not been placed into a class yet.\nPlease wait for an admin to assign you.",
            font=ctk.CTkFont(size=13), text_color="gray", justify="center"
        ).pack(pady=(0, 30))

        ctk.CTkButton(
            self, text="🔄  Check Now", width=180, height=40,
            fg_color=_PRI, hover_color=_HOV,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._check_now
        ).pack()

    def _animate(self):
        if not self._polling:
            return
        self.status_lbl.configure(text=self._DOTS[self._dot_idx % len(self._DOTS)])
        self._dot_idx += 1
        self.after(600, self._animate)

    def _auto_poll(self):
        """Automatically polls the server every 10 seconds."""
        if not self._polling:
            return
        self._check_class_status()
        self.after(10_000, self._auto_poll)

    def _check_now(self):
        """Manual refresh triggered by the button."""
        self._check_class_status()

    def _check_class_status(self):
        try:
            resp = self.net.request("CHECK_CLASS")
            parts = resp.split("|")
            if parts[0] == "CLASS_STATUS" and parts[1] == "1":
                self._polling = False
                self._transition_to_app()
        except Exception:
            pass  # Server may be busy; will retry

    def _transition_to_app(self):
        """Signal LoginWindow to open the full student app after this quits."""
        self._polling = False
        self._transitioned = True   # flag checked by LoginWindow.do_login
        self.quit()                 # exits mainloop cleanly; LoginWindow handles the rest
        self.destroy()


# ─────────────────────────────────────────────
#  Main Student application
# ─────────────────────────────────────────────

class StudentApp(ctk.CTkToplevel):
    def __init__(self, net: NetworkClient, username: str):
        super().__init__()
        self.net = net
        self.username = username
        self.title(f"Quizy – Student: {username}")
        self.geometry("820x580")
        self.minsize(700, 450)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self.show_tests_tab()

    def _on_close(self):
        self.net.close()
        self.quit()   # exits the mainloop() called in LoginWindow.do_login
        self.destroy()

    def _logout(self):
        self._logged_out = True
        self.net.close()
        self.quit()
        self.destroy()

    def _build_ui(self):
        # Sidebar
        self.sidebar = ctk.CTkFrame(self, width=210, corner_radius=0, fg_color="#0A1628")
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # Logo area
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
        ctk.CTkLabel(self.sidebar, text="Student",
                     font=ctk.CTkFont(size=11), text_color="gray").pack(pady=(0, 10))

        ctk.CTkButton(self.sidebar, text="📷  Update Picture",
                      fg_color="#1a2a3a", hover_color=_PRI, height=34,
                      command=self.update_picture).pack(padx=14, pady=(0, 16), fill="x")

        sep = ctk.CTkFrame(self.sidebar, height=1, fg_color="#1e3050")
        sep.pack(fill="x", padx=14, pady=(0, 12))

        for icon, label, cmd in [
            ("📋", "Available Tests", self.show_tests_tab),
            ("📈", "My Results",      self.show_results_tab),
        ]:
            ctk.CTkButton(self.sidebar, text=f"{icon}  {label}",
                          fg_color="#162235", hover_color=_PRI,
                          anchor="w", height=38,
                          command=cmd).pack(padx=14, pady=3, fill="x")

        ctk.CTkButton(self.sidebar, text="🔄  Refresh",
                      fg_color="#1a2030", hover_color="#253050",
                      anchor="w", height=34,
                      command=self._refresh_current).pack(padx=14, pady=(16, 3), fill="x")

        ctk.CTkButton(self.sidebar, text="🚪  Log Out",
                      fg_color="#1a2030", hover_color="#8B0000",
                      anchor="w", height=34,
                      command=self._logout).pack(padx=14, pady=(6, 3), fill="x", side="bottom")

        # Main content area
        self.main = ctk.CTkFrame(self, fg_color="#0D1B2A")
        self.main.pack(side="left", fill="both", expand=True)
        self._current_tab = "tests"

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
        # Pass parent=self so tkinter anchors the dialog to this window and
        # returns focus cleanly — without it the window stays in a broken
        # grab state and every click re-opens the dialog.
        msg = messagebox.askyesno("Update Picture",
                                  "Take a new profile picture with your webcam?",
                                  parent=self)
        if msg:
            res = capture_webcam_snapshot(self.username)
            if res:
                self.load_profile_picture()
                messagebox.showinfo("Updated", "Profile picture updated!", parent=self)
            else:
                messagebox.showerror("Error", "Could not capture webcam.", parent=self)

    def _clear_main(self):
        for w in self.main.winfo_children():
            w.destroy()

    def _refresh_current(self):
        if self._current_tab == "tests":
            self.show_tests_tab()
        else:
            self.show_results_tab()

    # ── Tests tab ────────────────────────────────

    def show_tests_tab(self):
        self._current_tab = "tests"
        self._clear_main()
        resp = self.net.request("LIST_TESTS")
        self.tests = parse_tests(resp)

        hdr = ctk.CTkFrame(self.main, height=50, fg_color=_PRI, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="📋  Available Tests",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="white").pack(side="left", padx=20, expand=True)

        scroll = ctk.CTkScrollableFrame(self.main, fg_color="#0D1B2A")
        scroll.pack(fill="both", expand=True, padx=16, pady=16)

        if not self.tests:
            ctk.CTkLabel(scroll, text="No tests available yet. Check back later.",
                         text_color="gray").pack(pady=40)
            return

        for test in self.tests:
            self._make_test_card(scroll, test)

    def _make_test_card(self, parent, test: dict):
        card = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=10)
        card.pack(fill="x", pady=5, padx=2)

        accent = ctk.CTkFrame(card, width=5, fg_color=_ACC, corner_radius=0)
        accent.pack(side="left", fill="y", padx=(0, 12))

        info_frame = ctk.CTkFrame(card, fg_color="transparent")
        info_frame.pack(side="left", fill="x", expand=True, pady=14)

        ctk.CTkLabel(info_frame, text=f"📝  {test['title']}",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="white", anchor="w").pack(anchor="w")
        tl = test.get("time_limit", 0)
        time_text = f"  •  ⏱ {tl} min" if tl > 0 else "  •  ⏱ no limit"
        ctk.CTkLabel(info_frame,
                     text=f"{test['qcount']} question(s)  •  {test['date'][:10]}{time_text}",
                     font=ctk.CTkFont(size=11), text_color=_DIM,
                     anchor="w").pack(anchor="w", pady=(2, 0))

        ctk.CTkButton(card, text="Start ▶", width=100, height=34,
                      fg_color=_PRI, hover_color=_HOV,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=lambda tid=test["id"], tt=test["title"]:
                          self.start_test(tid, tt)).pack(side="right", padx=14, pady=14)

    # ── Results tab ──────────────────────────────

    def show_results_tab(self):
        self._current_tab = "results"
        self._clear_main()
        resp = self.net.request("GET_RESULTS")
        results = parse_results(resp)

        hdr = ctk.CTkFrame(self.main, height=50, fg_color=_PRI, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="📈  My Results",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="white").pack(side="left", padx=20, expand=True)

        scroll = ctk.CTkScrollableFrame(self.main, fg_color="#0D1B2A")
        scroll.pack(fill="both", expand=True, padx=16, pady=16)

        if not results:
            ctk.CTkLabel(scroll, text="You haven't submitted any tests yet.",
                         text_color="gray").pack(pady=40)
            return

        for result in results:
            self._make_result_card(scroll, result)

    def _make_result_card(self, parent, result: dict):
        score = result['score']
        card = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=10)
        card.pack(fill="x", pady=5, padx=2)

        color = _ACC if score >= 60 else ("#FFB74D" if score >= 40 else "#EF5350")
        accent = ctk.CTkFrame(card, width=5, fg_color=color, corner_radius=0)
        accent.pack(side="left", fill="y", padx=(0, 12))

        info_frame = ctk.CTkFrame(card, fg_color="transparent")
        info_frame.pack(side="left", fill="x", expand=True, pady=14)
        ctk.CTkLabel(info_frame, text=f"📊 {result['title']}",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="white", anchor="w").pack(anchor="w")
        ctk.CTkLabel(info_frame,
                     text=f"{result['date'][:10]}",
                     font=ctk.CTkFont(size=11), text_color=_DIM,
                     anchor="w").pack(anchor="w", pady=(2, 0))

        ctk.CTkLabel(card, text=f"{score:.1f}%",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=color).pack(side="right", padx=18, pady=14)

    # ── Test taking ───────────────────────────────

    def start_test(self, test_id: int, test_title: str):
        """Start taking a test - enter kiosk mode and show first question."""
        test_window = ctk.CTkToplevel(self)
        test_window.title(f"Quizy - Test: {test_title}")
        test_window.geometry("900x600")
        test_window.minsize(800, 500)

        # kiosk_enter sets WM_DELETE_WINDOW to a no-op and grabs input
        _kiosk_enter(test_window)

        # Fetch test data
        resp = self.net.request(f"GET_TEST|{test_id}")
        title, time_limit, questions = parse_test_data(resp)

        # Create test interface
        test_app = TestTakingWindow(test_window, self.net, title, questions, test_id, self.username, time_limit)
        test_app.show_question(0)

    def _exit_test(self, window):
        """Exit kiosk mode and close test window."""
        _kiosk_exit(window)
        window.destroy()


class TestTakingWindow:
    """Handles the actual test-taking interface."""
    
    def __init__(self, parent, net, title, questions, test_id, username, time_limit=0):
        self.parent = parent
        self.net = net
        self.title = title
        self.questions = questions
        self.test_id = test_id
        self.username = username
        self.current_q = 0
        self.answers = {}  # question_id -> answer
        self.time_limit_minutes = time_limit  # 0 = unlimited
        self.remaining_seconds = time_limit * 60 if time_limit > 0 else 0
        self.timer_label = None
        self.submitted = False
        self._timer_job = None

        self._build_ui()
        if self.time_limit_minutes > 0:
            self._tick_timer()
    
    def _build_ui(self):
        # Header
        header = ctk.CTkFrame(self.parent)
        header.pack(fill="x", padx=20, pady=(20, 10))
        
        ctk.CTkLabel(header, text=f"📝 {self.title}",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left", padx=20, pady=15)
        
        self.progress_label = ctk.CTkLabel(header, text=f"Question 1 of {len(self.questions)}",
                                          font=ctk.CTkFont(size=12), text_color="gray")
        self.progress_label.pack(side="right", padx=20, pady=15)

        if self.time_limit_minutes > 0:
            self.timer_label = ctk.CTkLabel(header, text="",
                                            font=ctk.CTkFont(size=14, weight="bold"),
                                            text_color="#ffcc00")
            self.timer_label.pack(side="right", padx=20, pady=15)
        
        # Question area
        self.q_frame = ctk.CTkFrame(self.parent)
        self.q_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        # Navigation
        nav_frame = ctk.CTkFrame(self.parent)
        nav_frame.pack(fill="x", padx=20, pady=(10, 20))
        
        self.prev_btn = ctk.CTkButton(nav_frame, text="← Previous", width=120,
                                      command=self.prev_question, state="disabled")
        self.prev_btn.pack(side="left", padx=10)
        
        self.next_btn = ctk.CTkButton(nav_frame, text="Next →", width=120,
                                      command=self.next_question)
        self.next_btn.pack(side="right", padx=10)
        
        self.submit_btn = ctk.CTkButton(nav_frame, text="Submit Test", width=120,
                                       fg_color="#d32f2f", hover_color="#b71c1c",
                                       command=self.submit_test, state="disabled")
        self.submit_btn.pack(side="right", padx=10)
    
    def show_question(self, q_index):
        """Display a specific question."""
        if q_index < 0 or q_index >= len(self.questions):
            return
            
        self.current_q = q_index
        question = self.questions[q_index]
        
        # Clear question frame
        for widget in self.q_frame.winfo_children():
            widget.destroy()
        
        # Update progress
        self.progress_label.configure(text=f"Question {q_index + 1} of {len(self.questions)}")
        
        # Update navigation buttons
        self.prev_btn.configure(state="normal" if q_index > 0 else "disabled")
        self.next_btn.configure(state="normal" if q_index < len(self.questions) - 1 else "disabled")
        self.submit_btn.configure(state="normal" if q_index == len(self.questions) - 1 else "disabled")
        
        # Question text
        ctk.CTkLabel(self.q_frame, text=f"Q{q_index + 1}: {question['prompt']}",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     wraplength=700, justify="left").pack(padx=30, pady=(30, 20))
        
        # Answer options based on question type
        if question['qtype'] == 'mc':
            self._create_multiple_choice(question)
        elif question['qtype'] == 'tf':
            self._create_true_false(question)
        elif question['qtype'] == 'short':
            self._create_short_answer(question)
    
    def _create_multiple_choice(self, question):
        """Create multiple choice radio buttons."""
        self.answer_var = ctk.StringVar(value=self.answers.get(str(question['id']), ""))
        
        options = [
            ("A", question['opt_a']),
            ("B", question['opt_b']),
            ("C", question['opt_c']),
            ("D", question['opt_d'])
        ]
        
        for label, text in options:
            if text:  # Only show non-empty options
                ctk.CTkRadioButton(self.q_frame, text=f"{label}. {text}",
                                   variable=self.answer_var, value=label).pack(
                                       anchor="w", padx=60, pady=8)
    
    def _create_true_false(self, question):
        """Create true/false radio buttons."""
        self.answer_var = ctk.StringVar(value=self.answers.get(str(question['id']), ""))
        
        ctk.CTkRadioButton(self.q_frame, text="True",
                           variable=self.answer_var, value="True").pack(
                               anchor="w", padx=60, pady=8)
        ctk.CTkRadioButton(self.q_frame, text="False",
                           variable=self.answer_var, value="False").pack(
                               anchor="w", padx=60, pady=8)
    
    def _create_short_answer(self, question):
        """Create short answer text entry."""
        self.answer_var = ctk.StringVar(value=self.answers.get(str(question['id']), ""))
        
        ctk.CTkEntry(self.q_frame, textvariable=self.answer_var,
                     width=400, placeholder_text="Type your answer here...").pack(
                         padx=60, pady=20)
    
    def prev_question(self):
        """Go to previous question."""
        self._save_current_answer()
        self.show_question(self.current_q - 1)
    
    def next_question(self):
        """Go to next question."""
        self._save_current_answer()
        self.show_question(self.current_q + 1)
    
    def _save_current_answer(self):
        """Save the current answer."""
        question_id = str(self.questions[self.current_q]['id'])
        answer = self.answer_var.get().strip()
        if answer:
            self.answers[question_id] = answer
    
    def _tick_timer(self):
        """Update countdown each second; auto-submit when it hits zero."""
        if self.submitted:
            return
        if self.remaining_seconds <= 0:
            if self.timer_label:
                self.timer_label.configure(text="⏰ Time's up!", text_color="#ff4d4d")
            self._auto_submit()
            return
        mins, secs = divmod(self.remaining_seconds, 60)
        if self.timer_label:
            color = "#ff4d4d" if self.remaining_seconds <= 30 else "#ffcc00"
            self.timer_label.configure(text=f"⏱ {mins:02d}:{secs:02d}", text_color=color)
        self.remaining_seconds -= 1
        try:
            self._timer_job = self.parent.after(1000, self._tick_timer)
        except Exception:
            pass

    def _auto_submit(self):
        """Submit without confirmation when time runs out."""
        if self.submitted:
            return
        self._save_current_answer()
        self._do_submit(auto=True)

    def submit_test(self):
        """Submit the completed test."""
        self._save_current_answer()

        if not messagebox.askyesno("Submit Test",
                                   f"Are you sure you want to submit your answers?\n"
                                   f"You've answered {len(self.answers)} out of {len(self.questions)} questions.",
                                   parent=self.parent):
            return
        self._do_submit(auto=False)

    def _do_submit(self, auto: bool = False):
        if self.submitted:
            return
        self.submitted = True
        if self._timer_job is not None:
            try:
                self.parent.after_cancel(self._timer_job)
            except Exception:
                pass

        answer_pairs = [f"{qid}:{ans}" for qid, ans in self.answers.items()]
        cmd = f"SUBMIT_TEST|{self.test_id}|" + "|".join(answer_pairs)

        try:
            resp = self.net.request(cmd)
            parts = resp.split("|")
            if parts[0] == "OK":
                score = float(parts[1]) if len(parts) > 1 else 0
                if score >= 60:
                    sounds.submit_pass()
                else:
                    sounds.submit_fail()
                prefix = "⏰ Time expired — test auto-submitted!\n\n" if auto else ""
                _kiosk_exit(self.parent)
                messagebox.showinfo("Test Submitted",
                                    f"{prefix}Your test has been submitted!\nScore: {score:.1f}%",
                                    parent=self.parent)
                self.parent.destroy()
            else:
                sounds.error()
                messagebox.showerror("Submission Failed",
                                     parts[1] if len(parts) > 1 else "Unknown error",
                                     parent=self.parent)
        except Exception as e:
            sounds.error()
            messagebox.showerror("Error", f"Failed to submit test: {e}", parent=self.parent)


# ─────────────────────────────────────────────
#  Main execution
# ─────────────────────────────────────────────

if __name__ == "__main__":
    app = LoginWindow()
    app.mainloop()
