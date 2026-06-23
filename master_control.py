#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
产线数据采集主控面板 v2.1
=======================
Trigger 路径一目了然，写在明处。
"""
import os, sys, json, threading, time, subprocess, configparser, datetime
from tkinter import ttk, filedialog, messagebox
import tkinter as tk

STYLE = {
    "bg": "#f5f6fa", "card": "#ffffff",
    "accent": "#2563EB", "success": "#10B981",
    "danger": "#EF4444", "warning": "#F59E0B",
    "text": "#1e293b", "sub": "#64748b", "border": "#e2e8f0",
}

POLL = 3; TIMEOUT = 600


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
        if not os.path.exists(p): return None
        try:
            with open(p, encoding="utf-8") as f: return json.load(f)
        except: return None


class AnalyzerBridge:
    @staticmethod
    def run(data_path, out_dir, log_cb):
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import at_analyzer as aa
            log_cb("🔍 解析数据中...")
            r, sk, sf = aa.parse_source(data_path)
            if not r: raise ValueError("无有效记录")
            log_cb(f"📊 {len(r)} 行, {len(sf)} 站别")
            a = aa.analyze(r)
            os.makedirs(out_dir, exist_ok=True)
            hp = aa.make_html(a, out_dir, os.path.join(out_dir, "report.html"), os.path.basename(data_path))
            log_cb(f"✅ {hp}")
            return hp, a
        except ImportError:
            log_cb("⚠️ 子进程调用 Analyzer...")
            an = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AT-Audio-Test-Analyzer", "at_analyzer.py")
            code = f'''
import sys; sys.path.insert(0, r"{os.path.dirname(an)}")
import at_analyzer as aa
r,sk,sf = aa.parse_source(r"{data_path}")
a = aa.analyze(r)
hp = aa.make_html(a, r"{out_dir}", r"{os.path.join(out_dir, 'report.html')}", r"{os.path.basename(data_path)}")
print("OK:"+hp)
'''
            res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
            if res.returncode == 0 and "OK:" in res.stdout:
                return res.stdout.split("OK:")[1].strip(), None
            raise RuntimeError(res.stderr)


class App:
    def __init__(self, root):
        self.root = root
        root.title("产线数据采集主控 v2.1")
        root.geometry("1020x740")
        root.minsize(920, 600)
        root.configure(bg=STYLE["bg"])
        try:
            from ctypes import windll; windll.shcore.SetProcessDpiAwareness(1)
        except: pass

        app_dir = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
        self.cfg_file = os.path.join(app_dir, "config.ini")
        self.cfg = configparser.ConfigParser()
        self.server = tk.StringVar()
        self.date = tk.StringVar(value=datetime.date.today().isoformat())
        self.stations = []
        self.monitoring = False; self.pending = []; self.status = {}; self.last_report = None
        self.load_cfg()
        self._build()
        self._refresh_table()

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
        if "SERVER" not in self.cfg: self.cfg["SERVER"] = {}
        self.cfg["SERVER"]["root"] = self.server.get()
        self.cfg["SERVER"]["date"] = self.date.get()
        for s in list(self.cfg.sections()):
            if s.startswith("STATION_"): self.cfg.remove_section(s)
        for i, st in enumerate(self.stations):
            sec = f"STATION_{i}"; self.cfg[sec] = {
                "line": st["line"], "station": st["station"],
                "dirs": json.dumps(st.get("dirs", []), ensure_ascii=False),
                "enabled": str(st.get("enabled", True)),
            }
        with open(self.cfg_file, "w", encoding="utf-8") as f: self.cfg.write(f)

    def _trigger_dir(self):
        """Trigger 写入路径"""
        s = self.server.get().strip()
        return os.path.join(s, "trigger").replace("\\", "/") if s else "（请先设置服务器路径）"

    def _trigger_example(self, line, st):
        """某站别的完整 trigger 路径示例"""
        s = self.server.get().strip()
        if not s: return ""
        return os.path.join(s, "trigger", f"line_{line}", f"{st}_cmd.json").replace("\\", "/")

    def _build(self):
        # ── 顶栏 ──
        top = tk.Frame(self.root, bg=STYLE["accent"], height=50)
        top.pack(fill="x"); top.pack_propagate(False)
        tk.Label(top, text="🏭 产线数据采集主控 v2.1", bg=STYLE["accent"], fg="white",
                 font=("Microsoft YaHei", 15, "bold")).pack(side="left", padx=16, pady=10)

        # ═══════════════════════════════════════════
        #  SECTION 1: 服务器 & Trigger 路径
        # ═══════════════════════════════════════════
        s1 = tk.LabelFrame(self.root, text=" 📡 服务器 & Trigger 写入路径 ", bg=STYLE["card"],
                           fg=STYLE["text"], font=("Microsoft YaHei", 12, "bold"),
                           padx=12, pady=10)
        s1.pack(fill="x", padx=12, pady=(10, 0))

        # 行1: 服务器
        r1 = tk.Frame(s1, bg=STYLE["card"])
        r1.pack(fill="x", pady=(0, 6))
        tk.Label(r1, text="服务器根目录", bg=STYLE["card"], fg=STYLE["text"],
                 font=("Microsoft YaHei", 10), width=12, anchor="e").pack(side="left", padx=(0, 6))
        tk.Entry(r1, textvariable=self.server, font=("Consolas", 10), width=55,
                 bg="#f8fafc", relief="solid", bd=1).pack(side="left", ipady=2)
        tk.Button(r1, text="浏览", command=lambda: self._browse(self.server),
                  bg=STYLE["accent"], fg="white", font=("Microsoft YaHei", 9),
                  relief="flat", padx=10, cursor="hand2").pack(side="left", padx=4)
        tk.Label(r1, text="📅", bg=STYLE["card"], font=("Microsoft YaHei", 10)).pack(side="left", padx=(12, 2))
        tk.Entry(r1, textvariable=self.date, font=("Consolas", 10), width=12,
                 bg="#f8fafc", relief="solid", bd=1).pack(side="left", ipady=2)
        tk.Button(r1, text="今天", command=lambda: self.date.set(datetime.date.today().isoformat()),
                  bg="#e2e8f0", fg=STYLE["text"], font=("Microsoft YaHei", 8),
                  relief="flat", padx=6, cursor="hand2").pack(side="left", padx=2)

        # 行2: Trigger 写入路径（醒目）
        r2 = tk.Frame(s1, bg=STYLE["card"])
        r2.pack(fill="x")
        tk.Label(r2, text="▶ Trigger 写入", bg=STYLE["card"], fg=STYLE["text"],
                 font=("Microsoft YaHei", 10), width=12, anchor="e").pack(side="left", padx=(0, 6))
        self.trigger_label = tk.Label(r2, text="", bg="#EFF6FF", fg=STYLE["accent"],
                                       font=("Consolas", 10, "bold"), anchor="w",
                                       relief="solid", bd=1, padx=10, pady=3)
        self.trigger_label.pack(side="left", fill="x", expand=True)
        # 绑定 server 变更时刷新
        self.server.trace_add("write", lambda *a: self._update_trigger_display())

        # ═══════════════════════════════════════════
        #  Notebook: 三 Tab
        # ═══════════════════════════════════════════
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=12, pady=8)

        # === Tab1: 站别管理 ===
        t1 = tk.Frame(nb, bg=STYLE["bg"]); nb.add(t1, text="  ⚙️ 站别管理  ")

        # 添加行
        ab = tk.Frame(t1, bg=STYLE["card"]); ab.pack(fill="x", pady=(0, 6))
        tk.Label(ab, text="添加站别 → 线体:", bg=STYLE["card"], font=("Microsoft YaHei", 10)).pack(side="left", padx=(8, 2), pady=6)
        self.al = ttk.Combobox(ab, values=["A03","A05","A07","Line_1","Line_2"], width=7, font=("Consolas",10))
        self.al.pack(side="left", padx=2, pady=6); self.al.set("A03")
        tk.Label(ab, text="站别:", bg=STYLE["card"], font=("Microsoft YaHei",10)).pack(side="left", padx=(4,2), pady=6)
        self.as_ = ttk.Combobox(ab, values=["AT","FT","QA"], width=5, font=("Consolas",10))
        self.as_.pack(side="left", padx=2, pady=6); self.as_.set("AT")
        tk.Button(ab, text="➕ 添加", command=self._add_station,
                  bg=STYLE["accent"], fg="white", font=("Microsoft YaHei",10),
                  relief="flat", padx=14, cursor="hand2").pack(side="left", padx=8, pady=4)

        # 表格
        tf = tk.Frame(t1, bg=STYLE["card"]); tf.pack(fill="both", expand=True)
        cols = ("#","线体","站别","数据源目录","启用","Trigger 路径","")
        self.tbl = ttk.Treeview(tf, columns=cols, show="headings", height=14)
        self.tbl.heading("#",text="#"); self.tbl.column("#",width=30,anchor="center")
        self.tbl.heading("线体",text="线体"); self.tbl.column("线体",width=60,anchor="center")
        self.tbl.heading("站别",text="站别"); self.tbl.column("站别",width=50,anchor="center")
        self.tbl.heading("数据源目录",text="数据源目录"); self.tbl.column("数据源目录",width=280)
        self.tbl.heading("启用",text="启用"); self.tbl.column("启用",width=40,anchor="center")
        self.tbl.heading("Trigger 路径",text="⟶ Trigger 写入位置（客户端需监控此路径）")
        self.tbl.column("Trigger 路径",width=320)
        self.tbl.heading("",text=""); self.tbl.column("",width=170)
        self.tbl.pack(side="left",fill="both",expand=True)
        ttk.Scrollbar(tf,command=self.tbl.yview).pack(side="right",fill="y"); self.tbl.config(yscrollcommand=lambda *a: None)
        self.tbl.bind("<Button-1>", self._tbl_click)
        self.tbl.tag_configure("on", foreground=STYLE["success"])
        self.tbl.tag_configure("off", foreground=STYLE["sub"])

        # === Tab2: 采集监控 ===
        t2 = tk.Frame(nb, bg=STYLE["bg"]); nb.add(t2, text="  🚀 采集监控  ")

        br = tk.Frame(t2, bg=STYLE["bg"]); br.pack(fill="x", pady=(6,10))
        self.go_btn = tk.Button(br, text="🚀 开始采集全部站别", command=self._start,
                                 bg=STYLE["accent"], fg="white",
                                 font=("Microsoft YaHei", 14, "bold"),
                                 relief="flat", padx=30, pady=12, cursor="hand2")
        self.go_btn.pack(fill="x", ipady=2)

        self.prog = ttk.Progressbar(t2, mode="determinate"); self.prog.pack(fill="x")
        self.pl = tk.Label(t2, text="就绪", bg=STYLE["bg"], fg=STYLE["sub"], font=("Microsoft YaHei",10))
        self.pl.pack(pady=(2,6))

        sf = tk.Frame(t2, bg=STYLE["card"]); sf.pack(fill="both", expand=True)
        scols = ("线体","站别","状态","详情")
        self.st_tbl = ttk.Treeview(sf, columns=scols, show="headings", height=10)
        self.st_tbl.heading("线体",text="线体"); self.st_tbl.column("线体",width=80,anchor="center")
        self.st_tbl.heading("站别",text="站别"); self.st_tbl.column("站别",width=80,anchor="center")
        self.st_tbl.heading("状态",text="状态"); self.st_tbl.column("状态",width=120,anchor="center")
        self.st_tbl.heading("详情",text="详情"); self.st_tbl.column("详情",width=380)
        self.st_tbl.pack(side="left",fill="both",expand=True)
        ttk.Scrollbar(sf,command=self.st_tbl.yview).pack(side="right",fill="y")
        self.st_tbl.tag_configure("ok",foreground=STYLE["success"])
        self.st_tbl.tag_configure("err",foreground=STYLE["danger"])
        self.st_tbl.tag_configure("wait",foreground=STYLE["warning"])

        btnr = tk.Frame(t2, bg=STYLE["bg"]); btnr.pack(fill="x", pady=(8,0))
        self.rpt_btn = tk.Button(btnr, text="📄 打开最新报告", command=self._open_report,
                                  bg=STYLE["success"], fg="white",
                                  font=("Microsoft YaHei", 11, "bold"),
                                  relief="flat", padx=20, pady=8, cursor="hand2", state="disabled")
        self.rpt_btn.pack(side="left", padx=(0,6))
        self.rd_btn = tk.Button(btnr, text="📂 打开输出目录", command=self._open_outdir,
                                 bg="#e2e8f0", fg=STYLE["text"],
                                 font=("Microsoft YaHei", 10),
                                 relief="flat", padx=16, pady=8, cursor="hand2", state="disabled")
        self.rd_btn.pack(side="left")

        # === Tab3: 日志 ===
        t3 = tk.Frame(nb, bg=STYLE["bg"]); nb.add(t3, text="  📋 日志  ")
        ltop = tk.Frame(t3, bg=STYLE["bg"]); ltop.pack(fill="x", pady=(0,2))
        tk.Button(ltop, text="清空", command=self._clear_log,
                  bg="#e2e8f0", fg=STYLE["text"], font=("Microsoft YaHei",9),
                  relief="flat", padx=10).pack(side="right")
        self.log = tk.Text(t3, bg="#1e293b", fg="#e2e8f0", insertbackground="white",
                           font=("Consolas",9), relief="flat", bd=0, state="disabled")
        self.log.pack(fill="both", expand=True)

        # 状态栏
        sb = tk.Frame(self.root, bg=STYLE["card"], height=24); sb.pack(fill="x", side="bottom"); sb.pack_propagate(False)
        self.sb = tk.Label(sb, text="就绪 — 设置服务器路径并添加站别", bg=STYLE["card"],
                           fg=STYLE["sub"], font=("Microsoft YaHei",9), anchor="w")
        self.sb.pack(side="left", fill="x", padx=12, pady=2)

        self._update_trigger_display()

    def _update_trigger_display(self):
        td = self._trigger_dir()
        self.trigger_label.config(text=f"  {td}/line_{{线体}}/{{站别}}_cmd.json  ")
        self._refresh_table()

    # ═══ 站别表格 ═══
    def _refresh_table(self):
        for i in self.tbl.get_children(): self.tbl.delete(i)
        for idx, st in enumerate(self.stations, 1):
            dirs_text = "  |  ".join(st.get("dirs", [])) if st.get("dirs") else "（点击编辑）"
            en = "✅" if st.get("enabled", True) else "❌"
            tag = "on" if st.get("enabled", True) else "off"
            trigger_path = self._trigger_example(st["line"], st["station"]) or "—"
            iid = self.tbl.insert("", "end",
                                   values=(idx, st["line"], st["station"], dirs_text, en, trigger_path,
                                           "📂编辑  🔄切换  🗑删除"),
                                   tags=(tag,))

    def _add_station(self):
        l, s = self.al.get().strip(), self.as_.get().strip()
        if not l or not s: return
        if any(x["line"]==l and x["station"]==s for x in self.stations):
            messagebox.showwarning("重复", f"{l}/{s} 已存在"); return
        self.stations.append({"line": l, "station": s, "dirs": [], "enabled": True})
        self._refresh_table(); self.save_cfg(); self._sb(f"已添加 {l}/{s}")

    def _tbl_click(self, event):
        item = self.tbl.identify_row(event.y)
        col = int(self.tbl.identify_column(event.x).replace("#","")) - 1
        if not item: return
        idx = int(self.tbl.index(item))
        if idx >= len(self.stations): return
        st = self.stations[idx]
        if col == 6:
            x = event.x
            if x < 85: self._edit_dirs(idx)
            elif x < 150:
                st["enabled"] = not st.get("enabled", True)
                self._refresh_table(); self.save_cfg()
            else:
                if messagebox.askyesno("确认", f"删除 {st['line']}/{st['station']}？"):
                    del self.stations[idx]; self._refresh_table(); self.save_cfg()

    def _edit_dirs(self, idx):
        st = self.stations[idx]
        dlg = tk.Toplevel(self.root)
        dlg.title(f"源目录 — {st['line']}/{st['station']}")
        dlg.geometry("620x400"); dlg.configure(bg=STYLE["card"])
        dlg.transient(self.root); dlg.grab_set()

        tk.Label(dlg, text=f"{st['line']} 线 — {st['station']} 站  数据源目录",
                 bg=STYLE["card"], font=("Microsoft YaHei", 12, "bold"), fg=STYLE["text"]).pack(pady=(10,0))
        # 显示 trigger 路径
        tp = self._trigger_example(st["line"], st["station"])
        tk.Label(dlg, text=f"⟶ Trigger: {tp}", bg=STYLE["card"],
                 fg=STYLE["accent"], font=("Consolas", 9)).pack(pady=(2,6))

        lf = tk.Frame(dlg, bg=STYLE["card"]); lf.pack(fill="both", expand=True, padx=10)
        lb = tk.Listbox(lf, font=("Consolas",9), selectmode=tk.EXTENDED, bg="#f8fafc", height=10)
        lb.pack(side="left", fill="both", expand=True)
        sc = ttk.Scrollbar(lf, command=lb.yview); sc.pack(side="right", fill="y")
        for d in st.get("dirs",[]): lb.insert(tk.END, d)

        bf = tk.Frame(dlg, bg=STYLE["card"]); bf.pack(pady=6)
        tk.Button(bf, text="➕ 添加目录", command=lambda: (p := filedialog.askdirectory()) and lb.insert(tk.END, p.replace("\\","/")),
                  bg=STYLE["accent"], fg="white", font=("Microsoft YaHei",10),
                  relief="flat", padx=12, pady=3).pack(side="left", padx=3)
        tk.Button(bf, text="🗑 删除选中", command=lambda: [lb.delete(i) for i in reversed(lb.curselection())],
                  bg=STYLE["danger"], fg="white", font=("Microsoft YaHei",10),
                  relief="flat", padx=12, pady=3).pack(side="left", padx=3)

        def save():
            st["dirs"] = [lb.get(i) for i in range(lb.size())]
            self._refresh_table(); self.save_cfg(); dlg.destroy()
            self._log(f"📂 {st['line']}/{st['station']} 目录已更新 ({len(st['dirs'])}个)")
        tk.Button(dlg, text="💾 保存", command=save, bg=STYLE["success"], fg="white",
                  font=("Microsoft YaHei",11,"bold"), relief="flat", padx=24, pady=6).pack(pady=(0,10))

    # ═══ 采集 ═══
    def _start(self):
        srv = self.server.get().strip()
        if not srv or not os.path.exists(srv):
            messagebox.showerror("错误", f"服务器路径不存在:\n{srv}"); return
        enabled = [s for s in self.stations if s.get("enabled",True) and s.get("dirs")]
        if not enabled:
            messagebox.showwarning("提示", "没有已启用且配置了源目录的站别"); return
        self.save_cfg()
        self.monitoring = True
        self.go_btn.config(state="disabled", text="⏳ 采集中...", bg=STYLE["sub"])
        self.pending = [(s["line"], s["station"]) for s in enabled]
        self.status.clear()
        for l,st in self.pending: self.status[(l,st)] = "waiting"
        self._refresh_st(); self.prog["value"]=0; self.pl.config(text="写入 trigger 指令中...")
        tm = TriggerManager(srv)
        os.makedirs(tm.dir, exist_ok=True)
        for s in enabled:
            p = tm.write(s["line"], s["station"], srv, s["dirs"])
            self._log(f"📤 {s['line']}/{s['station']} → {p}")
        self._log(f"🚀 {len(enabled)} 个站别已触发，等待机台响应...")
        self._sb(f"等待 {len(enabled)} 个站别...")
        threading.Thread(target=self._monitor, args=(tm, len(enabled)), daemon=True).start()

    def _monitor(self, tm, total):
        start = time.time()
        while self.monitoring and self.pending:
            for line, st in list(self.pending):
                d = tm.check(line, st)
                if d:
                    self.status[(line,st)] = "done" if d.get("errors",0)==0 else "error"
                    self._log(f"{'✅' if d.get('errors',0)==0 else '⚠️'} {line}/{st} — {d.get('files_copied',0)}文件"
                              + (f", {d.get('errors',0)}异常" if d.get('errors',0) else ""))
                    self.pending.remove((line,st))
            done = total - len(self.pending)
            self.root.after(0, lambda: self.prog["value"] == done/total*90)
            self.root.after(0, lambda: self.pl.config(text=f"{done}/{total} 站别完成"))
            self.root.after(0, self._refresh_st)
            self.root.after(0, lambda: self._sb(f"收集中 {done}/{total}"))
            if not self.pending: break
            if time.time()-start > TIMEOUT:
                for l,st in self.pending:
                    self.status[(l,st)]="error"; self._log(f"❌ {l}/{st} 超时")
                self.root.after(0, self._refresh_st); self.root.after(0, self._done); return
            time.sleep(POLL)
        self.root.after(0, self._done)

    def _done(self):
        self.monitoring=False
        self.go_btn.config(state="normal", text="🚀 重新采集", bg=STYLE["accent"])
        self._refresh_st()
        if any(v=="done" for v in self.status.values()):
            self.pl.config(text="采集完成，开始分析..."); self.prog["value"]=92
            self._run_analysis()
        else:
            self.pl.config(text="❌ 全部失败"); self._sb("全部失败")

    def _run_analysis(self):
        srv, ds = self.server.get(), self.date.get()
        data_root = os.path.join(srv, ds)
        if not os.path.exists(data_root):
            try:
                dirs = sorted([d for d in os.listdir(srv) if os.path.isdir(os.path.join(srv,d)) and len(d)==10 and d[4]=='-'], reverse=True)
                if dirs: data_root = os.path.join(srv, dirs[0]); self._log(f"⚠️ 使用最新日期: {dirs[0]}")
            except: pass
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", ds)
        self._log(f"🔍 分析: {data_root}")
        def work():
            try:
                hp, a = AnalyzerBridge.run(data_root, out, self._log)
                self.last_report = hp
                self.root.after(0, lambda: [self.prog["value"], self.pl.config(text="✅ 完成！")])
                self.root.after(0, lambda: self.rpt_btn.config(state="normal"))
                self.root.after(0, lambda: self.rd_btn.config(state="normal"))
                self.root.after(0, lambda: self._sb("✅ 报告已生成"))
                try:
                    import shutil; rd=os.path.join(srv,"reports"); os.makedirs(rd,exist_ok=True)
                    shutil.copy2(hp, os.path.join(rd,f"{ds}_report.html"))
                    self._log(f"📤 报告→服务器: {rd}")
                except Exception as e: self._log(f"⚠️ 同步失败: {e}")
                if hp and os.path.exists(hp):
                    try:
                        if sys.platform=="win32": os.startfile(hp)
                        elif sys.platform=="darwin": subprocess.run(["open",hp])
                        else: subprocess.run(["xdg-open",hp])
                    except: pass
            except Exception as e:
                import traceback; self._log(f"❌ {e}"); self._log(traceback.format_exc())
                self.root.after(0, lambda: [self.pl.config(text=f"❌ {e}"), self._sb(f"❌ {e}")])
        threading.Thread(target=work, daemon=True).start()

    def _refresh_st(self):
        for i in self.st_tbl.get_children(): self.st_tbl.delete(i)
        for (l,st), s in sorted(self.status.items()):
            icons={"waiting":("⏳ 等待中","wait"),"done":("✅ 完成","ok"),"error":("❌ 失败","err")}
            ic,tg = icons.get(s,("🔄",""))
            cfg=next((x for x in self.stations if x["line"]==l and x["station"]==st),{})
            dt=", ".join([os.path.basename(d) for d in cfg.get("dirs",[])]) if cfg.get("dirs") else ""
            self.st_tbl.insert("","end",values=(l,st,ic,dt),tags=(tg,))

    def _open_report(self):
        if self.last_report and os.path.exists(self.last_report):
            try:
                if sys.platform=="win32": os.startfile(self.last_report)
                elif sys.platform=="darwin": subprocess.run(["open",self.last_report])
                else: subprocess.run(["xdg-open",self.last_report])
            except: messagebox.showinfo("提示", f"报告:\n{self.last_report}")
        else: messagebox.showinfo("提示", "暂无报告")

    def _open_outdir(self):
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", self.date.get())
        if os.path.exists(d):
            try:
                if sys.platform=="win32": os.startfile(d)
                else: subprocess.run(["xdg-open",d])
            except: pass
        else: messagebox.showinfo("提示", "目录不存在")

    def _browse(self, var):
        p = filedialog.askdirectory(); p and var.set(p.replace("\\","/")) or None; self.save_cfg()

    def _log(self, msg):
        def do():
            self.log.config(state="normal")
            self.log.insert(tk.END, f"[{datetime.datetime.now():%H:%M:%S}] {msg}\n"); self.log.see(tk.END)
            n=int(self.log.index('end-1c').split('.')[0])
            if n>500: self.log.delete('1.0',f'{n-500}.0')
            self.log.config(state="disabled")
        self.root.after(0, do)

    def _clear_log(self):
        self.log.config(state="normal"); self.log.delete("1.0",tk.END); self.log.config(state="disabled")

    def _sb(self, msg): self.sb.config(text=msg)


def main():
    root = tk.Tk(); App(root); root.mainloop()

if __name__ == "__main__": main()
