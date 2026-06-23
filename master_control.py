#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
产线数据采集主控面板 v2.0
=======================
一目了然：配置 → 采集 → 看报告

Tab 1: 站别管理（表格直管，不用右键菜单）
Tab 2: 采集监控（一键触发，实时状态）
Tab 3: 历史报告
"""
import os
import sys
import json
import threading
import time
import subprocess
import configparser
import datetime
from tkinter import ttk, filedialog, messagebox
import tkinter as tk

STYLE = {
    "bg": "#f5f6fa",
    "card": "#ffffff",
    "accent": "#2563EB",
    "success": "#10B981",
    "danger": "#EF4444",
    "warning": "#F59E0B",
    "text": "#1e293b",
    "sub": "#64748b",
    "border": "#e2e8f0",
}

POLL_INTERVAL = 3
TIMEOUT = 600


# ═══════════════════════════════════════════════
# 核心引擎（同 v1.0）
# ═══════════════════════════════════════════════
class TriggerManager:
    def __init__(self, server_root):
        self.root = server_root
        self.dir = os.path.join(server_root, "trigger")

    def cmd_path(self, line, st):
        d = os.path.join(self.dir, f"line_{line}")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{st}_cmd.json")

    def done_path(self, line, st):
        return os.path.join(self.dir, f"line_{line}", f"{st}_done.json")

    def write(self, line, st, target_root, src_dirs):
        cmd = {
            "action": "sync_now",
            "timestamp": datetime.datetime.now().isoformat(),
            "line": line, "station_type": st,
            "target_root": target_root,
            "source_paths": [{"src": d, "dst_sub": os.path.basename(d.rstrip("/\\")) or "data"} for d in src_dirs],
        }
        p = self.cmd_path(line, st)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cmd, f, ensure_ascii=False, indent=2)
        return p

    def check(self, line, st):
        p = self.done_path(line, st)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except:
            return None


class AnalyzerBridge:
    @staticmethod
    def run(data_path, out_dir, log_cb):
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import at_analyzer
            log_cb("🔍 解析数据中...")
            recs, skipped, sf = at_analyzer.parse_source(data_path)
            if not recs:
                raise ValueError("未找到有效记录")
            log_cb(f"📊 {len(recs)} 行记录，{len(sf)} 个站别")
            a = at_analyzer.analyze(recs)
            os.makedirs(out_dir, exist_ok=True)
            hp = at_analyzer.make_html(a, out_dir, os.path.join(out_dir, "report.html"), os.path.basename(data_path))
            log_cb(f"✅ 报告: {hp}")
            return hp, a
        except ImportError:
            log_cb("⚠️ 未找到 at_analyzer.py，尝试子进程...")
            # fallback
            analyzer = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AT-Audio-Test-Analyzer", "at_analyzer.py")
            code = f'''
import sys; sys.path.insert(0, r"{os.path.dirname(analyzer)}")
import at_analyzer as aa
r,sk,sf = aa.parse_source(r"{data_path}")
a = aa.analyze(r)
hp = aa.make_html(a, r"{out_dir}", r"{os.path.join(out_dir, 'report.html')}", r"{os.path.basename(data_path)}")
print("OK:"+hp)
'''
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
            if result.returncode == 0 and "OK:" in result.stdout:
                return result.stdout.split("OK:")[1].strip(), None
            raise RuntimeError(result.stderr)


# ═══════════════════════════════════════════════
# GUI v2.0 — 一目了然
# ═══════════════════════════════════════════════
class App:
    def __init__(self, root):
        self.root = root
        root.title("产线数据采集主控 v2.0")
        root.geometry("1000x720")
        root.minsize(900, 600)
        root.configure(bg=STYLE["bg"])

        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except:
            pass

        app_dir = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
        self.cfg_file = os.path.join(app_dir, "config.ini")
        self.cfg = configparser.ConfigParser()

        # 数据
        self.server = tk.StringVar()
        self.date = tk.StringVar(value=datetime.date.today().isoformat())
        self.stations = []  # [{line, station, dirs:[...], enabled:bool}]

        # 运行时
        self.monitoring = False
        self.pending = []
        self.status = {}  # (line,st) → waiting/running/done/error
        self.last_report = None

        self.load_cfg()
        self._build()
        self._refresh_table()

    # ═══ 配置 ═══
    def load_cfg(self):
        if os.path.exists(self.cfg_file):
            self.cfg.read(self.cfg_file, encoding="utf-8")
            self.server.set(self.cfg.get("SERVER", "root", fallback=""))
            self.date.set(self.cfg.get("SERVER", "date", fallback=datetime.date.today().isoformat()))
            for s in self.cfg.sections():
                if s.startswith("STATION_"):
                    self.stations.append({
                        "line": self.cfg.get(s, "line", fallback=""),
                        "station": self.cfg.get(s, "station", fallback=""),
                        "dirs": json.loads(self.cfg.get(s, "dirs", fallback="[]")),
                        "enabled": self.cfg.getboolean(s, "enabled", fallback=True),
                    })

    def save_cfg(self):
        if "SERVER" not in self.cfg:
            self.cfg["SERVER"] = {}
        self.cfg["SERVER"]["root"] = self.server.get()
        self.cfg["SERVER"]["date"] = self.date.get()
        # 清旧配置
        for s in list(self.cfg.sections()):
            if s.startswith("STATION_"):
                self.cfg.remove_section(s)
        for i, st in enumerate(self.stations):
            sec = f"STATION_{i}"
            self.cfg[sec] = {
                "line": st["line"],
                "station": st["station"],
                "dirs": json.dumps(st.get("dirs", []), ensure_ascii=False),
                "enabled": str(st.get("enabled", True)),
            }
        with open(self.cfg_file, "w", encoding="utf-8") as f:
            self.cfg.write(f)

    # ═══ UI ═══
    def _build(self):
        # ── 顶部栏 ──
        top = tk.Frame(self.root, bg=STYLE["accent"], height=52)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text="🏭 产线数据采集主控 v2.0", bg=STYLE["accent"], fg="white",
                 font=("Microsoft YaHei", 16, "bold")).pack(side="left", padx=20, pady=12)
        tk.Label(top, text="配置 → 采集 → 看报告", bg=STYLE["accent"], fg="#93C5FD",
                 font=("Microsoft YaHei", 10)).pack(side="right", padx=20, pady=12)

        # ── 服务器行 ──
        bar = tk.Frame(self.root, bg=STYLE["card"], height=48)
        bar.pack(fill="x", padx=12, pady=(10, 0))
        bar.pack_propagate(False)

        tk.Label(bar, text="📡 服务器", bg=STYLE["card"], fg=STYLE["text"],
                 font=("Microsoft YaHei", 11, "bold")).pack(side="left", padx=(12, 6), pady=12)
        tk.Entry(bar, textvariable=self.server, font=("Consolas", 10), width=45,
                 bg="#f8fafc", relief="solid", bd=1).pack(side="left", ipady=2, pady=8)
        tk.Button(bar, text="浏览", command=lambda: self._browse(self.server),
                  bg=STYLE["accent"], fg="white", font=("Microsoft YaHei", 9),
                  relief="flat", padx=14, cursor="hand2").pack(side="left", padx=6)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10, pady=8)

        tk.Label(bar, text="📅 日期", bg=STYLE["card"], fg=STYLE["text"],
                 font=("Microsoft YaHei", 11, "bold")).pack(side="left", padx=(6, 4), pady=12)
        tk.Entry(bar, textvariable=self.date, font=("Consolas", 11), width=12,
                 bg="#f8fafc", relief="solid", bd=1).pack(side="left", ipady=2, pady=8)
        tk.Button(bar, text="今天", command=lambda: self.date.set(datetime.date.today().isoformat()),
                  bg="#e2e8f0", fg=STYLE["text"], font=("Microsoft YaHei", 9),
                  relief="flat", padx=8, cursor="hand2").pack(side="left", padx=4)

        # ── 主 Notebook ──
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=12, pady=8)

        # === Tab 1: 站别管理 ===
        t1 = tk.Frame(nb, bg=STYLE["bg"])
        nb.add(t1, text="  ⚙️ 站别管理  ")

        # 添加行
        add_bar = tk.Frame(t1, bg=STYLE["card"])
        add_bar.pack(fill="x", pady=(0, 8))
        tk.Label(add_bar, text="  线体:", bg=STYLE["card"], font=("Microsoft YaHei", 10)).pack(side="left", pady=8)
        self.add_line = ttk.Combobox(add_bar, values=["A03", "A05", "A07", "Line_1", "Line_2"], width=8, font=("Consolas", 10))
        self.add_line.pack(side="left", padx=(4, 12), pady=8)
        self.add_line.set("A03")
        tk.Label(add_bar, text="站别:", bg=STYLE["card"], font=("Microsoft YaHei", 10)).pack(side="left", pady=8)
        self.add_st = ttk.Combobox(add_bar, values=["AT", "FT", "QA"], width=6, font=("Consolas", 10))
        self.add_st.pack(side="left", padx=4, pady=8)
        self.add_st.set("AT")
        tk.Button(add_bar, text="➕ 添加", command=self._add_station,
                  bg=STYLE["accent"], fg="white", font=("Microsoft YaHei", 10),
                  relief="flat", padx=14, cursor="hand2").pack(side="left", padx=10, pady=6)

        # 表格
        tbl_frame = tk.Frame(t1, bg=STYLE["card"])
        tbl_frame.pack(fill="both", expand=True)

        cols = ("#", "线体", "站别", "数据源目录", "启用", "")
        self.tbl = ttk.Treeview(tbl_frame, columns=cols, show="headings", height=14)
        self.tbl.heading("#", text="#"); self.tbl.column("#", width=35, anchor="center")
        self.tbl.heading("线体", text="线体"); self.tbl.column("线体", width=70, anchor="center")
        self.tbl.heading("站别", text="站别"); self.tbl.column("站别", width=60, anchor="center")
        self.tbl.heading("数据源目录", text="数据源目录"); self.tbl.column("数据源目录", width=420)
        self.tbl.heading("启用", text="启用"); self.tbl.column("启用", width=50, anchor="center")
        self.tbl.heading("", text=""); self.tbl.column("", width=160)
        self.tbl.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(tbl_frame, command=self.tbl.yview)
        sb.pack(side="right", fill="y")
        self.tbl.config(yscrollcommand=sb.set)

        # 表格内按钮绑定
        self.tbl.bind("<Button-1>", self._tbl_click)
        self.tbl.tag_configure("on", foreground=STYLE["success"])
        self.tbl.tag_configure("off", foreground=STYLE["sub"])

        # === Tab 2: 采集监控 ===
        t2 = tk.Frame(nb, bg=STYLE["bg"])
        nb.add(t2, text="  🚀 采集监控  ")

        # 大按钮
        btn_row = tk.Frame(t2, bg=STYLE["bg"])
        btn_row.pack(fill="x", pady=(8, 12))
        self.go_btn = tk.Button(btn_row, text="🚀 开始采集全部站别", command=self._start,
                                 bg=STYLE["accent"], fg="white",
                                 font=("Microsoft YaHei", 14, "bold"),
                                 relief="flat", padx=30, pady=14, cursor="hand2")
        self.go_btn.pack(fill="x", ipady=4)

        # 进度条
        self.prog = ttk.Progressbar(t2, mode="determinate", length=600)
        self.prog.pack(fill="x")

        self.prog_label = tk.Label(t2, text="就绪", bg=STYLE["bg"], fg=STYLE["sub"],
                                    font=("Microsoft YaHei", 10))
        self.prog_label.pack(pady=(4, 8))

        # 状态表
        st_frame = tk.Frame(t2, bg=STYLE["card"])
        st_frame.pack(fill="both", expand=True)

        scols = ("线体", "站别", "状态", "详情")
        self.st_tbl = ttk.Treeview(st_frame, columns=scols, show="headings", height=10)
        self.st_tbl.heading("线体", text="线体"); self.st_tbl.column("线体", width=80, anchor="center")
        self.st_tbl.heading("站别", text="站别"); self.st_tbl.column("站别", width=80, anchor="center")
        self.st_tbl.heading("状态", text="状态"); self.st_tbl.column("状态", width=120, anchor="center")
        self.st_tbl.heading("详情", text="详情"); self.st_tbl.column("详情", width=350)
        self.st_tbl.pack(side="left", fill="both", expand=True)

        ssb = ttk.Scrollbar(st_frame, command=self.st_tbl.yview)
        ssb.pack(side="right", fill="y")
        self.st_tbl.config(yscrollcommand=ssb.set)

        self.st_tbl.tag_configure("ok", foreground=STYLE["success"])
        self.st_tbl.tag_configure("err", foreground=STYLE["danger"])
        self.st_tbl.tag_configure("wait", foreground=STYLE["warning"])

        # 打开报告按钮
        self.rpt_btn = tk.Button(t2, text="📄 打开最新报告", command=self._open_report,
                                  bg=STYLE["success"], fg="white",
                                  font=("Microsoft YaHei", 11, "bold"),
                                  relief="flat", padx=20, pady=10, cursor="hand2",
                                  state="disabled")
        self.rpt_btn.pack(side="left", pady=(10, 0), padx=(0, 6))

        self.rpt_dir_btn = tk.Button(t2, text="📂 打开输出目录", command=self._open_outdir,
                                      bg="#e2e8f0", fg=STYLE["text"],
                                      font=("Microsoft YaHei", 10),
                                      relief="flat", padx=16, pady=10, cursor="hand2",
                                      state="disabled")
        self.rpt_dir_btn.pack(side="left", pady=(10, 0))

        # === Tab 3: 日志 ===
        t3 = tk.Frame(nb, bg=STYLE["bg"])
        nb.add(t3, text="  📋 日志  ")

        log_top = tk.Frame(t3, bg=STYLE["bg"])
        log_top.pack(fill="x", pady=(0, 4))
        tk.Button(log_top, text="清空日志", command=self._clear_log,
                  bg="#e2e8f0", fg=STYLE["text"], font=("Microsoft YaHei", 9),
                  relief="flat", padx=10, cursor="hand2").pack(side="right")

        self.log = tk.Text(t3, bg="#1e293b", fg="#e2e8f0", insertbackground="white",
                           font=("Consolas", 9), relief="flat", bd=0, state="disabled")
        self.log.pack(fill="both", expand=True)

        # 状态栏
        sbar = tk.Frame(self.root, bg=STYLE["card"], height=26)
        sbar.pack(fill="x", side="bottom")
        sbar.pack_propagate(False)
        self.sbar_text = tk.Label(sbar, text="就绪 — 请先配置服务器路径和站别",
                                   bg=STYLE["card"], fg=STYLE["sub"],
                                   font=("Microsoft YaHei", 9), anchor="w")
        self.sbar_text.pack(side="left", fill="x", padx=12, pady=3)

    # ═══ 站别管理 ═══
    def _refresh_table(self):
        for i in self.tbl.get_children():
            self.tbl.delete(i)
        for idx, st in enumerate(self.stations, 1):
            dirs_text = "  |  ".join(st.get("dirs", [])) if st.get("dirs") else "（未配置 — 点击编辑）"
            en = "✅" if st.get("enabled", True) else "❌"
            tag = "on" if st.get("enabled", True) else "off"
            iid = self.tbl.insert("", "end",
                                   values=(idx, st["line"], st["station"], dirs_text, en, "📂编辑目录  🔄切换  🗑删除"),
                                   tags=(tag,))
            self.tbl.item(iid, tags=(tag,))

    def _add_station(self):
        line = self.add_line.get().strip()
        st = self.add_st.get().strip()
        if not line or not st:
            return
        for s in self.stations:
            if s["line"] == line and s["station"] == st:
                messagebox.showwarning("重复", f"{line}/{st} 已存在")
                return
        self.stations.append({"line": line, "station": st, "dirs": [], "enabled": True})
        self._refresh_table()
        self.save_cfg()
        self._sbar(f"已添加 {line}/{st}")

    def _tbl_click(self, event):
        item = self.tbl.identify_row(event.y)
        col = self.tbl.identify_column(event.x)
        if not item:
            return
        idx = int(self.tbl.index(item))
        if idx >= len(self.stations):
            return

        col_idx = int(col.replace("#", "")) - 1
        if col_idx == 5:  # 操作列
            # 判断点击位置
            x = event.x
            # 粗略判断：📂编辑目录(0-80) 🔄切换(80-140) 🗑删除(140-)
            st = self.stations[idx]
            if x < 90:
                self._edit_dirs(idx)
            elif x < 155:
                st["enabled"] = not st.get("enabled", True)
                self._refresh_table()
                self.save_cfg()
            else:
                if messagebox.askyesno("确认", f"删除 {st['line']}/{st['station']}？"):
                    del self.stations[idx]
                    self._refresh_table()
                    self.save_cfg()

    def _edit_dirs(self, idx):
        st = self.stations[idx]
        dlg = tk.Toplevel(self.root)
        dlg.title(f"数据源目录 — {st['line']}/{st['station']}")
        dlg.geometry("600x380")
        dlg.configure(bg=STYLE["card"])
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text=f"{st['line']} 线 — {st['station']} 站",
                 bg=STYLE["card"], font=("Microsoft YaHei", 13, "bold"),
                 fg=STYLE["text"]).pack(pady=(12, 2))
        tk.Label(dlg, text="设置此站别需要采集的数据源目录（可添加多个）",
                 bg=STYLE["card"], font=("Microsoft YaHei", 9),
                 fg=STYLE["sub"]).pack(pady=(0, 8))

        lst_frame = tk.Frame(dlg, bg=STYLE["card"])
        lst_frame.pack(fill="both", expand=True, padx=12)

        lb = tk.Listbox(lst_frame, font=("Consolas", 9), selectmode=tk.EXTENDED, bg="#f8fafc")
        lb.pack(side="left", fill="both", expand=True)
        sc = ttk.Scrollbar(lst_frame, command=lb.yview)
        sc.pack(side="right", fill="y")
        lb.config(yscrollcommand=sc.set)

        for d in st.get("dirs", []):
            lb.insert(tk.END, d)

        def add():
            p = filedialog.askdirectory(title="选择数据源目录", mustexist=True)
            if p:
                lb.insert(tk.END, p.replace("\\", "/"))

        def remove():
            for i in reversed(lb.curselection()):
                lb.delete(i)

        btn_f = tk.Frame(dlg, bg=STYLE["card"])
        btn_f.pack(pady=8)
        tk.Button(btn_f, text="➕ 添加", command=add,
                  bg=STYLE["accent"], fg="white", font=("Microsoft YaHei", 10),
                  relief="flat", padx=14, pady=4).pack(side="left", padx=3)
        tk.Button(btn_f, text="🗑 删除选中", command=remove,
                  bg=STYLE["danger"], fg="white", font=("Microsoft YaHei", 10),
                  relief="flat", padx=14, pady=4).pack(side="left", padx=3)

        def save():
            st["dirs"] = [lb.get(i) for i in range(lb.size())]
            self._refresh_table()
            self.save_cfg()
            dlg.destroy()
            self._log(f"📂 {st['line']}/{st['station']} 目录已更新: {len(st['dirs'])} 个")

        tk.Button(dlg, text="💾 保存", command=save,
                  bg=STYLE["success"], fg="white",
                  font=("Microsoft YaHei", 11, "bold"),
                  relief="flat", padx=24, pady=8).pack(pady=(0, 12))

    # ═══ 采集流程 ═══
    def _start(self):
        srv = self.server.get().strip()
        if not srv or not os.path.exists(srv):
            messagebox.showerror("错误", f"服务器路径不存在:\n{srv}")
            return
        enabled = [s for s in self.stations if s.get("enabled", True) and s.get("dirs")]
        if not enabled:
            messagebox.showwarning("提示", "没有已启用且配置了源目录的站别")
            return

        self.save_cfg()
        self.monitoring = True
        self.go_btn.config(state="disabled", text="⏳ 采集中...", bg=STYLE["sub"])
        self.pending = [(s["line"], s["station"]) for s in enabled]
        self.status.clear()
        for l, st in self.pending:
            self.status[(l, st)] = "waiting"

        self._refresh_status_table()
        self.prog["value"] = 0
        self.prog_label.config(text="正在写入触发指令...")

        tm = TriggerManager(srv)
        tm.dir and os.makedirs(tm.dir, exist_ok=True)

        for s in enabled:
            tm.write(s["line"], s["station"], srv, s["dirs"])
            self._log(f"📤 写入 trigger → {s['line']}/{s['station']}")

        self._log(f"🚀 已触发 {len(enabled)} 个站别，等待机台响应...")
        self._sbar(f"等待 {len(enabled)} 个站别同步...")

        threading.Thread(target=self._monitor, args=(tm, len(enabled)), daemon=True).start()

    def _monitor(self, tm, total):
        start = time.time()
        while self.monitoring and self.pending:
            done_now = []
            for line, st in list(self.pending):
                d = tm.check(line, st)
                if d:
                    errs = d.get("errors", 0)
                    self.status[(line, st)] = "done" if errs == 0 else "error"
                    done_now.append((line, st, d))
                    self.pending.remove((line, st))

            for line, st, d in done_now:
                files = d.get("files_copied", 0)
                errs = d.get("errors", 0)
                emoji = "✅" if errs == 0 else "⚠️"
                self._log(f"{emoji} {line}/{st} 完成 — {files} 文件" + (f", {errs} 异常" if errs else ""))

            done = total - len(self.pending)
            self.root.after(0, lambda: self.prog["value"] = done / total * 90)
            self.root.after(0, lambda: self.prog_label.config(text=f"{done}/{total} 站别完成"))
            self.root.after(0, self._refresh_status_table)
            self.root.after(0, lambda: self._sbar(f"采集中... {done}/{total} 完成"))

            if not self.pending:
                break

            if time.time() - start > TIMEOUT:
                for l, st in self.pending:
                    self.status[(l, st)] = "error"
                    self._log(f"❌ {l}/{st} 超时无响应")
                self.root.after(0, self._refresh_status_table)
                self.root.after(0, self._done)
                return

            time.sleep(POLL_INTERVAL)

        self.root.after(0, self._done)

    def _done(self):
        self.monitoring = False
        self.go_btn.config(state="normal", text="🚀 重新采集", bg=STYLE["accent"])
        self.prog_label.config(text="采集完成，开始分析...")
        self.prog["value"] = 92
        self._refresh_status_table()

        has_data = any(v == "done" for v in self.status.values())
        if not has_data:
            self._sbar("所有站别均失败")
            self.prog_label.config(text="❌ 采集失败")
            return

        self._run_analysis()

    def _run_analysis(self):
        srv = self.server.get()
        date_str = self.date.get()
        data_root = os.path.join(srv, date_str)

        if not os.path.exists(data_root):
            # 尝试找服务器上最新的日期目录
            try:
                dirs = sorted([d for d in os.listdir(srv) if os.path.isdir(os.path.join(srv, d)) and len(d) == 10 and d[4] == '-'], reverse=True)
                if dirs:
                    data_root = os.path.join(srv, dirs[0])
                    self._log(f"⚠️ 指定日期目录不存在，使用最新: {dirs[0]}")
            except:
                pass

        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", date_str)
        self._log(f"🔍 分析路径: {data_root}")

        def analyze():
            try:
                hp, a = AnalyzerBridge.run(data_root, out_dir, self._log)
                self.last_report = hp
                self.root.after(0, lambda: self.prog["value"] == 100)
                self.root.after(0, lambda: self.prog_label.config(text="✅ 分析完成！"))
                self.root.after(0, lambda: self.rpt_btn.config(state="normal"))
                self.root.after(0, lambda: self.rpt_dir_btn.config(state="normal"))
                self.root.after(0, lambda: self._sbar(f"✅ 报告已生成"))

                # 复制到服务器
                try:
                    import shutil
                    rd = os.path.join(srv, "reports")
                    os.makedirs(rd, exist_ok=True)
                    shutil.copy2(hp, os.path.join(rd, f"{date_str}_report.html"))
                    self._log(f"📤 报告已同步到服务器: {rd}")
                except Exception as e:
                    self._log(f"⚠️ 同步报告失败: {e}")

                # 弹出
                if hp and os.path.exists(hp):
                    self._log("📄 自动打开报告...")
                    try:
                        if sys.platform == "win32":
                            os.startfile(hp)
                        elif sys.platform == "darwin":
                            subprocess.run(["open", hp])
                        else:
                            subprocess.run(["xdg-open", hp])
                    except:
                        pass

            except Exception as e:
                import traceback
                self._log(f"❌ 分析失败: {e}")
                self._log(traceback.format_exc())
                self.root.after(0, lambda: self.prog_label.config(text=f"❌ {e}"))
                self.root.after(0, lambda: self._sbar(f"❌ 分析失败"))

        threading.Thread(target=analyze, daemon=True).start()

    def _refresh_status_table(self):
        for i in self.st_tbl.get_children():
            self.st_tbl.delete(i)
        for (l, st), s in sorted(self.status.items()):
            icons = {"waiting": ("⏳ 等待中", "wait"), "done": ("✅ 完成", "ok"), "error": ("❌ 失败", "err")}
            icon, tag = icons.get(s, ("🔄 进行中", ""))
            cfg = next((x for x in self.stations if x["line"] == l and x["station"] == st), {})
            detail = ", ".join([os.path.basename(d) for d in cfg.get("dirs", [])]) if cfg.get("dirs") else ""
            self.st_tbl.insert("", "end", values=(l, st, icon, detail), tags=(tag,))

    # ═══ 报告 ═══
    def _open_report(self):
        if self.last_report and os.path.exists(self.last_report):
            try:
                if sys.platform == "win32":
                    os.startfile(self.last_report)
                elif sys.platform == "darwin":
                    subprocess.run(["open", self.last_report])
                else:
                    subprocess.run(["xdg-open", self.last_report])
            except:
                messagebox.showinfo("提示", f"报告位置:\n{self.last_report}")
        else:
            messagebox.showinfo("提示", "暂无报告")

    def _open_outdir(self):
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", self.date.get())
        if os.path.exists(d):
            try:
                if sys.platform == "win32":
                    os.startfile(d)
                else:
                    subprocess.run(["xdg-open", d])
            except:
                pass
        else:
            messagebox.showinfo("提示", "输出目录不存在")

    # ═══ 工具 ═══
    def _browse(self, var):
        p = filedialog.askdirectory(title="选择目录")
        if p:
            var.set(p.replace("\\", "/"))
            self.save_cfg()

    def _log(self, msg):
        def do():
            self.log.config(state="normal")
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            self.log.insert(tk.END, f"[{ts}] {msg}\n")
            self.log.see(tk.END)
            # 限制行数
            n = int(self.log.index('end-1c').split('.')[0])
            if n > 500:
                self.log.delete('1.0', f'{n - 500}.0')
            self.log.config(state="disabled")
        self.root.after(0, do)

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.config(state="disabled")

    def _sbar(self, msg):
        self.sbar_text.config(text=msg)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
