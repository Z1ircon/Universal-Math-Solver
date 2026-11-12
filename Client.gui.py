"""
Smart Math Solver - CLIENT (Tkinter GUI, styled)
Connects to server.py over a socket and displays results.

Design notes:
- No external image/gif files are used anywhere - the logo and the
  loading spinner are both drawn live on a tk.Canvas with plain lines/
  arcs. This keeps the whole client a single .py file with zero extra
  assets to lose or mis-path.
- Network calls run on a background thread so the window never freezes
  while waiting on the server; the spinner animates via the main
  thread's own event loop (root.after), which is what makes it possible
  to keep it animating smoothly even while a request is in flight.
"""

import socket
import threading
import time
import math
import tkinter as tk
from tkinter import ttk, messagebox

PORT = 65432

# App icon (a blue circle + sigma symbol), generated once and embedded here as
# base64 so the whole client stays a single file with no external asset to lose.
ICON_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAADK0lEQVR4nO2bP2gTURzHv9fahkv0"
    "ElKKtoKWliD4B0FKRwddHBw7OIgddFOrLRTFIjg4KDWiFKOmrXUQCxpjKa12EQURQRzcHAKOIoiF"
    "mOZPrU0d5EJy9+5yd3nv3Z/cd8u7cO/7+b7f+9NrDvDV3BJ4dhY7/XPT6HczM51cvDHtxAxwPbEK"
    "hMlNaYIrRTsIajdjCa0lGmG00DBiBzytfhtK0C5wkqxWg+UKcBI8YN2PpQCcBi/Lii/TATgVXpZZ"
    "f6YCcDq8LDM+DQfgFnhZRv0aCsBt8LKM+K4bgFvhZdXzrxuA2+Fl6XFQOQm6WZoBeGX0ZWnxEAPw"
    "GrwsEtcW1p2+vRnFzo7GZtrl2VWkP5QoOaqVvwYoG7xa/rKUfH4FVH/w+ujLquZkvgi++bKGU0dF"
    "VfvnzDrOJ3L4lSuztqAr5lPg+lwe156s4u9GbXt/rA3pqxHs2818DHTFZQ14+q6EoXgWK4rR7oq2"
    "YO5SGMcHAjxsEFV5jsZj/ndHW5A4J2HvLvWoTy8XcetFHmWOq1BmplPgugt8XynjxI0slj6tqa6d"
    "OSYieUGCFOT6zyr+22DpzyZGkjniaB/e347UeAR9Xa3c/Nh2Dki+LmL4/m9Ve8/2Vjy/EkHvDj4h"
    "2BaA2C7g5BH19ggAz96X8O3HBvEabdmyB4kBAVPDEgb2tKmuTS0XMZHKc/PCPYBgQMD0RQn9MTX8"
    "g6UCbr8scPXDNYCt4n/4Q31q+HuLBdyd5wsPcAxgmyjg0UgYB3vVXU4uFDC5wB8e4BSAFBQwOxrG"
    "gR51d3fmC0gs2gMPcAggHBLweDRMPPPH03k8fFVkbUFXlW2Q1U9QEmclIvxEyl54mZd5BXR3kA80"
    "Y4MhjA2GDN3j49d1DMWzNG1V5D8RstuA3aoJgNdv8+xWNWfTVwBxxL38cFRZ5U1fAX4ApEavLoYk"
    "Ls0K8FoIWjz+FNC76JUq0OOoWwFuD6Gef0NTwK0hGPFteA1wWwhG/ZpaBN0SghmfpncBp4dg1p+l"
    "bdCpIVjxZfkc4LQQrPqhAmHnX4+NDgSVk6Bd1UCjX/+9QZo3U6pp3xzVkhPfHW56/QMiYw4zUGbu"
    "vAAAAABJRU5ErkJggg=="
)

# ---------------------------------------------------------------------
# COLOR PALETTE / TYPOGRAPHY  (change these to re-theme the whole app)
# ---------------------------------------------------------------------
COLOR_HEADER      = "#0f172a"   # dark slate header bar
COLOR_APP_BG      = "#f1f5f9"   # light gray app background
COLOR_CARD        = "#ffffff"   # white card panels
COLOR_BORDER      = "#e2e8f0"
COLOR_ACCENT      = "#2563eb"   # primary blue
COLOR_ACCENT_DARK = "#1d4ed8"
COLOR_TEXT        = "#0f172a"
COLOR_MUTED       = "#64748b"
COLOR_SUCCESS     = "#16a34a"
COLOR_ERROR       = "#dc2626"

FONT_UI   = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_MONO = ("Consolas", 10)


class Spinner(tk.Canvas):
    """A small animated loading indicator - a rotating 'comet tail' of
    fading arcs, drawn frame-by-frame with Canvas (no gif file needed).
    Call .start() / .stop()."""

    def __init__(self, parent, size=22, color=COLOR_ACCENT, bg=COLOR_CARD):
        super().__init__(parent, width=size, height=size, bg=bg, highlightthickness=0)
        self.size = size
        self.color = color
        self.bg_color = bg
        self.angle = 0
        self._running = False

    def start(self):
        self._running = True
        self._tick()

    def stop(self):
        self._running = False
        self.delete("all")

    def _blend(self, hex_a, hex_b, t):
        """Linearly interpolate between two colors - used to fade the
        spinner's trailing arcs toward the background color."""
        ra, ga, ba = self.winfo_rgb(hex_a)
        rb, gb, bb = self.winfo_rgb(hex_b)
        r = int(ra + (rb - ra) * t) >> 8
        g = int(ga + (gb - ga) * t) >> 8
        b = int(ba + (bb - ba) * t) >> 8
        return f"#{r:02x}{g:02x}{b:02x}"

    def _tick(self):
        if not self._running:
            return
        self.delete("all")
        pad = 3
        trail_segments = 5
        for i in range(trail_segments):
            seg_angle = (self.angle - i * 20) % 360
            fade = i / trail_segments
            seg_color = self._blend(self.color, self.bg_color, fade)
            self.create_arc(
                pad, pad, self.size - pad, self.size - pad,
                start=seg_angle, extent=16,
                style="arc", outline=seg_color, width=3
            )
        self.angle = (self.angle + 20) % 360
        self.after(40, self._tick)


def rounded_rect_points(x1, y1, x2, y2, r):
    """Corner points for a rounded rectangle, drawn as a smoothed polygon -
    Tkinter has no native rounded-rect primitive, so this is the standard
    trick: a 12-point polygon with smooth=True rounds the corners."""
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class RoundedButton(tk.Canvas):
    """A flat, rounded-corner button with a real hover/press color change -
    ttk buttons can't do rounded corners, so this draws its own on a Canvas."""

    def __init__(self, parent, text, command=None, width=150, height=38, radius=10,
                 style="filled", font=FONT_BOLD, parent_bg=COLOR_CARD):
        super().__init__(parent, width=width, height=height, bg=parent_bg, highlightthickness=0)
        self.command = command
        self.enabled = True
        self.width_, self.height_, self.radius = width, height, radius

        if style == "filled":
            self.base_color, self.hover_color, self.text_color = COLOR_ACCENT, COLOR_ACCENT_DARK, "white"
            outline = ""
        else:  # "outline" style, used for secondary actions
            self.base_color, self.hover_color, self.text_color = COLOR_CARD, COLOR_APP_BG, COLOR_ACCENT
            outline = COLOR_ACCENT

        pts = rounded_rect_points(1, 1, width - 1, height - 1, radius)
        self.shape = self.create_polygon(pts, smooth=True, fill=self.base_color,
                                          outline=outline, width=1.4)
        self.label = self.create_text(width / 2, height / 2, text=text,
                                       fill=self.text_color, font=font)

        for tag in (self.shape, self.label):
            self.tag_bind(tag, "<Enter>", self._on_enter)
            self.tag_bind(tag, "<Leave>", self._on_leave)
            self.tag_bind(tag, "<ButtonRelease-1>", self._on_click)
        self.config(cursor="hand2")

    def _on_enter(self, _e):
        if self.enabled:
            self.itemconfig(self.shape, fill=self.hover_color)

    def _on_leave(self, _e):
        if self.enabled:
            self.itemconfig(self.shape, fill=self.base_color)

    def _on_click(self, _e):
        if self.enabled and self.command:
            self.command()

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self.itemconfig(self.shape, fill=self.base_color if enabled else "#cbd5e1")
        self.itemconfig(self.label, fill=self.text_color if enabled else "#94a3b8")
        self.config(cursor="hand2" if enabled else "arrow")


def card(parent, fill=tk.X, expand=False, padx=0, pady=(0, 0)):
    """A white 'card' panel with rounded-feel border + a soft drop shadow
    (a slightly larger gray frame peeking out on the bottom-right edge)."""
    shadow = tk.Frame(parent, bg="#cbd5e1")
    shadow.pack(fill=fill, expand=expand, padx=padx, pady=pady)
    inner = tk.Frame(shadow, bg=COLOR_CARD, highlightbackground=COLOR_BORDER, highlightthickness=1)
    inner.pack(fill=fill, expand=expand, padx=(0, 3), pady=(0, 3))
    return inner


class MathClientGUI:
    HINTS = {
        "solve": "e.g.  x**2 - 4",
        "diff": "e.g.  x**3 + 2*x",
        "integrate": "e.g.  2*x",
        "simplify": "e.g.  (x**2 - 1)/(x - 1)",
        "factor": "e.g.  x**2 - 4",
        "expand": "e.g.  (x+2)*(x-2)",
        "apart": "e.g.  1/(x**2 - 1)",
        "trigsimp": "e.g.  sin(x)**2 + cos(x)**2",
        "limit": "format: expr@point   e.g.  sin(x)/x@0",
        "series": "format: expr@point,order   e.g.  exp(x)@0,4",
        "dsolve": "e.g.  Derivative(f(x), x) - f(x)",
        "gcd": "format: expr1,expr2   e.g.  x**2-1,x-1",
        "lcm": "format: expr1,expr2   e.g.  x**2-1,x-1",
        "nsolve": "format: expr,guess   e.g.  x**2 - 2,1",
        "det": "matrix literal   e.g.  [[1,2],[3,4]]",
        "inverse": "matrix literal   e.g.  [[1,2],[3,4]]",
        "mean": "format: mu,sigma   e.g.  0,1",
        "variance": "format: mu,sigma   e.g.  0,1",
        "probability": "format: mu,sigma,threshold   e.g.  0,1,1",
        "plot": "format: expr@xmin,xmax   e.g.  sin(x)@-10,10  (opens a graph window)",
    }
    OPERATIONS = [
        "solve", "diff", "integrate", "simplify", "factor", "expand",
        "apart", "trigsimp", "limit", "series", "dsolve",
        "gcd", "lcm", "nsolve", "det", "inverse",
        "mean", "variance", "probability", "plot",
    ]
    EXAMPLES = {
        "solve": "x**2 - 4", "diff": "x**3 + 2*x", "integrate": "2*x",
        "simplify": "(x**2 - 1)/(x - 1)", "factor": "x**2 - 4", "expand": "(x+2)*(x-2)",
        "apart": "1/(x**2 - 1)", "trigsimp": "sin(x)**2 + cos(x)**2",
        "limit": "sin(x)/x@0", "series": "exp(x)@0,4",
        "dsolve": "Derivative(f(x), x) - f(x)", "gcd": "x**2-1,x-1", "lcm": "x**2-1,x-1",
        "nsolve": "x**2 - 2,1", "det": "[[1,2],[3,4]]", "inverse": "[[1,2],[3,4]]",
        "mean": "0,1", "variance": "0,1", "probability": "0,1,1",
        "plot": "sin(x)@-10,10",
    }

    def __init__(self, root):
        self.root = root
        self.sock = None
        root.title("Smart Math Solver")
        root.geometry("560x680")
        root.configure(bg=COLOR_APP_BG)
        root.minsize(500, 560)

        self._icon_img = tk.PhotoImage(data=ICON_B64)  # keep a reference - Tk drops GC'd images
        root.iconphoto(True, self._icon_img)

        self._build_style()
        self._build_header()
        self._build_connection_card()
        self._build_request_card()
        self._build_output_card()

        self.connect()

    # ---------------- style setup ----------------

    def _build_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass  # fall back to whatever default theme exists on this platform

        style.configure("TCombobox", fieldbackground="white", padding=4)

        style.configure("Accent.TButton", background=COLOR_ACCENT, foreground="white",
                         font=FONT_BOLD, padding=(14, 8), borderwidth=0)
        style.map("Accent.TButton", background=[("active", COLOR_ACCENT_DARK)])

        style.configure("Secondary.TButton", background=COLOR_CARD, foreground=COLOR_ACCENT,
                         font=FONT_UI, padding=(14, 8), borderwidth=1)
        style.map("Secondary.TButton", background=[("active", COLOR_APP_BG)])

    # ---------------- header ----------------

    def _build_header(self):
        header = tk.Frame(self.root, bg=COLOR_HEADER, height=72)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)

        # Logo: a hand-drawn sine wave + sigma symbol, no image file needed
        logo = tk.Canvas(header, width=54, height=54, bg=COLOR_HEADER, highlightthickness=0)
        logo.pack(side=tk.LEFT, padx=(16, 8), pady=9)
        pts = []
        for i in range(0, 55, 3):
            yv = 27 + 14 * math.sin(i / 54 * 2 * math.pi)
            pts.extend([i, yv])
        logo.create_line(*pts, fill=COLOR_ACCENT, width=3, smooth=True)
        logo.create_text(27, 12, text="\u03a3", fill="white", font=("Segoe UI", 13, "bold"))

        title_box = tk.Frame(header, bg=COLOR_HEADER)
        title_box.pack(side=tk.LEFT, pady=12)
        tk.Label(title_box, text="Smart Math Solver", fg="white", bg=COLOR_HEADER,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w")
        tk.Label(title_box, text="Distributed Symbolic Computation Client", fg="#94a3b8",
                 bg=COLOR_HEADER, font=("Segoe UI", 9)).pack(anchor="w")

    # ---------------- connection card ----------------

    def _build_connection_card(self):
        outer = tk.Frame(self.root, bg=COLOR_APP_BG)
        outer.pack(fill=tk.X, padx=16, pady=(14, 8))

        c = card(outer)
        c.pack(fill=tk.X, ipady=10, ipadx=12)

        row = tk.Frame(c, bg=COLOR_CARD)
        row.pack(fill=tk.X, padx=10, pady=(8, 4))

        tk.Label(row, text="Server IP", bg=COLOR_CARD, fg=COLOR_MUTED, font=FONT_UI).grid(row=0, column=0, sticky="w")
        tk.Label(row, text="Port", bg=COLOR_CARD, fg=COLOR_MUTED, font=FONT_UI).grid(row=0, column=1, sticky="w", padx=(10, 0))

        self.ip_entry = tk.Entry(row, font=FONT_UI, relief="solid", bd=1, width=18)
        self.ip_entry.insert(0, "127.0.0.1")
        self.ip_entry.grid(row=1, column=0, sticky="we", pady=(2, 0))

        self.port_entry = tk.Entry(row, font=FONT_UI, relief="solid", bd=1, width=7)
        self.port_entry.insert(0, str(PORT))
        self.port_entry.grid(row=1, column=1, sticky="we", padx=(10, 0), pady=(2, 0))

        RoundedButton(row, "Connect", command=self.connect, width=110, height=32,
                      parent_bg=COLOR_CARD).grid(row=1, column=2, padx=(10, 0), pady=(2, 0))

        status_row = tk.Frame(c, bg=COLOR_CARD)
        status_row.pack(fill=tk.X, padx=10, pady=(4, 8))
        self.status_canvas = tk.Canvas(status_row, width=10, height=10, bg=COLOR_CARD, highlightthickness=0)
        self.status_dot = self.status_canvas.create_oval(1, 1, 9, 9, fill=COLOR_ERROR, outline="")
        self.status_canvas.pack(side=tk.LEFT)
        self.status_label = tk.Label(status_row, text="Not connected", fg=COLOR_ERROR,
                                      bg=COLOR_CARD, font=FONT_UI)
        self.status_label.pack(side=tk.LEFT, padx=(6, 0))

    # ---------------- request card ----------------

    def _build_request_card(self):
        outer = tk.Frame(self.root, bg=COLOR_APP_BG)
        outer.pack(fill=tk.X, padx=16, pady=8)

        c = card(outer)
        c.pack(fill=tk.X, ipady=10, ipadx=12)

        tk.Label(c, text="Operation", bg=COLOR_CARD, fg=COLOR_MUTED, font=FONT_UI)\
            .pack(anchor="w", padx=10, pady=(8, 0))

        self.op_var = tk.StringVar(value="solve")
        op_box = ttk.Combobox(c, textvariable=self.op_var, values=self.OPERATIONS,
                               state="readonly", font=FONT_UI)
        op_box.pack(fill=tk.X, padx=10, pady=(2, 0))
        self.op_var.trace_add("write", self.update_hint)

        self.hint_label = tk.Label(c, text="", fg=COLOR_MUTED, bg=COLOR_CARD,
                                    font=("Segoe UI", 8, "italic"), wraplength=480, justify="left")
        self.hint_label.pack(anchor="w", padx=10, pady=(2, 6))
        self.update_hint()

        tk.Label(c, text="Expression(s) - separate multiple with ';'", bg=COLOR_CARD,
                 fg=COLOR_MUTED, font=FONT_UI).pack(anchor="w", padx=10)
        self.expr_entry = tk.Entry(c, font=FONT_MONO, relief="solid", bd=1)
        self.expr_entry.insert(0, "x**2 - 4")
        self.expr_entry.pack(fill=tk.X, padx=10, pady=(2, 10))
        self.expr_entry.bind("<Return>", lambda e: self.send_request())

        btn_row = tk.Frame(c, bg=COLOR_CARD)
        btn_row.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.send_btn = RoundedButton(btn_row, "Send to Server", command=self.send_request,
                                       width=150, height=38, parent_bg=COLOR_CARD)
        self.send_btn.pack(side=tk.LEFT)
        RoundedButton(btn_row, "View History", command=self.view_history, style="outline",
                      width=130, height=38, parent_bg=COLOR_CARD).pack(side=tk.LEFT, padx=(8, 0))

        self.spinner = Spinner(btn_row)
        # not packed yet - shown only while a request is in flight

    # ---------------- output card ----------------

    def _build_output_card(self):
        outer = tk.Frame(self.root, bg=COLOR_APP_BG)
        outer.pack(fill=tk.BOTH, expand=True, padx=16, pady=(8, 16))

        c = card(outer)
        c.pack(fill=tk.BOTH, expand=True)

        tk.Label(c, text="Results", bg=COLOR_CARD, fg=COLOR_TEXT, font=FONT_BOLD)\
            .pack(anchor="w", padx=10, pady=(8, 4))

        text_frame = tk.Frame(c, bg=COLOR_CARD)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.output = tk.Text(text_frame, wrap="word", relief="flat",
                               bg="#f8fafc", fg=COLOR_TEXT, font=FONT_MONO,
                               yscrollcommand=scrollbar.set, padx=8, pady=8)
        self.output.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.output.yview)

        self.output.tag_configure("timestamp", foreground=COLOR_MUTED, font=("Segoe UI", 8))
        self.output.tag_configure("request", foreground=COLOR_ACCENT_DARK, font=FONT_BOLD)
        self.output.tag_configure("response", foreground=COLOR_TEXT, font=FONT_MONO)
        self.output.tag_configure("error", foreground=COLOR_ERROR, font=FONT_UI)
        self.output.tag_configure("divider", foreground=COLOR_BORDER)

    # ---------------- hint / status helpers ----------------

    def update_hint(self, *_):
        op = self.op_var.get()
        self.hint_label.config(text=self.HINTS.get(op, ""))
        # Swap the expression box to a matching example so a leftover value
        # from a different operation can't silently cause a format error.
        if hasattr(self, "expr_entry"):
            self.expr_entry.delete(0, tk.END)
            self.expr_entry.insert(0, self.EXAMPLES.get(op, ""))

    def _set_status(self, text, color):
        self.status_label.config(text=text, fg=color)
        self.status_canvas.itemconfig(self.status_dot, fill=color)

    def _log(self, request_line, body, is_error=False):
        ts = time.strftime("%H:%M:%S")
        divider = "\u2500" * 56
        start = self.output.index(tk.END)
        self.output.insert(tk.END, f"[{ts}]  ", "timestamp")
        self.output.insert(tk.END, f"{request_line}\n", "request")
        self.output.insert(tk.END, f"{body}\n", "error" if is_error else "response")
        self.output.insert(tk.END, f"{divider}\n", "divider")
        self.output.see(tk.END)

        # Briefly flash the new entry's background so the eye catches that
        # something just arrived, then fade it back to normal.
        flash_tag = f"flash_{time.time_ns()}"
        self.output.tag_add(flash_tag, start, self.output.index(tk.END))
        self.output.tag_configure(flash_tag, background="#dbeafe")
        self.output.tag_lower(flash_tag)  # keep text color tags (request/response) visible on top
        self.root.after(600, lambda: self.output.tag_configure(flash_tag, background=""))

    # ---------------- connection ----------------

    def connect(self):
        server_ip = self.ip_entry.get().strip() or "127.0.0.1"
        try:
            server_port = int(self.port_entry.get().strip())
        except ValueError:
            server_port = PORT
        try:
            if self.sock:
                self.sock.close()
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.settimeout(5)
            self.sock.connect((server_ip, server_port))
            self.sock.settimeout(None)
            self._set_status(f"Connected to {server_ip}:{server_port}", COLOR_SUCCESS)
        except Exception as e:
            self._set_status("Not connected", COLOR_ERROR)
            messagebox.showerror("Connection Error", f"Could not reach {server_ip}:{server_port}\n{e}")

    # ---------------- protocol ----------------

    def _send_message(self, text: str):
        payload = text.encode('utf-8')
        self.sock.sendall(len(payload).to_bytes(4, 'big') + payload)

    def _recv_message(self) -> str:
        header = self.sock.recv(4)
        if not header:
            return ""
        length = int.from_bytes(header, 'big')
        chunks, remaining = [], length
        while remaining > 0:
            chunk = self.sock.recv(min(4096, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks).decode('utf-8')

    # ---------------- requests (threaded, so the UI never freezes) ----------------

    def send_request(self):
        op = self.op_var.get()
        exprs = self.expr_entry.get().strip()
        if not exprs:
            return
        message = f"{op}|{exprs}"

        self.send_btn.set_enabled(False)
        self.spinner.pack(side=tk.LEFT, padx=(10, 0))
        self.spinner.start()

        threading.Thread(target=self._request_worker, args=(message, exprs), daemon=True).start()

    def _request_worker(self, message, exprs):
        """Runs on a background thread - does ONLY networking, touches
        no Tkinter widgets directly (that must happen back on the main
        thread, via root.after)."""
        try:
            self._send_message(message)
            response = self._recv_message()
            self.root.after(0, self._on_response, message, exprs, response, None)
        except Exception as e:
            self.root.after(0, self._on_response, message, exprs, None, e)

    def _on_response(self, message, exprs, response, error):
        self.spinner.stop()
        self.spinner.pack_forget()
        self.send_btn.set_enabled(True)

        if error is not None:
            self._log(message, str(error), is_error=True)
            messagebox.showerror("Error", str(error))
            self.connect()
            return

        if response.strip().startswith("IMAGE:"):
            self.show_plot_window(exprs, response.strip()[len("IMAGE:"):])
            self._log(message, "Graph opened in a new window.")
        else:
            self._log(message, response)

    def show_plot_window(self, title_text, base64_png):
        """Opens a new window showing the graph the server generated.
        tk.PhotoImage decodes PNG data natively (Tk 8.6+), no Pillow needed."""
        win = tk.Toplevel(self.root)
        win.title(f"Graph: {title_text}")
        win.configure(bg=COLOR_CARD)
        photo = tk.PhotoImage(data=base64_png)
        win.photo_ref = photo  # keep a reference so it isn't garbage-collected
        tk.Label(win, image=photo, bg=COLOR_CARD).pack(padx=8, pady=8)

    def view_history(self):
        self.send_btn.set_enabled(False)
        self.spinner.pack(side=tk.LEFT, padx=(10, 0))
        self.spinner.start()
        threading.Thread(target=self._history_worker, daemon=True).start()

    def _history_worker(self):
        try:
            self._send_message("history|20")
            response = self._recv_message()
            self.root.after(0, self._on_history, response, None)
        except Exception as e:
            self.root.after(0, self._on_history, None, e)

    def _on_history(self, response, error):
        self.spinner.stop()
        self.spinner.pack_forget()
        self.send_btn.set_enabled(True)
        if error is not None:
            messagebox.showerror("Error", str(error))
            self.connect()
            return
        self._log("Recent History", response)

    def on_close(self):
        try:
            self._send_message('exit')
            self.sock.close()
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = MathClientGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()