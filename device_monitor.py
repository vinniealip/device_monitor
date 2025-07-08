import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import pandas as pd
import threading
import subprocess
import time
import csv
import os

CSV_FILE = "device_list.csv"

if not os.path.isfile(CSV_FILE):
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo("Select File", "No CSV file found. Please select a device list CSV file to load.")
    CSV_FILE = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])
    root.destroy()
    if not CSV_FILE:
        raise SystemExit("No CSV selected.")

ROWS_PER_PAGE = 100

class PingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Device Monitor")

        try:
            self.full_df = pd.read_csv(CSV_FILE)
            if not {'Name', 'IP'}.issubset(self.full_df.columns):
                raise ValueError("CSV must contain 'Name' and 'IP' columns.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load device list: {e}")
            root.destroy()
            return

        self.page = 0
        self.auto_refresh_seconds = tk.IntVar(value=600)
        self.rotation_seconds = tk.IntVar(value=10)
        self.enable_monitoring = tk.BooleanVar(value=False)
        self.enable_rotation = tk.BooleanVar(value=False)
        self.paused = tk.BooleanVar(value=False)

        self.filtered_df = self.full_df.copy()
        self.camera_states = {}
        self.down_cameras = {}
        self.rows = []

        self.build_controls()
        self.build_layout()
        self.add_navigation()
        self.load_servers()
        self.start_background_monitor()
        self.start_page_rotation()

    def build_controls(self):
        frame = tk.Frame(self.root)
        frame.pack(pady=5)

        tk.Button(frame, text="Check Now", command=self.run_bulk_check).pack(side=tk.LEFT, padx=5)
        tk.Checkbutton(frame, text="Enable Auto Monitoring", variable=self.enable_monitoring).pack(side=tk.LEFT, padx=5)

        tk.Label(frame, text="Auto Check Interval (sec):").pack(side=tk.LEFT)
        tk.Entry(frame, textvariable=self.auto_refresh_seconds, width=5).pack(side=tk.LEFT)

        tk.Label(frame, text="Page Rotate:").pack(side=tk.LEFT)
        tk.Entry(frame, textvariable=self.rotation_seconds, width=5).pack(side=tk.LEFT)

        tk.Checkbutton(frame, text="Auto Rotate", variable=self.enable_rotation).pack(side=tk.LEFT, padx=5)

        self.pause_button = tk.Button(frame, text="Pause All", command=self.toggle_pause)
        self.pause_button.pack(side=tk.LEFT, padx=5)

        self.cancel_button = tk.Button(frame, text="Cancel Check", command=self.cancel_bulk_ping)
        self.cancel_button.pack(side=tk.LEFT, padx=5)

        tk.Button(frame, text="Export CSV", command=self.export_to_csv).pack(side=tk.LEFT, padx=5)

        self.status_label = tk.Label(self.root, text="Ready", fg="gray")
        self.status_label.pack(pady=5)

        self.progress = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(self.root, variable=self.progress, maximum=100)
        self.progress_label = tk.Label(self.root, text="", fg="blue")
        self.progress_bar.pack(fill=tk.X, padx=10, pady=5)
        self.progress_label.pack(pady=2)
        self.progress_bar.pack_forget()
        self.progress_label.pack_forget()

        self.cancel_bulk = False

    def build_layout(self):
        paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, sashrelief=tk.RAISED)
        paned.pack(fill=tk.BOTH, expand=True)

        left_frame = tk.Frame(paned)
        right_frame = tk.Frame(paned, bg="white", relief=tk.SUNKEN, bd=1)

        self.canvas = tk.Canvas(left_frame)
        self.scrollable_frame = tk.Frame(self.canvas)
        self.scrollbar = tk.Scrollbar(left_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        tk.Label(right_frame, text="DOWN Devices", font=('Arial', 10, 'bold'), bg="white").pack(anchor="n", pady=5)
        self.down_list = tk.Listbox(right_frame)
        self.down_scroll = tk.Scrollbar(right_frame, command=self.down_list.yview)
        self.down_list.config(yscrollcommand=self.down_scroll.set)
        self.down_list.pack(side="left", fill="both", expand=True)
        self.down_scroll.pack(side="right", fill="y")

        paned.add(left_frame, minsize=550)
        paned.add(right_frame, minsize=300)
        self.root.update_idletasks()
        width = self.root.winfo_width()
        paned.sash_place(0, width // 2, 0)

    def add_navigation(self):
        nav = tk.Frame(self.root)
        nav.pack(pady=5)
        self.nav_label = tk.Label(nav, text="")
        self.nav_label.pack(side=tk.LEFT, padx=10)
        tk.Button(nav, text="Previous", command=self.prev_page).pack(side=tk.LEFT)
        tk.Button(nav, text="Next", command=self.next_page).pack(side=tk.LEFT)

    def run_bulk_check(self):
        self.status_label.config(text="Bulk checking...")
        threading.Thread(target=self.bulk_ping_all, daemon=True).start()

    def check_server(self, row):
        if self.paused.get(): return
        ip, status_lbl, time_lbl, err_lbl = row
        status_lbl.config(text="Checking...", bg="yellow")
        result, reason = self.ping(ip)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        status_lbl.config(text=result, bg="lightgreen" if result == "UP" else "salmon")
        time_lbl.config(text=now)
        err_lbl.config(text=reason)
        self.camera_states[ip] = (result, now, reason)
        if result == "DOWN":
            if ip not in self.down_cameras:
                self.down_cameras[ip] = now
        elif ip in self.down_cameras:
            del self.down_cameras[ip]
        self.update_down_list()

    def check_one(self, ip):
        for row in self.rows:
            if row[0] == ip:
                threading.Thread(target=self.check_server, args=(row,), daemon=True).start()

    def ping_and_store(self, ip):
        result, reason = self.ping(ip)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self.camera_states[ip] = (result, now, reason)
        if result == "DOWN":
            if ip not in self.down_cameras:
                self.down_cameras[ip] = now
        elif ip in self.down_cameras:
            del self.down_cameras[ip]
        self.update_down_list()

    def bulk_ping_all(self):
        def format_time(seconds):
            m, s = divmod(int(seconds), 60)
            return f"{m}m {s}s" if m else f"{s}s"

        def worker(ip):
            if self.cancel_bulk:
                return
            nonlocal completed
            self.ping_and_store(ip)
            completed += 1
            elapsed = time.time() - start_time
            avg = elapsed / completed if completed else 0
            remaining = (total - completed) * avg
            self.progress.set((completed / total) * 100)
            self.progress_label.config(text=f"Pinging {completed} of {total} devices... (~{format_time(remaining)} remaining)")
            self.root.update_idletasks()

        self.progress_bar.pack(fill=tk.X, padx=10, pady=5)
        self.progress_label.pack(pady=2)
        total = len(self.filtered_df["IP"])
        if total == 0: return
        self.progress.set(0)
        completed = 0
        start_time = time.time()
        threads = []
        self.cancel_bulk = False
        for ip in self.filtered_df["IP"]:
            t = threading.Thread(target=worker, args=(ip,))
            threads.append(t)
            t.start()
            if len(threads) >= 100:
                for t in threads: t.join()
                threads.clear()
        for t in threads: t.join()
        self.progress_label.config(text="Bulk check complete." if not self.cancel_bulk else "Bulk check cancelled.")
        self.root.after(2000, self.progress_bar.pack_forget)
        self.root.after(2000, self.progress_label.pack_forget)

    def ping(self, ip):
        try:
            output = subprocess.run(["ping", "-n", "1", ip],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW)
            if "TTL=" in output.stdout: return "UP", ""
            elif "Request timed out" in output.stdout: return "DOWN", "Timeout"
            elif "unreachable" in output.stdout: return "DOWN", "Unreachable"
            else: return "DOWN", "No response"
        except Exception as e:
            return "DOWN", str(e)

    def toggle_pause(self):
        self.paused.set(not self.paused.get())
        self.pause_button.config(text="Resume All" if self.paused.get() else "Pause All")

    def cancel_bulk_ping(self):
        self.cancel_bulk = True
        self.progress_label.config(text="Cancelling bulk check...")

    def start_background_monitor(self):
        def loop():
            while True:
                if self.enable_monitoring.get() and not self.paused.get():
                    threading.Thread(target=self.bulk_ping_all, daemon=True).start()
                time.sleep(self.auto_refresh_seconds.get())
        threading.Thread(target=loop, daemon=True).start()

    def start_page_rotation(self):
        def loop():
            while True:
                if self.enable_rotation.get() and not self.paused.get():
                    self.next_page()
                time.sleep(self.rotation_seconds.get())
        threading.Thread(target=loop, daemon=True).start()

    def prev_page(self):
        if self.page > 0:
            self.page -= 1
            self.load_servers()

    def next_page(self):
        max_page = (len(self.filtered_df) - 1) // ROWS_PER_PAGE
        self.page = (self.page + 1) if self.page < max_page else 0
        self.load_servers()

    def load_servers(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.rows.clear()
        start = self.page * ROWS_PER_PAGE
        end = start + ROWS_PER_PAGE
        self.df = self.filtered_df.iloc[start:end]
        self.nav_label.config(text=f"Showing {start+1}-{min(end, len(self.filtered_df))} of {len(self.filtered_df)}")
        headers = ["Name", "IP", "Status", "Last Checked", "Error", "Action"]
        for col, head in enumerate(headers):
            tk.Label(self.scrollable_frame, text=head, font=('Arial', 10, 'bold')).grid(row=0, column=col, padx=5)
        for i, row in self.df.iterrows():
            ip = row["IP"]
            name_lbl = tk.Label(self.scrollable_frame, text=row["Name"])
            ip_lbl = tk.Label(self.scrollable_frame, text=ip)
            status_lbl = tk.Label(self.scrollable_frame, text="-")
            time_lbl = tk.Label(self.scrollable_frame, text="-")
            err_lbl = tk.Label(self.scrollable_frame, text="-")
            btn = tk.Button(self.scrollable_frame, text="Check", command=lambda ip=ip: self.check_one(ip))
            name_lbl.grid(row=i+1, column=0, sticky="w", padx=5)
            ip_lbl.grid(row=i+1, column=1, sticky="w", padx=5)
            status_lbl.grid(row=i+1, column=2)
            time_lbl.grid(row=i+1, column=3)
            err_lbl.grid(row=i+1, column=4)
            btn.grid(row=i+1, column=5)
            if ip in self.camera_states:
                s, t, e = self.camera_states[ip]
                status_lbl.config(text=s, bg="lightgreen" if s=="UP" else "salmon")
                time_lbl.config(text=t)
                err_lbl.config(text=e)
            self.rows.append((ip, status_lbl, time_lbl, err_lbl))

    def update_down_list(self):
        self.down_list.delete(0, tk.END)
        sorted_down = sorted(self.down_cameras.items(), key=lambda x: x[1])
        for ip, ts in sorted_down:
            name = self.full_df[self.full_df["IP"] == ip]["Name"].values[0]
            self.down_list.insert(tk.END, f"{name} ({ip}) - Detected down at {ts}")

    def export_to_csv(self):
        with open("checked_background_servers.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Name", "IP", "Status", "Last Checked", "Error"])
            for ip in self.filtered_df["IP"]:
                name = self.full_df[self.full_df["IP"] == ip]["Name"].values[0]
                s, t, e = self.camera_states.get(ip, ("-", "-", "-"))
                w.writerow([name, ip, s, t, e])
        self.status_label.config(text="Exported to CSV")

if __name__ == "__main__":
    root = tk.Tk()
    app = PingApp(root)
    root.geometry("1200x700")
    root.mainloop()
