#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
产线数据采集主控面板 v1.0
=======================
部署于办公电脑，统一管理多产线多站别的数据采集触发与自动分析。

工作流：
  1. 配置服务器路径 + 各线体/站别
  2. 点击「开始采集」→ 向服务器写 trigger JSON
  3. 轮询各机台完成标记
  4. 全部到齐 → 自动跑 AT Analyzer 直读网络路径 → 弹出报告
"""
import os
import sys
import json
import threading
import time
import subprocess
import configparser
import datetime
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
import tkinter as tk

# ═══════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════
POLL_INTERVAL = 3  # 轮询完成标记间隔(秒)
TRIGGER_FILENAME = "{station}_cmd.json"
DONE_FILENAME = "{station}_done.json"

STYLE = {
    "bg": "#f0f2f5",
    "fg": "#1a1a2e",
    "card_bg": "#ffffff",
    "accent": "#2563EB",
    "accent_hover": "#1D4ED8",
    "success": "#16A34A",
    "danger": "#DC2626",
    "warning": "#F59E0B",
    "text_secondary": "#6B7280",
    "input_bg": "#ffffff",
}


# ═══════════════════════════════════════════════
# 核心逻辑
# ═══════════════════════════════════════════════
class TriggerManager:
    """管理服务器端 trigger 文件的读写与轮询"""

    def __init__(self, server_root):
        self.server_root = server_root
        self.trigger_dir = os.path.join(server_root, "trigger")

    def ensure_trigger_dir(self):
        os.makedirs(self.trigger_dir, exist_ok=True)

    def get_trigger_path(self, line_id, station_type):
        """trigger/{line_id}/{station}_cmd.json"""
        line_dir = os.path.join(self.trigger_dir, f"line_{line_id}")
        os.makedirs(line_dir, exist_ok=True)
        return os.path.join(line_dir, TRIGGER_FILENAME.format(station=station_type))

    def get_done_path(self, line_id, station_type):
        """trigger/{line_id}/{station}_done.json"""
        line_dir = os.path.join(self.trigger_dir, f"line_{line_id}")
        return os.path.join(line_dir, DONE_FILENAME.format(station=station_type))

    def write_trigger(self, line_id, station_type, config):
        """向服务器写入一条 trigger 指令"""
        cmd = {
            "action": "sync_now",
            "timestamp": datetime.datetime.now().isoformat(),
            "line": line_id,
            "station_type": station_type,
            "target_root": config.get("target_root", ""),
            "source_paths": config.get("source_paths", []),
        }
        path = self.get_trigger_path(line_id, station_type)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cmd, f, ensure_ascii=False, indent=2)
        return path

    def write_all_triggers(self, lines_config, log_cb=None):
        """批量写入所有线体/站别的 trigger"""
        written = []
        for line_id, stations in lines_config.items():
            for st_type, st_config in stations.items():
                if st_config.get("enabled", True):
                    path = self.write_trigger(line_id, st_type, st_config)
                    written.append((line_id, st_type, path))
                    if log_cb:
                        log_cb(f"📤 trigger → line_{line_id}/{st_type}")
        return written

    def check_done(self, line_id, station_type):
        """检查某个站别是否完成"""
        path = self.get_done_path(line_id, station_type)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def clear_trigger(self, line_id, station_type):
        """清理已消费的 trigger 文件"""
        path = self.get_trigger_path(line_id, station_type)
        if os.path.exists(path):
            os.remove(path)

    def clear_done(self, line_id, station_type):
        path = self.get_done_path(line_id, station_type)
        if os.path.exists(path):
            os.remove(path)

    def build_source_paths(self, station_config, target_root):
        """根据站别配置构建数据路径映射"""
        paths = []
        for src_dir in station_config.get("source_dirs", []):
            # src_dir 格式: D:/TestLog/AT01/TC661
            if src_dir:
                basename = os.path.basename(src_dir.rstrip("/\\"))
                if not basename:
                    basename = os.path.basename(os.path.dirname(src_dir.rstrip("/\\")))
                paths.append({"src": src_dir, "dst_sub": basename})
        return paths


# ═══════════════════════════════════════════════
# Analyzer 桥接
# ═══════════════════════════════════════════════
class AnalyzerBridge:
    """对接 AT-Audio-Test-Analyzer，支持导入模块或子进程调用"""

    @staticmethod
    def run_analysis(data_path, output_dir, log_cb=None):
        """
        对指定数据目录运行分析。
        先尝试导入 at_analyzer 模块（同目录），失败则走子进程。
        """
        try:
            # 尝试导入 at_analyzer 模块
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import at_analyzer

            if log_cb:
                log_cb("🔍 开始解析数据...")

            recs, skipped, st_files = at_analyzer.parse_source(data_path)
            if not recs:
                raise ValueError("未找到有效测试记录")

            if log_cb:
                log_cb(f"📊 解析完成：{len(recs)} 行记录")

            analysis = at_analyzer.analyze(recs)
            os.makedirs(output_dir, exist_ok=True)

            html_path = at_analyzer.make_html(
                analysis, output_dir,
                os.path.join(output_dir, "report.html"),
                os.path.basename(data_path)
            )

            if log_cb:
                log_cb(f"✅ 报告已生成：{html_path}")

            return html_path, analysis

        except ImportError:
            # 子进程方式调用
            if log_cb:
                log_cb("⚠️ 未找到本地 at_analyzer.py，尝试子进程调用...")

            analyzer_script = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "AT-Audio-Test-Analyzer", "at_analyzer.py"
            )

            # 用 -c 让 Python 直接执行内联代码来调用分析
            code = f'''
import sys; sys.path.insert(0, r"{os.path.dirname(analyzer_script)}")
import at_analyzer
recs, skipped, st_files = at_analyzer.parse_source(r"{data_path}")
analysis = at_analyzer.analyze(recs)
html = at_analyzer.make_html(analysis, r"{output_dir}", r"{os.path.join(output_dir, 'report.html')}", r"{os.path.basename(data_path)}")
print("OK:" + html)
'''
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0 and "OK:" in result.stdout:
                html_path = result.stdout.split("OK:")[1].strip()
                return html_path, None
            else:
                raise RuntimeError(f"Analyzer 子进程失败:\n{result.stderr}")


# ═══════════════════════════════════════════════
# GUI 主控面板
# ═══════════════════════════════════════════════
class MasterControlApp:
    def __init__(self, root):
        self.root = root
        self.root.title("产线数据采集主控面板 v1.0")
        self.root.geometry("900x700")
        self.root.minsize(800, 550)
        self.root.configure(bg=STYLE["bg"])

        # DPI 感知
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        # 配置
        app_path = os.path.dirname(
            sys.executable if getattr(sys, 'frozen', False)
            else os.path.abspath(__file__)
        )
        self.config_file = os.path.join(app_path, "config.ini")
        self.config = configparser.ConfigParser()
        self.server_root = tk.StringVar(value="")
        self.date_var = tk.StringVar(value=datetime.date.today().isoformat())
        self.status_var = tk.StringVar(value="就绪 — 配置服务器路径后开始")
        self.progress_var = tk.DoubleVar(value=0)

        # 线体/站别树形数据
        # {line_id: {station_type: {"enabled": True, "source_dirs": [...], "target_root": "..."}}}
        self.lines = {}

        # 运行状态
        self.monitoring = False
        self.monitor_thread = None
        self.pending_stations = []  # [(line_id, station_type), ...]
        self.station_status = {}    # {(line_id, station_type): "waiting"|"running"|"done"|"error"}

        self.load_config()
        self._build_ui()

    # ═══════════════════════════════════════════
    # 配置持久化
    # ═══════════════════════════════════════════
    def load_config(self):
        if os.path.exists(self.config_file):
            self.config.read(self.config_file, encoding="utf-8")
            self.server_root.set(
                self.config.get("SERVER", "root", fallback="")
            )
            self.date_var.set(
                self.config.get("SERVER", "date", fallback=datetime.date.today().isoformat())
            )
            # 加载线体配置
            for section in self.config.sections():
                if section.startswith("LINE_"):
                    line_id = section[5:]
                    stations_str = self.config.get(section, "stations", fallback="")
                    if stations_str:
                        self.lines[line_id] = {}
                        for st_info in stations_str.split(";"):
                            parts = st_info.split(":")
                            if len(parts) >= 2:
                                st_type = parts[0]
                                enabled = parts[1] == "1" if len(parts) > 1 else True
                                source_dirs = parts[2].split(",") if len(parts) > 2 and parts[2] else []
                                self.lines[line_id][st_type] = {
                                    "enabled": enabled,
                                    "source_dirs": [d for d in source_dirs if d],
                                    "target_root": self.server_root.get(),
                                }

    def save_config(self):
        if "SERVER" not in self.config:
            self.config["SERVER"] = {}
        self.config["SERVER"]["root"] = self.server_root.get()
        self.config["SERVER"]["date"] = self.date_var.get()

        for line_id, stations in self.lines.items():
            section = f"LINE_{line_id}"
            if section not in self.config:
                self.config[section] = {}
            parts = []
            for st_type, st_cfg in stations.items():
                dirs = ",".join(st_cfg.get("source_dirs", []))
                enabled = "1" if st_cfg.get("enabled", True) else "0"
                parts.append(f"{st_type}:{enabled}:{dirs}")
            self.config[section]["stations"] = ";".join(parts)

        with open(self.config_file, "w", encoding="utf-8") as f:
            self.config.write(f)

    # ═══════════════════════════════════════════
    # UI 构建
    # ═══════════════════════════════════════════
    def _build_ui(self):
        # Header
        hdr = tk.Frame(self.root, bg=STYLE["accent"], height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(
            hdr, text="🏭 产线数据采集主控", bg=STYLE["accent"],
            fg="white", font=("Microsoft YaHei", 15, "bold")
        ).pack(side="left", padx=20, pady=10)

        main = tk.Frame(self.root, bg=STYLE["bg"])
        main.pack(fill="both", expand=True, padx=15, pady=(10, 0))

        # ── 左栏：配置 ──
        left = tk.Frame(main, bg=STYLE["bg"])
        left.pack(side="left", fill="y", padx=(0, 10))

        # 服务器路径
        f1 = tk.LabelFrame(
            left, text="🖥 服务器路径", bg=STYLE["card_bg"],
            fg=STYLE["fg"], font=("Microsoft YaHei", 11, "bold"),
            padx=12, pady=10
        )
        f1.pack(fill="x", pady=(0, 8))
        r1 = tk.Frame(f1, bg=STYLE["card_bg"])
        r1.pack(fill="x")
        tk.Entry(
            r1, textvariable=self.server_root, font=("Consolas", 10),
            bg=STYLE["input_bg"], relief="solid", bd=1
        ).pack(side="left", fill="x", expand=True, ipady=3)
        tk.Button(
            r1, text="📂 浏览", command=self._pick_server,
            bg=STYLE["accent"], fg="white", font=("Microsoft YaHei", 9),
            relief="flat", padx=12, pady=3, cursor="hand2"
        ).pack(side="left", padx=(6, 0))

        # 日期
        r_date = tk.Frame(f1, bg=STYLE["card_bg"])
        r_date.pack(fill="x", pady=(8, 0))
        tk.Label(
            r_date, text="采集日期:", bg=STYLE["card_bg"],
            fg=STYLE["text_secondary"], font=("Microsoft YaHei", 10)
        ).pack(side="left")
        tk.Entry(
            r_date, textvariable=self.date_var, width=14,
            font=("Consolas", 10), bg=STYLE["input_bg"],
            relief="solid", bd=1
        ).pack(side="left", padx=(6, 0))
        tk.Label(
            r_date, text="(YYYY-MM-DD)", bg=STYLE["card_bg"],
            fg=STYLE["text_secondary"], font=("Microsoft YaHei", 8)
        ).pack(side="left", padx=(4, 0))

        # 线体/站别管理
        f2 = tk.LabelFrame(
            left, text="🏗 线体 & 站别管理", bg=STYLE["card_bg"],
            fg=STYLE["fg"], font=("Microsoft YaHei", 11, "bold"),
            padx=12, pady=10
        )
        f2.pack(fill="both", expand=True, pady=(0, 8))

        # 树形列表
        tree_frame = tk.Frame(f2, bg=STYLE["card_bg"])
        tree_frame.pack(fill="both", expand=True)
        self.line_tree = ttk.Treeview(
            tree_frame, columns=("info",), show="tree",
            height=10, selectmode="browse"
        )
        self.line_tree.heading("#0", text="线体 / 站别")
        self.line_tree.column("#0", width=200)
        self.line_tree.pack(side="left", fill="both", expand=True)

        scroll_y = ttk.Scrollbar(tree_frame, command=self.line_tree.yview)
        scroll_y.pack(side="right", fill="y")
        self.line_tree.config(yscrollcommand=scroll_y.set)

        # 右键菜单
        self.tree_menu = tk.Menu(self.line_tree, tearoff=0)
        self.tree_menu.add_command(label="➕ 添加站别", command=self._add_station_dialog)
        self.tree_menu.add_command(label="📂 设置源目录", command=self._edit_source_dirs)
        self.tree_menu.add_command(label="✅/❌ 启用/禁用", command=self._toggle_station)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label="🗑 删除", command=self._delete_selected)
        self.line_tree.bind("<Button-3>", self._show_tree_menu)

        # 按钮行
        btn_row = tk.Frame(f2, bg=STYLE["card_bg"])
        btn_row.pack(fill="x", pady=(8, 0))
        tk.Button(
            btn_row, text="➕ 添加线体", command=self._add_line_dialog,
            bg="#ECEFF1", fg=STYLE["fg"], font=("Microsoft YaHei", 9),
            relief="flat", padx=10, pady=2, cursor="hand2"
        ).pack(side="left", padx=(0, 6))

        # 操作按钮
        f3 = tk.Frame(left, bg=STYLE["bg"])
        f3.pack(fill="x", pady=(0, 8))
        self.start_btn = tk.Button(
            f3, text="🚀 开始采集", command=self._start_collection,
            bg=STYLE["accent"], fg="white",
            font=("Microsoft YaHei", 12, "bold"),
            relief="flat", padx=20, pady=10, cursor="hand2"
        )
        self.start_btn.pack(side="left", fill="x", expand=True)
        self.stop_btn = tk.Button(
            f3, text="⏹ 取消监控", command=self._stop_monitoring,
            bg=STYLE["danger"], fg="white",
            font=("Microsoft YaHei", 10),
            relief="flat", padx=12, pady=10, cursor="hand2",
            state="disabled"
        )
        self.stop_btn.pack(side="left", padx=(6, 0))

        # ── 右栏：状态 & 日志 ──
        right = tk.Frame(main, bg=STYLE["bg"])
        right.pack(side="left", fill="both", expand=True)

        # 状态面板
        status_frame = tk.LabelFrame(
            right, text="📊 采集状态", bg=STYLE["card_bg"],
            fg=STYLE["fg"], font=("Microsoft YaHei", 11, "bold"),
            padx=12, pady=10
        )
        status_frame.pack(fill="both", expand=True, pady=(0, 8))

        self.status_tree = ttk.Treeview(
            status_frame,
            columns=("line", "station", "status", "detail"),
            show="headings", height=12
        )
        self.status_tree.heading("line", text="线体")
        self.status_tree.column("line", width=80, anchor="center")
        self.status_tree.heading("station", text="站别")
        self.status_tree.column("station", width=60, anchor="center")
        self.status_tree.heading("status", text="状态")
        self.status_tree.column("status", width=100, anchor="center")
        self.status_tree.heading("detail", text="详情")
        self.status_tree.column("detail", width=200)
        self.status_tree.pack(fill="both", expand=True)

        # 日志
        log_frame = tk.LabelFrame(
            right, text="📋 运行日志", bg=STYLE["card_bg"],
            fg=STYLE["fg"], font=("Microsoft YaHei", 11, "bold"),
            padx=12, pady=10
        )
        log_frame.pack(fill="x", pady=(0, 8))
        self.log_text = tk.Text(
            log_frame, height=8, bg="#1a1a2e", fg="#e2e8f0",
            font=("Consolas", 9), relief="flat", bd=0,
            insertbackground="white", state="disabled"
        )
        self.log_text.pack(fill="both", expand=True)

        # 底部状态栏
        sbar = tk.Frame(self.root, bg=STYLE["card_bg"], height=28)
        sbar.pack(fill="x", side="bottom", pady=(8, 0))
        sbar.pack_propagate(False)
        tk.Label(
            sbar, textvariable=self.status_var, bg=STYLE["card_bg"],
            fg=STYLE["text_secondary"], font=("Microsoft YaHei", 9),
            anchor="w"
        ).pack(side="left", fill="x", padx=12, pady=3)
        self.pb = ttk.Progressbar(
            sbar, variable=self.progress_var, mode="determinate", length=150
        )
        self.pb.pack(side="right", padx=12, pady=2)

        # 刷新树
        self._refresh_line_tree()

    # ═══════════════════════════════════════════
    # 线体/站别树管理
    # ═══════════════════════════════════════════
    def _refresh_line_tree(self):
        for item in self.line_tree.get_children():
            self.line_tree.delete(item)

        for line_id in sorted(self.lines.keys()):
            stations = self.lines[line_id]
            line_node = self.line_tree.insert(
                "", "end", iid=f"L_{line_id}",
                text=f"📁 {line_id}",
                values=(f"{len(stations)} 个站别",)
            )
            for st_type in sorted(stations.keys()):
                st_cfg = stations[st_type]
                enabled = st_cfg.get("enabled", True)
                icon = "✅" if enabled else "❌"
                dirs = st_cfg.get("source_dirs", [])
                info = f"{len(dirs)} 个源目录" if dirs else "未配置源目录"
                self.line_tree.insert(
                    line_node, "end", iid=f"S_{line_id}_{st_type}",
                    text=f"{icon} {st_type}",
                    values=(info,)
                )

    def _show_tree_menu(self, event):
        item = self.line_tree.identify_row(event.y)
        if item:
            self.line_tree.selection_set(item)
            self.tree_menu.post(event.x_root, event.y_root)

    def _get_selected(self):
        sel = self.line_tree.selection()
        if not sel:
            return None, None
        iid = sel[0]
        if iid.startswith("S_"):
            # 站别节点: S_{line_id}_{station_type}
            parts = iid[2:].split("_", 1)
            return parts[0], parts[1]
        elif iid.startswith("L_"):
            # 线体节点
            return iid[2:], None
        return None, None

    def _add_line_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("添加线体")
        dialog.geometry("300x120")
        dialog.configure(bg=STYLE["card_bg"])
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(
            dialog, text="线体编号:", bg=STYLE["card_bg"],
            font=("Microsoft YaHei", 11)
        ).pack(pady=(15, 5))
        entry = tk.Entry(dialog, font=("Consolas", 12), width=20)
        entry.pack(pady=(0, 10))
        entry.focus()

        def confirm():
            line_id = entry.get().strip()
            if not line_id:
                messagebox.showwarning("提示", "请输入线体编号")
                return
            if line_id in self.lines:
                messagebox.showwarning("提示", f"线体 {line_id} 已存在")
                return
            self.lines[line_id] = {}
            self._refresh_line_tree()
            self.save_config()
            dialog.destroy()

        entry.bind("<Return>", lambda e: confirm())
        tk.Button(
            dialog, text="确认", command=confirm,
            bg=STYLE["accent"], fg="white",
            font=("Microsoft YaHei", 10), relief="flat",
            padx=20, pady=5
        ).pack()

    def _add_station_dialog(self):
        line_id, _ = self._get_selected()
        if not line_id:
            # 可能是站别节点，往上找线体
            sel = self.line_tree.selection()
            if sel:
                parent = self.line_tree.parent(sel[0])
                if parent:
                    line_id = parent[2:]
        if not line_id:
            messagebox.showwarning("提示", "请先选择一个线体节点")
            return
        if line_id not in self.lines:
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("添加站别")
        dialog.geometry("300x120")
        dialog.configure(bg=STYLE["card_bg"])
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(
            dialog, text=f"线体 {line_id} — 站别类型:",
            bg=STYLE["card_bg"], font=("Microsoft YaHei", 11)
        ).pack(pady=(15, 5))
        entry = tk.Entry(dialog, font=("Consolas", 12), width=20)
        entry.pack(pady=(0, 10))
        entry.focus()

        def confirm():
            st_type = entry.get().strip()
            if not st_type:
                return
            if st_type in self.lines[line_id]:
                messagebox.showwarning("提示", f"站别 {st_type} 已存在")
                return
            self.lines[line_id][st_type] = {
                "enabled": True,
                "source_dirs": [],
                "target_root": self.server_root.get(),
            }
            self._refresh_line_tree()
            self.save_config()
            dialog.destroy()

        entry.bind("<Return>", lambda e: confirm())
        tk.Button(
            dialog, text="确认", command=confirm,
            bg=STYLE["accent"], fg="white",
            font=("Microsoft YaHei", 10), relief="flat",
            padx=20, pady=5
        ).pack()

    def _edit_source_dirs(self):
        line_id, st_type = self._get_selected()
        if not line_id or not st_type or line_id not in self.lines:
            messagebox.showwarning("提示", "请选择一个站别节点")
            return
        if st_type not in self.lines[line_id]:
            return

        st_cfg = self.lines[line_id][st_type]

        dialog = tk.Toplevel(self.root)
        dialog.title(f"源目录配置 — {line_id}/{st_type}")
        dialog.geometry("550x350")
        dialog.configure(bg=STYLE["card_bg"])
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(
            dialog, text=f"站别 {line_id}/{st_type} 的数据源目录",
            bg=STYLE["card_bg"], font=("Microsoft YaHei", 11, "bold")
        ).pack(pady=(10, 5))

        list_frame = tk.Frame(dialog, bg=STYLE["card_bg"])
        list_frame.pack(fill="both", expand=True, padx=10)

        dir_listbox = tk.Listbox(
            list_frame, font=("Consolas", 9), selectmode=tk.EXTENDED
        )
        dir_listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_frame, command=dir_listbox.yview)
        scroll.pack(side="right", fill="y")
        dir_listbox.config(yscrollcommand=scroll.set)

        for d in st_cfg.get("source_dirs", []):
            dir_listbox.insert(tk.END, d)

        def add_dir():
            path = filedialog.askdirectory(title="选择测试数据源目录")
            if path:
                dir_listbox.insert(tk.END, path.replace("\\", "/"))

        def remove_dir():
            for idx in reversed(dir_listbox.curselection()):
                dir_listbox.delete(idx)

        btn_row = tk.Frame(dialog, bg=STYLE["card_bg"])
        btn_row.pack(pady=8)
        tk.Button(
            btn_row, text="➕ 添加目录", command=add_dir,
            bg=STYLE["accent"], fg="white",
            font=("Microsoft YaHei", 9), relief="flat", padx=8, pady=2
        ).pack(side="left", padx=3)
        tk.Button(
            btn_row, text="🗑 删除选中", command=remove_dir,
            bg=STYLE["danger"], fg="white",
            font=("Microsoft YaHei", 9), relief="flat", padx=8, pady=2
        ).pack(side="left", padx=3)

        def save_dirs():
            dirs = [dir_listbox.get(i) for i in range(dir_listbox.size())]
            st_cfg["source_dirs"] = dirs
            self._refresh_line_tree()
            self.save_config()
            dialog.destroy()
            self._log(f"📂 {line_id}/{st_type} 源目录已更新：{len(dirs)} 个")

        tk.Button(
            dialog, text="💾 保存", command=save_dirs,
            bg=STYLE["success"], fg="white",
            font=("Microsoft YaHei", 10, "bold"), relief="flat",
            padx=20, pady=6
        ).pack(pady=(0, 10))

    def _toggle_station(self):
        line_id, st_type = self._get_selected()
        if not line_id or not st_type or line_id not in self.lines:
            return
        if st_type not in self.lines[line_id]:
            return
        st_cfg = self.lines[line_id][st_type]
        st_cfg["enabled"] = not st_cfg.get("enabled", True)
        self._refresh_line_tree()
        self.save_config()

    def _delete_selected(self):
        line_id, st_type = self._get_selected()
        if st_type and line_id in self.lines and st_type in self.lines[line_id]:
            if messagebox.askyesno("确认", f"删除站别 {line_id}/{st_type}？"):
                del self.lines[line_id][st_type]
                if not self.lines[line_id]:
                    del self.lines[line_id]
                self._refresh_line_tree()
                self.save_config()
        elif line_id and line_id in self.lines:
            if messagebox.askyesno("确认", f"删除线体 {line_id} 及其所有站别？"):
                del self.lines[line_id]
                self._refresh_line_tree()
                self.save_config()

    # ═══════════════════════════════════════════
    # 服务器路径
    # ═══════════════════════════════════════════
    def _pick_server(self):
        path = filedialog.askdirectory(title="选择服务器根目录")
        if path:
            self.server_root.set(path.replace("\\", "/"))
            self.save_config()
            self._log(f"🖥 服务器路径: {path}")

    # ═══════════════════════════════════════════
    # 日志
    # ═══════════════════════════════════════════
    def _log(self, message):
        def append():
            self.log_text.config(state="normal")
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            self.log_text.insert(tk.END, f"[{ts}] {message}\n")
            self.log_text.see(tk.END)
            # 限制 500 行
            lines = int(self.log_text.index('end-1c').split('.')[0])
            if lines > 500:
                self.log_text.delete('1.0', f'{lines - 500}.0')
            self.log_text.config(state="disabled")
        self.root.after(0, append)

    # ═══════════════════════════════════════════
    # 采集主流程
    # ═══════════════════════════════════════════
    def _start_collection(self):
        server = self.server_root.get().strip()
        if not server:
            messagebox.showerror("错误", "请先配置服务器路径")
            return
        if not os.path.exists(server):
            messagebox.showerror("错误", f"服务器路径不存在:\n{server}")
            return
        if not self.lines:
            messagebox.showwarning("提示", "请先添加线体和站别")
            return

        self.save_config()
        tm = TriggerManager(server)
        tm.ensure_trigger_dir()

        # 收集启用站别
        self.pending_stations = []
        self.station_status.clear()

        for line_id, stations in self.lines.items():
            for st_type, st_cfg in stations.items():
                if not st_cfg.get("enabled", True):
                    continue
                # 更新 target_root
                st_cfg["target_root"] = server
                source_paths = tm.build_source_paths(st_cfg, server)
                st_cfg["source_paths"] = source_paths

                self.pending_stations.append((line_id, st_type))
                self.station_status[(line_id, st_type)] = "waiting"

        if not self.pending_stations:
            messagebox.showwarning("提示", "没有启用的站别")
            return

        # 写入所有 trigger
        self._log(f"🚀 开始采集 — {len(self.pending_stations)} 个站别")
        written = tm.write_all_triggers(self.lines, log_cb=self._log)

        # 刷新状态面板
        self._refresh_status()

        # 切到监控模式
        self.monitoring = True
        self.start_btn.config(state="disabled", text="⏳ 监控中...")
        self.stop_btn.config(state="normal")
        self.status_var.set(f"等待 {len(self.pending_stations)} 个站别响应...")
        self.progress_var.set(0)

        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(tm,),
            daemon=True
        )
        self.monitor_thread.start()

    def _monitor_loop(self, tm):
        total = len(self.pending_stations)
        timeout = 600  # 10 分钟超时
        start_time = time.time()

        while self.monitoring and self.pending_stations:
            all_done_now = []
            for line_id, st_type in list(self.pending_stations):
                done = tm.check_done(line_id, st_type)
                if done:
                    files = done.get("files_copied", 0)
                    errors = done.get("errors", 0)
                    status = "done" if errors == 0 else "error"
                    self.station_status[(line_id, st_type)] = status
                    all_done_now.append((line_id, st_type, files, errors))
                    self.pending_stations.remove((line_id, st_type))
                    self._log(
                        f"✅ {line_id}/{st_type} 完成"
                        + (f" ({files} 文件)" if files else "")
                        + (f" ⚠️ {errors} 异常" if errors else "")
                    )

            done_count = total - len(self.pending_stations)
            self.root.after(0, lambda: self.progress_var.set(
                done_count / total * 90
            ))
            self.root.after(0, self._refresh_status)

            if not self.pending_stations:
                break

            # 超时检查
            if time.time() - start_time > timeout:
                self._log(f"⚠️ 超时 — {len(self.pending_stations)} 个站别无响应:")
                for line_id, st_type in self.pending_stations:
                    self.station_status[(line_id, st_type)] = "error"
                    self._log(f"  ❌ {line_id}/{st_type} 超时")
                self.root.after(0, self._refresh_status)
                self.root.after(0, self._on_collection_done)
                return

            self.root.after(0, lambda: self.status_var.set(
                f"收集中... {done_count}/{total} 完成"
            ))
            time.sleep(POLL_INTERVAL)

        # 全部完成
        self.root.after(0, lambda: self.status_var.set("✅ 全部采集完成！开始分析..."))
        self.root.after(0, self._refresh_status)
        self.root.after(0, self._on_collection_done)

    def _on_collection_done(self):
        self.monitoring = False
        self.stop_btn.config(state="disabled")

        # 检查是否有成功完成的站别
        has_data = any(
            v == "done" for v in self.station_status.values()
        )
        if not has_data:
            self.start_btn.config(state="normal", text="🚀 重新采集")
            self.status_var.set("❌ 所有站别均失败，请检查配置")
            return

        # 自动跑分析
        self._run_analysis()

    def _run_analysis(self):
        """采集完成后自动跑 Analyzer"""
        date_str = self.date_var.get()
        server = self.server_root.get()

        # 构建数据路径：server/2026-06-23/
        data_root = os.path.join(server, date_str)

        if not os.path.exists(data_root):
            self._log(f"⚠️ 数据目录不存在: {data_root}")
            self.start_btn.config(state="normal", text="🚀 重新采集")
            self.status_var.set("数据目录不存在，请检查日期")
            return

        # 输出目录（本地）
        local_output = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "reports", date_str
        )

        self._log(f"🔍 开始分析 {data_root} ...")
        self.progress_var.set(95)

        def analysis_thread():
            try:
                bridge = AnalyzerBridge()
                html_path, analysis = bridge.run_analysis(
                    data_root, local_output, log_cb=self._log
                )
                self.root.after(0, lambda: self.progress_var.set(100))
                self.root.after(0, lambda: self.status_var.set(
                    f"✅ 报告已生成"
                ))

                # 弹出报告
                if html_path and os.path.exists(html_path):
                    self._log(f"📄 打开报告: {html_path}")
                    try:
                        if sys.platform == "win32":
                            os.startfile(html_path)
                        elif sys.platform == "darwin":
                            subprocess.run(["open", html_path])
                        else:
                            subprocess.run(["xdg-open", html_path])
                    except Exception:
                        self._log("⚠️ 无法自动打开报告，请手动打开")

                # 同时复制报告到服务器
                try:
                    import shutil
                    server_report_dir = os.path.join(server, "reports")
                    os.makedirs(server_report_dir, exist_ok=True)
                    shutil.copy2(
                        html_path,
                        os.path.join(server_report_dir, f"{date_str}_report.html")
                    )
                    self._log(f"📤 报告已同步到服务器")
                except Exception as e:
                    self._log(f"⚠️ 报告同步到服务器失败: {e}")

            except Exception as e:
                self._log(f"❌ 分析失败: {e}")
                import traceback
                self._log(traceback.format_exc())
                self.root.after(0, lambda: self.status_var.set(f"❌ 分析失败: {e}"))

            finally:
                self.root.after(0, lambda: self.start_btn.config(
                    state="normal", text="🚀 重新采集"
                ))

        threading.Thread(target=analysis_thread, daemon=True).start()

    def _stop_monitoring(self):
        self.monitoring = False
        self.stop_btn.config(state="disabled")
        self.start_btn.config(state="normal", text="🚀 开始采集")
        self.status_var.set("已取消监控")
        self._log("⏹ 用户取消监控")

    # ═══════════════════════════════════════════
    # 状态面板
    # ═══════════════════════════════════════════
    def _refresh_status(self):
        for item in self.status_tree.get_children():
            self.status_tree.delete(item)

        for (line_id, st_type), status in sorted(self.station_status.items()):
            if status == "waiting":
                icon = "⏳ 等待中"
                tag = ""
            elif status == "done":
                icon = "✅ 完成"
                tag = "done"
            elif status == "error":
                icon = "❌ 失败"
                tag = "error"
            else:
                icon = f"🔄 {status}"
                tag = ""

            st_cfg = self.lines.get(line_id, {}).get(st_type, {})
            dirs = st_cfg.get("source_dirs", [])
            detail = ", ".join([os.path.basename(d) for d in dirs]) if dirs else ""

            self.status_tree.insert(
                "", "end",
                values=(line_id, st_type, icon, detail),
                tags=(tag,)
            )

        # 颜色标记
        self.status_tree.tag_configure("done", foreground=STYLE["success"])
        self.status_tree.tag_configure("error", foreground=STYLE["danger"])


# ═══════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════
def main():
    root = tk.Tk()
    MasterControlApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
