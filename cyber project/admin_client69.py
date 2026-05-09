"""
admin_client69.py – Admin GUI
==============================
CustomTkinter-based interface for the admin user:
  • Login (admin role only)
  • Create / view classes
  • View all users and their class assignments
  • Assign / remove users from classes
"""

import customtkinter as ctk
from tkinter import messagebox, simpledialog
from network import NetworkClient
import sounds

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def parse_classes(response: str):
    """CLASSES|id~name|..."""
    parts = response.split("|")
    if parts[0] != "CLASSES" or len(parts) < 2 or parts[1] == "":
        return []
    return [{"id": int(f.split("~")[0]), "name": f.split("~")[1]}
            for f in parts[1:]]


def parse_users(response: str):
    """USERS|id~username~role~class_name|..."""
    parts = response.split("|")
    if parts[0] != "USERS" or len(parts) < 2 or parts[1] == "":
        return []
    users = []
    for item in parts[1:]:
        f = item.split("~")
        users.append({
            "id":         int(f[0]),
            "username":   f[1],
            "role":       f[2],
            "class_name": f[3] if len(f) > 3 else "",
        })
    return users


def parse_members(response: str):
    """MEMBERS|id~username~role|..."""
    parts = response.split("|")
    if parts[0] != "MEMBERS" or len(parts) < 2 or parts[1] == "":
        return []
    members = []
    for item in parts[1:]:
        f = item.split("~")
        members.append({"id": int(f[0]), "username": f[1], "role": f[2]})
    return members


# ─────────────────────────────────────────────
#  Account Recovery
# ─────────────────────────────────────────────

class ForgotPasswordDialog(ctk.CTkToplevel):
    def __init__(self, parent, net):
        super().__init__(parent)
        self.title("Account Recovery")
        self.geometry("350x260")
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
#  Login window
# ─────────────────────────────────────────────

class LoginWindow(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Quizy – Admin Login")
        self.geometry("420x380")
        self.resizable(False, False)
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
        ctk.CTkLabel(self, text="Quizy",
                     font=ctk.CTkFont(size=28, weight="bold"),
                     text_color="#c084fc").pack(pady=(30, 4))
        ctk.CTkLabel(self, text="Admin Panel",
                     font=ctk.CTkFont(size=14),
                     text_color="gray").pack(pady=(0, 24))

        frame = ctk.CTkFrame(self)
        frame.pack(padx=40, fill="x")

        ctk.CTkLabel(frame, text="Username").pack(anchor="w", padx=16, pady=(16, 0))
        self.username_entry = ctk.CTkEntry(frame, width=300, placeholder_text="admin")
        self.username_entry.pack(padx=16, pady=(4, 12))

        ctk.CTkLabel(frame, text="Password").pack(anchor="w", padx=16)
        self.password_entry = ctk.CTkEntry(frame, width=300, show="•",
                                            placeholder_text="••••••••")
        self.password_entry.pack(padx=16, pady=(4, 20))
        self.password_entry.bind("<Return>", lambda _: self.do_login())

        ctk.CTkButton(frame, text="Login as Admin", width=300,
                      fg_color="#7c3aed", hover_color="#5b21b6",
                      command=self.do_login).pack(padx=16, pady=(0, 16))

        ctk.CTkButton(frame, text="Forgot Password / Username?", fg_color="transparent", 
                      text_color="gray", hover_color="#333333", height=20,
                      command=lambda: ForgotPasswordDialog(self, self.net)).pack(pady=(0, 10))

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
            if role != "admin":
                sounds.error()
                messagebox.showerror("Access Denied",
                                     "This portal is for admins only.")
                return
            sounds.login()
            self.withdraw()
            app = AdminApp(self.net, u)
            app.mainloop()
            self.destroy()
        else:
            sounds.error()
            messagebox.showerror("Login Failed",
                                 parts[1] if len(parts) > 1 else "Unknown error")


# ─────────────────────────────────────────────
#  Main Admin application
# ─────────────────────────────────────────────

class AdminApp(ctk.CTkToplevel):
    def __init__(self, net: NetworkClient, username: str):
        super().__init__()
        self.net = net
        self.username = username
        self.title(f"Quizy Admin – {username}")
        self.geometry("1000x640")
        self.minsize(800, 520)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.selected_class_id = None
        self.selected_class_name = ""
        self.classes = []
        self.users = []

        self._build_ui()
        self.refresh_all()

    def _on_close(self):
        self.net.close()
        self.quit()   # exits the mainloop() called in LoginWindow.do_login
        self.destroy()

    def _build_ui(self):
        # ── Sidebar ──────────────────────────────
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        ctk.CTkLabel(self.sidebar, text="Quizy",
                     font=ctk.CTkFont(size=20, weight="bold"),
                     text_color="#c084fc").pack(pady=(24, 4))
        ctk.CTkLabel(self.sidebar, text="🛡 Admin Panel",
                     font=ctk.CTkFont(size=12),
                     text_color="gray").pack()
        ctk.CTkLabel(self.sidebar, text=f"👤 {self.username}",
                     font=ctk.CTkFont(size=11), text_color="gray").pack(pady=(4, 20))

        ctk.CTkButton(self.sidebar, text="➕  New Class",
                      fg_color="#6d28d9", hover_color="#4c1d95",
                      command=self.create_class).pack(padx=16, pady=6, fill="x")
        ctk.CTkButton(self.sidebar, text="👁  User Details",
                      fg_color="#7c3aed", hover_color="#5b21b6",
                      command=self.view_user_details).pack(padx=16, pady=6, fill="x")
        ctk.CTkButton(self.sidebar, text="🔄  Refresh All",
                      fg_color="gray30", hover_color="gray20",
                      command=self.refresh_all).pack(padx=16, pady=6, fill="x")

        # ── Main area ────────────────────────────
        main = ctk.CTkFrame(self)
        main.pack(side="left", fill="both", expand=True, padx=0, pady=0)

        # Split: left = classes panel, right = users panel
        pane = ctk.CTkFrame(main, fg_color="transparent")
        pane.pack(fill="both", expand=True, padx=12, pady=12)

        # Classes panel
        classes_panel = ctk.CTkFrame(pane, width=320)
        classes_panel.pack(side="left", fill="both", expand=False, padx=(0, 8))
        classes_panel.pack_propagate(False)

        ctk.CTkLabel(classes_panel, text="Classes",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(
                         anchor="w", padx=12, pady=(12, 8))

        self.classes_scroll = ctk.CTkScrollableFrame(classes_panel)
        self.classes_scroll.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # Members sub-panel (bottom of classes panel)
        self.members_panel = ctk.CTkFrame(classes_panel)
        self.members_panel.pack(fill="x", padx=4, pady=4)
        self.members_title = ctk.CTkLabel(
            self.members_panel, text="Select a class to see members",
            font=ctk.CTkFont(size=12), text_color="gray")
        self.members_title.pack(anchor="w", padx=8, pady=6)
        self.members_scroll = ctk.CTkScrollableFrame(self.members_panel, height=160)
        self.members_scroll.pack(fill="x", padx=4, pady=(0, 6))

        # Users panel
        users_panel = ctk.CTkFrame(pane)
        users_panel.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(users_panel, text="All Users",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(
                         anchor="w", padx=12, pady=(12, 8))

        self.users_scroll = ctk.CTkScrollableFrame(users_panel)
        self.users_scroll.pack(fill="both", expand=True, padx=4, pady=(0, 4))

    # ── Data refresh ─────────────────────────────

    def refresh_all(self):
        self._load_classes()
        self._load_users()
        if self.selected_class_id:
            self._load_members(self.selected_class_id)

    def _load_classes(self):
        resp = self.net.request("LIST_CLASSES")
        self.classes = parse_classes(resp)
        self._render_classes()

    def _load_users(self):
        resp = self.net.request("LIST_USERS")
        self.users = parse_users(resp)
        self._render_users()

    def _load_members(self, class_id: int):
        resp = self.net.request(f"LIST_CLASS_MEMBERS|{class_id}")
        members = parse_members(resp)
        self._render_members(members)

    # ── Renderers ────────────────────────────────

    def _render_classes(self):
        for w in self.classes_scroll.winfo_children():
            w.destroy()

        if not self.classes:
            ctk.CTkLabel(self.classes_scroll,
                         text="No classes yet. Create one →",
                         text_color="gray").pack(pady=20)
            return

        for cls in self.classes:
            self._make_class_card(cls)

    def _make_class_card(self, cls: dict):
        is_selected = (cls["id"] == self.selected_class_id)
        card = ctk.CTkFrame(
            self.classes_scroll,
            fg_color=("#1a3a5c" if is_selected else ("gray86", "gray17")),
            cursor="hand2"
        )
        card.pack(fill="x", padx=4, pady=3)

        lbl = ctk.CTkLabel(card,
                            text=f"🏫  {cls['name']}",
                            font=ctk.CTkFont(size=13, weight="bold"),
                            anchor="w")
        lbl.pack(side="left", padx=12, pady=10, expand=True, fill="x")

        def on_click(event=None, cid=cls["id"], cname=cls["name"]):
            self.selected_class_id = cid
            self.selected_class_name = cname
            self._render_classes()  # re-render to highlight
            self._load_members(cid)

        card.bind("<Button-1>", on_click)
        lbl.bind("<Button-1>", on_click)

    def _render_members(self, members: list):
        for w in self.members_scroll.winfo_children():
            w.destroy()

        class_label = f"Members of '{self.selected_class_name}'"
        self.members_title.configure(text=class_label, text_color="white")

        if not members:
            ctk.CTkLabel(self.members_scroll,
                         text="No members yet.",
                         text_color="gray").pack(pady=10)
            return

        for m in members:
            row = ctk.CTkFrame(self.members_scroll)
            row.pack(fill="x", pady=2)
            icon = "👨‍🏫" if m["role"] == "teacher" else "🎓"
            ctk.CTkLabel(row,
                         text=f"{icon}  {m['username']}  ({m['role']})",
                         anchor="w").pack(side="left", padx=8, pady=4, expand=True, fill="x")
            ctk.CTkButton(row, text="✖", width=32,
                          fg_color="#8B0000", hover_color="#600000",
                          command=lambda uid=m["id"]: self._remove_user(uid)
                          ).pack(side="right", padx=6, pady=4)

    def _render_users(self):
        for w in self.users_scroll.winfo_children():
            w.destroy()

        if not self.users:
            ctk.CTkLabel(self.users_scroll,
                         text="No users registered yet.",
                         text_color="gray").pack(pady=20)
            return

        # Header row
        hdr = ctk.CTkFrame(self.users_scroll, fg_color="gray25")
        hdr.pack(fill="x", pady=(0, 4))
        for col, w in [("Username", 120), ("Role", 70), ("Class", 110), ("Action", 100)]:
            ctk.CTkLabel(hdr, text=col, width=w,
                         font=ctk.CTkFont(weight="bold"),
                         anchor="w").pack(side="left", padx=4, pady=6)

        for user in self.users:
            self._make_user_row(user)

    def _make_user_row(self, user: dict):
        row = ctk.CTkFrame(self.users_scroll)
        row.pack(fill="x", pady=2)

        icon = "👨‍🏫" if user["role"] == "teacher" else "🎓"
        ctk.CTkLabel(row,
                     text=f"{icon}  {user['username']}",
                     width=120, anchor="w").pack(side="left", padx=4, pady=8)
        ctk.CTkLabel(row,
                     text=user["role"].capitalize(),
                     width=70, anchor="w",
                     text_color="#a0d0ff" if user["role"] == "teacher" else "#7adba8"
                     ).pack(side="left", padx=4)

        class_text = user["class_name"] if user["class_name"] else "— (none)"
        ctk.CTkLabel(row,
                     text=class_text,
                     width=110, anchor="w",
                     text_color="gray" if not user["class_name"] else "white"
                     ).pack(side="left", padx=4)

        ctk.CTkButton(row, text="Assign", width=100,
                      fg_color="#2d6a4f", hover_color="#1b4332",
                      command=lambda uid=user["id"], uname=user["username"]:
                          self._assign_user_dialog(uid, uname)
                      ).pack(side="left", padx=4)

    # ── Actions ──────────────────────────────────

    def view_user_details(self):
        UserDetailsWindow(self, self.net)

    def create_class(self):
        name = simpledialog.askstring("New Class",
                                      "Enter class name:",
                                      parent=self)
        if not name or not name.strip():
            return
        resp = self.net.request(f"CREATE_CLASS|{name.strip()}")
        parts = resp.split("|")
        if parts[0] == "OK":
            sounds.success()
            messagebox.showinfo("Created", f"Class '{name}' created!")
            self.refresh_all()
        else:
            sounds.error()
            messagebox.showerror("Error", parts[1] if len(parts) > 1 else resp)

    def _assign_user_dialog(self, user_id: int, username: str):
        if not self.classes:
            messagebox.showwarning("No Classes",
                                   "Create at least one class first.")
            return

        win = ctk.CTkToplevel(self)
        win.title(f"Assign {username} to class")
        win.geometry("360x300")
        win.grab_set()

        ctk.CTkLabel(win, text=f"Assign '{username}' to:",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(20, 12))

        scroll = ctk.CTkScrollableFrame(win, height=180)
        scroll.pack(fill="x", padx=20)

        def do_assign(class_id, class_name):
            resp = self.net.request(f"ASSIGN_USER|{user_id}|{class_id}")
            if resp.startswith("OK"):
                sounds.success()
                messagebox.showinfo("Assigned",
                                    f"'{username}' assigned to '{class_name}'.")
                win.destroy()
                self.refresh_all()
                if self.selected_class_id:
                    self._load_members(self.selected_class_id)
            else:
                sounds.error()
                parts = resp.split("|")
                messagebox.showerror("Error",
                                     parts[1] if len(parts) > 1 else resp)

        for cls in self.classes:
            ctk.CTkButton(
                scroll,
                text=f"🏫  {cls['name']}",
                font=ctk.CTkFont(size=13),
                fg_color="#6d28d9", hover_color="#4c1d95",
                command=lambda cid=cls["id"], cname=cls["name"]:
                    do_assign(cid, cname)
            ).pack(fill="x", pady=4, padx=4)

        ctk.CTkButton(win, text="Cancel", fg_color="gray30",
                      hover_color="gray20",
                      command=win.destroy).pack(pady=12)

    def _remove_user(self, user_id: int):
        if not self.selected_class_id:
            return
        confirm = messagebox.askyesno("Remove Member",
                                      "Remove this user from the class?")
        if not confirm:
            return
        resp = self.net.request(
            f"REMOVE_USER|{user_id}|{self.selected_class_id}")
        if resp.startswith("OK"):
            sounds.delete()
            self._load_members(self.selected_class_id)
            self._load_users()
        else:
            sounds.error()
            parts = resp.split("|")
            messagebox.showerror("Error",
                                 parts[1] if len(parts) > 1 else resp)


# ─────────────────────────────────────────────
#  User Details window
# ─────────────────────────────────────────────

class UserDetailsWindow(ctk.CTkToplevel):
    def __init__(self, parent, net):
        super().__init__(parent)
        self.net = net
        self.title("User Details")
        self.geometry("900x560")
        self.grab_set()
        self._build_ui()
        self._load()

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, height=50, fg_color="#7c3aed", corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="👁  All User Details",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="white").pack(side="left", padx=20, expand=True)
        ctk.CTkButton(hdr, text="🔄 Refresh", width=90, height=32,
                      fg_color="#5b21b6", hover_color="#4c1d95",
                      command=self._load).pack(side="right", padx=12, pady=8)

        # Column headers
        col_frame = ctk.CTkFrame(self, fg_color="gray20", corner_radius=0)
        col_frame.pack(fill="x", padx=0)
        for col, w in [("ID", 40), ("Username", 140), ("Email", 220),
                       ("Role", 90), ("Class", 150)]:
            ctk.CTkLabel(col_frame, text=col, width=w,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         anchor="w").pack(side="left", padx=6, pady=6)

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="#0D1117")
        self.scroll.pack(fill="both", expand=True, padx=0, pady=0)

    def _load(self):
        for w in self.scroll.winfo_children():
            w.destroy()

        resp = self.net.request("GET_USER_DETAILS")
        parts = resp.split("|")

        if parts[0] != "USER_DETAILS" or len(parts) < 2 or parts[1] == "":
            ctk.CTkLabel(self.scroll, text="No users found.",
                         text_color="gray").pack(pady=20)
            return

        for entry in parts[1:]:
            fields = entry.split("~")
            if len(fields) < 5:
                continue
            uid, username, email, role, class_name = fields[:5]

            row = ctk.CTkFrame(self.scroll, fg_color="#161B22", corner_radius=6)
            row.pack(fill="x", pady=2, padx=4)

            role_color = "#a0d0ff" if role == "teacher" else ("#c084fc" if role == "admin" else "#7adba8")
            icon = "👨‍🏫" if role == "teacher" else ("🛡" if role == "admin" else "🎓")

            ctk.CTkLabel(row, text=uid, width=40, anchor="w",
                         text_color="gray").pack(side="left", padx=6, pady=8)
            ctk.CTkLabel(row, text=f"{icon} {username}", width=140,
                         anchor="w", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=4, pady=8)
            ctk.CTkLabel(row, text=email, width=220, anchor="w",
                         text_color="#60a5fa").pack(side="left", padx=4, pady=8)
            ctk.CTkLabel(row, text=role.capitalize(), width=90, anchor="w",
                         text_color=role_color).pack(side="left", padx=4, pady=8)
            ctk.CTkLabel(row, text=class_name if class_name else "—", width=150,
                         anchor="w", text_color="gray" if not class_name else "white"
                         ).pack(side="left", padx=4, pady=8)


# ─────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    app = LoginWindow()
    app.mainloop()
