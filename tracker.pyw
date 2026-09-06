"""
Study Tracker
-------------
Automatically opens a PDF in Microsoft Edge on a recurring schedule,
runs from the system tray, and remembers your settings between launches.

Requirements:
    pip install pystray pillow

Run as a .pyw file (double-click, or `pythonw tracker.pyw`) so no
console window appears.

Build as a standalone .exe:
    python -m PyInstaller --onefile --windowed --icon=icon.ico --add-data "icon.ico;." --name "StudyTracker" tracker.pyw
"""

import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess
import os
import sys
import json
import threading
from datetime import datetime, timedelta

try:
    import pystray
    from pystray import MenuItem as Item
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(get_app_dir(), "tracker_config.json")

EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def resource_path(relative_path):
    """Resolve a bundled resource path, both in dev and inside a PyInstaller exe."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


class StudyTracker:
    def __init__(self, root):
        self.root = root
        self.root.title("Study Tracker")
        self.root.geometry("460x430")
        self.root.resizable(False, False)

        try:
            self.root.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass

        self.pdf_path = ""
        self.running = False
        self.paused = False
        self.next_time = None
        self.interval = None
        self.timer_job = None
        self.countdown_job = None
        self.edge_process = None
        self.tray_icon = None

        self.first_mode = tk.StringVar(value="perfect_hour")
        self.custom_time_var = tk.StringVar(value="")

        self._build_ui()
        self._load_config()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if TRAY_AVAILABLE:
            self._start_tray()
        else:
            self.status.config(
                text="Tray icon unavailable (pip install pystray pillow)",
                fg="#b36b00",
            )

    def _build_ui(self):
        root = self.root

        tk.Label(root, text="📚 Study Tracker", font=("Segoe UI", 22, "bold")).pack(pady=15)

        tk.Button(root, text="Select Tracker PDF", command=self.select_pdf, width=22).pack(pady=5)

        self.file_label = tk.Label(root, text="No PDF selected", fg="gray", wraplength=400)
        self.file_label.pack(pady=5)

        tk.Label(root, text="Open tracker every:", font=("Segoe UI", 12)).pack(pady=(15, 5))

        interval_frame = tk.Frame(root)
        interval_frame.pack()
        self.interval_entry = tk.Spinbox(interval_frame, from_=1, to=24, width=5, font=("Segoe UI", 12))
        self.interval_entry.pack(side="left")
        tk.Label(interval_frame, text=" hour(s)", font=("Segoe UI", 12)).pack(side="left")

        tk.Label(root, text="First reminder:", font=("Segoe UI", 12)).pack(pady=(15, 5))

        first_frame = tk.Frame(root)
        first_frame.pack()

        tk.Radiobutton(
            first_frame, text="Now", variable=self.first_mode, value="now"
        ).grid(row=0, column=0, sticky="w")
        tk.Radiobutton(
            first_frame, text="Next perfect hour", variable=self.first_mode, value="perfect_hour"
        ).grid(row=1, column=0, sticky="w")
        tk.Radiobutton(
            first_frame, text="Custom time (HH:MM, 24h):", variable=self.first_mode, value="custom"
        ).grid(row=2, column=0, sticky="w")
        tk.Entry(first_frame, textvariable=self.custom_time_var, width=8).grid(row=2, column=1, padx=5)

        self.start_button = tk.Button(
            root, text="START", command=self.start, width=20, height=2, font=("Segoe UI", 12, "bold")
        )
        self.start_button.pack(pady=20)

        self.status = tk.Label(root, text="Ready", font=("Segoe UI", 11), fg="gray")
        self.status.pack()

        self.countdown = tk.Label(root, text="", font=("Segoe UI", 11))
        self.countdown.pack(pady=5)

        if TRAY_AVAILABLE:
            tk.Label(
                root, text="Closing this window minimizes to the tray.", font=("Segoe UI", 9), fg="gray"
            ).pack(pady=(10, 0))

    def _load_config(self):
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        pdf_path = cfg.get("pdf_path", "")
        if pdf_path and os.path.exists(pdf_path):
            self.pdf_path = pdf_path
            self.file_label.config(text=f"Selected: {os.path.basename(pdf_path)}", fg="green")

        interval = cfg.get("interval")
        if interval:
            self.interval_entry.delete(0, "end")
            self.interval_entry.insert(0, str(interval))

    def _save_config(self):
        cfg = {"pdf_path": self.pdf_path, "interval": self.interval}
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(cfg, f)
        except OSError:
            pass

    def select_pdf(self):
        path = filedialog.askopenfilename(
            title="Select Study Tracker PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if path:
            self.pdf_path = path
            self.file_label.config(text=f"Selected: {os.path.basename(path)}", fg="green")

    def open_pdf_in_edge(self):
        if not self.pdf_path:
            return

        if self.edge_process and self.edge_process.poll() is None:
            try:
                self.edge_process.terminate()
            except Exception:
                pass

        edge = next((p for p in EDGE_PATHS if os.path.exists(p)), None)

        if not edge:
            messagebox.showerror("Microsoft Edge not found", "Microsoft Edge could not be found on this PC.")
            return

        try:
            self.edge_process = subprocess.Popen([edge, self.pdf_path])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open the PDF:\n\n{e}")

        self._update_tray_title()

    def get_next_perfect_hour(self):
        now = datetime.now()
        candidate = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        while candidate.hour % self.interval != 0:
            candidate += timedelta(hours=1)
        return candidate

    def get_first_reminder_time(self):
        mode = self.first_mode.get()

        if mode == "now":
            return datetime.now()

        if mode == "custom":
            text = self.custom_time_var.get().strip()
            try:
                hh, mm = text.split(":")
                hh, mm = int(hh), int(mm)
                candidate = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
                if candidate <= datetime.now():
                    candidate += timedelta(days=1)
                return candidate
            except (ValueError, IndexError):
                messagebox.showerror("Invalid time", "Enter the custom time as HH:MM (24-hour), e.g. 14:30.")
                return None

        return self.get_next_perfect_hour()

    def start(self):
        if self.running:
            self.stop()
            return

        if not self.pdf_path:
            messagebox.showwarning("No PDF", "Please select your study tracker PDF first.")
            return

        try:
            hours = int(self.interval_entry.get())
            if hours < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid interval", "Enter a valid number of hours.")
            return

        self.interval = hours

        first_time = self.get_first_reminder_time()
        if first_time is None:
            return

        self._save_config()

        self.running = True
        self.paused = False
        self.start_button.config(text="STOP", bg="#ff5555")
        self.status.config(text=f"Running — every {hours} hour(s)", fg="green")

        self.next_time = first_time

        if self.next_time <= datetime.now():
            self.open_pdf_in_edge()
            self.next_time = self.get_next_perfect_hour() if self.first_mode.get() != "custom" \
                else self.next_time + timedelta(hours=self.interval)

        self.update_countdown()
        self.schedule_next()
        self._update_tray_menu()

    def schedule_next(self):
        if not self.running or self.paused:
            return

        remaining = (self.next_time - datetime.now()).total_seconds()

        if remaining <= 0:
            self.open_pdf_in_edge()
            self.next_time += timedelta(hours=self.interval)
            self.schedule_next()
        else:
            self.timer_job = self.root.after(1000, self.schedule_next)

    def update_countdown(self):
        if not self.running:
            self.countdown.config(text="")
            return

        if self.paused:
            self.countdown.config(text="Paused")
            self.countdown_job = self.root.after(1000, self.update_countdown)
            return

        remaining = self.next_time - datetime.now()
        total_seconds = max(0, int(remaining.total_seconds()))
        h, m, s = total_seconds // 3600, (total_seconds % 3600) // 60, total_seconds % 60

        self.countdown.config(text=f"Next tracker in: {h:02d}:{m:02d}:{s:02d}")
        self._update_tray_title()
        self.countdown_job = self.root.after(1000, self.update_countdown)

    def stop(self):
        self.running = False
        self.paused = False
        self.next_time = None

        if self.timer_job:
            self.root.after_cancel(self.timer_job)
            self.timer_job = None
        if self.countdown_job:
            self.root.after_cancel(self.countdown_job)
            self.countdown_job = None

        self.start_button.config(text="START", bg="SystemButtonFace")
        self.status.config(text="Stopped", fg="gray")
        self.countdown.config(text="")
        self._update_tray_menu()
        self._update_tray_title()

    def toggle_pause(self):
        if not self.running:
            return
        self.paused = not self.paused
        if self.paused:
            self.status.config(text="Paused", fg="#b36b00")
            if self.timer_job:
                self.root.after_cancel(self.timer_job)
                self.timer_job = None
        else:
            self.status.config(text=f"Running — every {self.interval} hour(s)", fg="green")
            self.schedule_next()
        self._update_tray_menu()

    def _make_tray_image(self):
        img = Image.new("RGB", (64, 64), "#2b6cb0")
        d = ImageDraw.Draw(img)
        d.rectangle((14, 10, 50, 54), fill="white")
        d.rectangle((20, 18, 44, 22), fill="#2b6cb0")
        d.rectangle((20, 28, 44, 32), fill="#2b6cb0")
        d.rectangle((20, 38, 44, 42), fill="#2b6cb0")
        return img

    def _start_tray(self):
        image = self._make_tray_image()
        self.tray_icon = pystray.Icon("study_tracker", image, "Study Tracker", self._build_tray_menu())
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _build_tray_menu(self):
        return pystray.Menu(
            Item("Show window", self._tray_show, default=True),
            Item("Open PDF now", self._tray_open_now),
            Item(
                lambda item: "Resume" if self.paused else "Pause",
                self._tray_toggle_pause,
                enabled=lambda item: self.running,
            ),
            Item("Exit", self._tray_exit),
        )

    def _update_tray_menu(self):
        if self.tray_icon:
            self.tray_icon.menu = self._build_tray_menu()

    def _update_tray_title(self):
        if not self.tray_icon:
            return
        if self.running and not self.paused and self.next_time:
            self.tray_icon.title = f"Study Tracker — next at {self.next_time.strftime('%H:%M')}"
        elif self.paused:
            self.tray_icon.title = "Study Tracker — paused"
        else:
            self.tray_icon.title = "Study Tracker — stopped"

    def _tray_show(self, icon=None, item=None):
        self.root.after(0, self.root.deiconify)

    def _tray_open_now(self, icon=None, item=None):
        self.root.after(0, self.open_pdf_in_edge)

    def _tray_toggle_pause(self, icon=None, item=None):
        self.root.after(0, self.toggle_pause)

    def _tray_exit(self, icon=None, item=None):
        self.root.after(0, self._real_exit)

    def on_close(self):
        if TRAY_AVAILABLE:
            self.root.withdraw()
        else:
            self._real_exit()

    def _real_exit(self):
        self.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()
        sys.exit(0)


if __name__ == "__main__":
    root = tk.Tk()
    app = StudyTracker(root)
    root.mainloop()
