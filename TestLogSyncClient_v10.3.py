#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
产线测试数据自动汇总客户端 v10.3 (Trigger Edition)
===============================================
基于 v10.2，新增显式 Trigger 监控路径配置。
不再从路径池推导——用户一目了然看到监控路径。
"""
import os, sys, shutil, configparser, threading, time, json
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import pystray
from PIL import Image, ImageDraw

MAX_LOG = 500; MAX_RETRY = 3; RETRY_DELAY = 5; PROG_INT = 50; TRIG_CHECK = 10


class App:
    def __init__(self, root):
        self.root = root
        root.title("产线测试数据自动汇总客户端 v10.3")
        root.geometry("720x740")
        root.resizable(False, False)
        try:
            from ctypes import windll; windll.shcore.SetProcessDpiAwareness(1)
        except: pass

        ap = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
        self.cfg_file = os.path.join(ap, "config.ini")
        self.cfg = configparser.ConfigParser()

        self.line_var = tk.StringVar(value="A03")
        self.station_var = tk.StringVar(value="AT")
        self.device_var = tk.StringVar(value="AT_01")
        self.source_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.trigger_root_var = tk.StringVar()  # 🆕 显式 Trigger 监控路径
        self.trigger_path_label_var = tk.StringVar(value="（请先设置 Trigger 监控根路径）")
        self.hour_var = tk.StringVar(value="16")
        self.min_var = tk.StringVar(value="30")
        self.running = False
        self.pool = []
        self.trig_mon = False
        self.trig_thread = None

        self.load_cfg(); self._build(); self.refresh_pool()
        root.protocol('WM_DELETE_WINDOW', self._tray)

    # ═══ 配置 ═══
    def load_cfg(self):
        if os.path.exists(self.cfg_file):
            self.cfg.read(self.cfg_file, encoding='utf-8')
            self.line_var.set(self.cfg.get("STATION","line",fallback="A03"))
            self.station_var.set(self.cfg.get("STATION","station",fallback="AT"))
            self.device_var.set(self.cfg.get("STATION","device",fallback="AT_01"))
            self.trigger_root_var.set(self.cfg.get("TRIGGER","root",fallback=""))  # 🆕
            self.hour_var.set(self.cfg.get("TIMER","hour",fallback="16"))
            self.min_var.set(self.cfg.get("TIMER","minute",fallback="30"))
            try: self.pool = json.loads(self.cfg.get("PATH","pool",fallback="[]"))
            except: self.pool = []
        self._update_trigger_display()

    def save_cfg(self):
        for sec, items in [("STATION",[("line",self.line_var),("station",self.station_var),("device",self.device_var)]),
                            ("TRIGGER",[("root",self.trigger_root_var)]),  # 🆕
                            ("TIMER",[("hour",self.hour_var),("minute",self.min_var)]),
                            ("PATH",[("pool",json.dumps(self.pool))])]:
            if sec not in self.cfg: self.cfg[sec] = {}
            for k,v in items:
                self.cfg[sec][k] = v.get().strip() if hasattr(v,'get') else v
        with open(self.cfg_file,"w",encoding="utf-8") as f: self.cfg.write(f)
        return True

    # ═══ Trigger 路径显示 ═══
    def _trigger_full_path(self):
        """显示完整监控路径: {root}/trigger/line_A03/AT_cmd.json"""
        r = self.trigger_root_var.get().strip()
        if not r: return "（未设置）"
        l = self.line_var.get().strip()
        s = self.station_var.get().strip()
        return os.path.join(r, "trigger", f"line_{l}", f"{s}_cmd.json").replace("\\", "/")

    def _update_trigger_display(self):
        self.trigger_path_label_var.set(f"⟶ 将监控: {self._trigger_full_path()}")

    # ═══ UI ═══
    def _build(self):
        mf = ttk.Frame(self.root, padding="12"); mf.pack(fill="both", expand=True)

        # ── 1. 机台信息 ──
        f1 = ttk.LabelFrame(mf, text=" 1. 机台信息 ", padding="8"); f1.pack(fill="x", pady=(0,6))

        r1 = ttk.Frame(f1); r1.pack(fill="x")
        ttk.Label(r1, text="线别:").grid(row=0,column=0,sticky="w",padx=2,pady=3)
        ttk.Combobox(r1, textvariable=self.line_var, values=["A03","A05","A07","Line_1","Line_2","Line_3"],
                     width=8).grid(row=0,column=1,sticky="w",padx=4,pady=3)
        ttk.Label(r1, text="站别:").grid(row=0,column=2,sticky="w",padx=(12,2),pady=3)
        ttk.Combobox(r1, textvariable=self.station_var, values=["AT","FT","QA"],
                     width=6).grid(row=0,column=3,sticky="w",padx=4,pady=3)
        ttk.Label(r1, text="机台编号:").grid(row=0,column=4,sticky="w",padx=(12,2),pady=3)
        ttk.Entry(r1, textvariable=self.device_var, width=10).grid(row=0,column=5,sticky="w",padx=4,pady=3)

        ttk.Label(r1, text="每日定时:").grid(row=1,column=0,sticky="w",padx=2,pady=3)
        tf = ttk.Frame(r1); tf.grid(row=1,column=1,columnspan=3,sticky="w",pady=3)
        ttk.Combobox(tf, textvariable=self.hour_var, values=[f"{i:02d}" for i in range(24)], width=4).pack(side="left")
        ttk.Label(tf, text="时").pack(side="left",padx=1)
        ttk.Combobox(tf, textvariable=self.min_var, values=[f"{i:02d}" for i in range(60)], width=4).pack(side="left")
        ttk.Label(tf, text="分").pack(side="left",padx=1)

        # ── 2. Trigger 监控路径（醒目！）──
        f_trig = ttk.LabelFrame(mf, text=" 2. Trigger 远程触发监控路径 ", padding="8")
        f_trig.pack(fill="x", pady=(0,6))

        r_trig1 = ttk.Frame(f_trig); r_trig1.pack(fill="x", pady=(0,4))
        ttk.Label(r_trig1, text="监控根路径", font=("Microsoft YaHei", 10, "bold")).pack(side="left", padx=(0,6))
        tk.Entry(r_trig1, textvariable=self.trigger_root_var, font=("Consolas", 10), width=52,
                 bg="#f8fafc", relief="solid", bd=1).pack(side="left", fill="x", expand=True, ipady=1)
        tk.Button(r_trig1, text="浏览", command=self._pick_trigger,
                  bg="#2563EB", fg="white", font=("Microsoft YaHei", 9),
                  relief="flat", padx=10, cursor="hand2").pack(side="left", padx=4)

        r_trig2 = ttk.Frame(f_trig); r_trig2.pack(fill="x")
        tk.Label(r_trig2, textvariable=self.trigger_path_label_var,
                 bg="#EFF6FF", fg="#2563EB", font=("Consolas", 10, "bold"),
                 anchor="w", relief="solid", bd=1, padx=10, pady=4).pack(fill="x")

        # 绑定线别/站别变更 → 刷新 trigger 路径显示
        self.line_var.trace_add("write", lambda *a: self._update_trigger_display())
        self.station_var.trace_add("write", lambda *a: self._update_trigger_display())
        self.trigger_root_var.trace_add("write", lambda *a: [self._update_trigger_display(), self.save_cfg()])

        # ── 3. 本地同步路径 ──
        f3 = ttk.LabelFrame(mf, text=" 3. 本地同步路径池（定时同步用） ", padding="8")
        f3.pack(fill="x", pady=(0,6))

        rp1 = ttk.Frame(f3); rp1.pack(fill="x")
        ttk.Label(rp1, text="源:").pack(side="left", padx=(0,4))
        tk.Entry(rp1, textvariable=self.source_var, font=("Consolas",9), width=42).pack(side="left",fill="x",expand=True,ipady=1)
        tk.Button(rp1, text="浏览", command=self._pick_src, relief="flat", padx=6).pack(side="left",padx=2)

        rp2 = ttk.Frame(f3); rp2.pack(fill="x", pady=(4,0))
        ttk.Label(rp2, text="目的:").pack(side="left", padx=(0,4))
        tk.Entry(rp2, textvariable=self.target_var, font=("Consolas",9), width=42).pack(side="left",fill="x",expand=True,ipady=1)
        tk.Button(rp2, text="浏览", command=self._pick_dst, relief="flat", padx=6).pack(side="left",padx=2)

        rp3 = ttk.Frame(f3); rp3.pack(fill="x", pady=(4,0))
        tk.Button(rp3, text="➕ 添加到路径池", command=self._add_pool,
                  bg="#2563EB", fg="white", font=("Microsoft YaHei",10),
                  relief="flat", padx=12, cursor="hand2").pack(side="right", padx=2)
        tk.Button(rp3, text="❌ 删除选中", command=self._del_pool,
                  bg="#EF4444", fg="white", font=("Microsoft YaHei",10),
                  relief="flat", padx=12, cursor="hand2").pack(side="right", padx=2)

        # 路径池列表
        self.plb = tk.Listbox(f3, height=3, font=("Consolas",9), selectmode="single", bg="#f8fafc")
        self.plb.pack(fill="x", pady=(4,0))
        ttk.Scrollbar(f3, command=self.plb.yview).pack(side="right",fill="y")

        # ── 4. 日志 ──
        f4 = ttk.LabelFrame(mf, text=" 4. 运行日志 ", padding="8"); f4.pack(fill="both", expand=True, pady=(0,6))
        self.log_txt = tk.Text(f4, height=8, bg="#1e293b", fg="#e2e8f0", insertbackground="white",
                                font=("Consolas",9), relief="flat", bd=0)
        self.log_txt.pack(fill="both", expand=True)
        ttk.Scrollbar(f4, command=self.log_txt.yview).pack(side="right",fill="y"); self.log_txt.config(yscrollcommand=lambda *a: None)

        # ── 按钮 ──
        bf = tk.Frame(mf); bf.pack(fill="x")
        tk.Button(bf, text="⚡ 手动同步(定时路径池)", command=self._manual,
                  bg="#0284c7", fg="white", font=("Microsoft YaHei",10,"bold"),
                  relief="flat", padx=14, pady=8).pack(side="left",padx=(0,6))
        self.start_btn = tk.Button(bf, text="▶️ 开始后台自动运行", command=self._toggle,
                                    bg="#10B981", fg="white", font=("Microsoft YaHei",10,"bold"),
                                    relief="flat", padx=14, pady=8)
        self.start_btn.pack(side="right")

    # ═══ 路径操作 ═══
    def _pick_src(self):
        p = filedialog.askdirectory(); p and self.source_var.set(p.replace("\\","/"))
    def _pick_dst(self):
        p = filedialog.askdirectory(); p and self.target_var.set(p.replace("\\","/"))
    def _pick_trigger(self):
        p = filedialog.askdirectory(title="选择服务器根目录（trigger 将在此目录下）")
        if p:
            self.trigger_root_var.set(p.replace("\\","/"))
            self._update_trigger_display()

    def _add_pool(self):
        s,d = self.source_var.get().strip(), self.target_var.get().strip()
        if not s or not d: messagebox.showwarning("提示","源和目的不能为空"); return
        self.pool.append({"src":s,"dst":d}); self.refresh_pool(); self.save_cfg()
        self.source_var.set(""); self.target_var.set("")
        self._log(f"【池】+1, 共 {len(self.pool)} 条")

    def _del_pool(self):
        sel = self.plb.curselection()
        if sel:
            del self.pool[sel[0]]; self.refresh_pool(); self.save_cfg()
            self._log("【池】已删除")

    def refresh_pool(self):
        self.plb.delete(0,"end")
        for i,p in enumerate(self.pool,1): self.plb.insert("end", f"[{i}] {p['src']} ➔ {p['dst']}")

    # ═══ 日志 ═══
    def _log(self, msg):
        def do():
            self.log_txt.insert("end", f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n"); self.log_txt.see("end")
            n = int(self.log_txt.index('end-1c').split('.')[0])
            if n > MAX_LOG: self.log_txt.delete('1.0', f'{n-MAX_LOG}.0')
        self.root.after(0, do)

    # ═══ 后台运行 ═══
    def _toggle(self):
        if not self.running:
            if not self.pool and not self.trigger_root_var.get().strip():
                messagebox.showwarning("警告","至少配置路径池或Trigger监控路径"); return
            self.save_cfg(); self.running = True
            self.start_btn.config(text="⏹️ 停止", bg="#EF4444")
            self._log(f"【启动】定时={self.hour_var.get()}:{self.min_var.get()}, Trigger监控={self._trigger_full_path()}")
            threading.Thread(target=self._timer, daemon=True).start()
            # 🆕 Trigger 监控线程
            if self.trigger_root_var.get().strip():
                self.trig_mon = True
                self.trig_thread = threading.Thread(target=self._trigger_watch, daemon=True)
                self.trig_thread.start()
                self._log("【Trigger】监控线程已启动")
            self.root.after(1200, self._tray)
        else:
            self.running = False; self.trig_mon = False
            self.start_btn.config(text="▶️ 开始后台自动运行", bg="#10B981")
            self._log("【停止】已手动退出")

    def _timer(self):
        last = ""
        while self.running:
            n = datetime.now(); cd = n.strftime("%Y-%m-%d"); ch,cm = n.hour,n.minute
            th,tm = int(self.hour_var.get()),int(self.min_var.get())
            if ch==th and abs(cm-tm)<=1 and last!=cd:
                self._log("【定时】触发！执行同步...")
                self._sync(); last = cd
            near = ch==th and abs(cm-tm)<=5; time.sleep(10 if near else 45)

    def _manual(self):
        if not self.pool: messagebox.showwarning("警告","路径池为空"); return
        if messagebox.askyesno("确认","立即用路径池同步？"):
            threading.Thread(target=self._sync, daemon=True).start()

    # ═══ 核心同步 ═══
    def _sync(self, paths=None, target_root=None):
        """paths: [{src,dst_sub}], target_root: 服务器根。为空则用本地 pool"""
        line = self.line_var.get().strip(); station = self.station_var.get().strip()
        dev = self.device_var.get().strip(); now = datetime.now(); ds = now.strftime("%Y-%m-%d"); td = now.date()
        if paths and target_root:
            pool = [{"src":p["src"], "dst": os.path.join(target_root, ds, f"Line_{line}", station, dev, p.get("dst_sub", os.path.basename(p["src"]))).replace("\\","/")} for p in paths]
        else:
            pool = self.pool
        tc,ts,te = 0,0,0
        for i, item in enumerate(pool, 1):
            s,d = item["src"], item["dst"]
            if not os.path.exists(s): self._log(f"❌ 源不存在: {s}"); te+=1; continue
            for at in range(1, MAX_RETRY+1):
                try:
                    c,sk,e = self._walk(s,d,td)
                    tc+=c; ts+=sk; te+=e
                    self._log(f"{'✅' if e==0 else '⚠️'} [{i}/{len(pool)}] +{c} 跳{sk}" + (f" 异常{e}" if e else ""))
                    break
                except Exception as ex:
                    if at<MAX_RETRY: self._log(f"重试{at}..."); time.sleep(RETRY_DELAY)
                    else: self._log(f"❌ 放弃: {ex}"); te+=1
        self._log(f"【完成】更新{tc}/跳过{ts}" + (f"/异常{te}" if te else ""))
        return tc,ts,te

    def _walk(self, src, dst, td):
        c=s=e=0
        try:
            if os.path.isfile(src):
                if datetime.fromtimestamp(os.path.getmtime(src)).date()==td:
                    need=True
                    if os.path.exists(dst):
                        try:
                            if os.path.getsize(src)==os.path.getsize(dst): need=False
                        except: pass
                    if need: os.makedirs(os.path.dirname(dst),exist_ok=True); shutil.copy2(src,dst); c+=1
                    else: s+=1
                else: s+=1
            elif os.path.isdir(src):
                for it in os.listdir(src):
                    cs,ss,es = self._walk(os.path.join(src,it), os.path.join(dst,it), td)
                    c+=cs; s+=ss; e+=es
        except Exception as ex: self._log(f"⚠️ {src}: {ex}"); e+=1
        return c,s,e

    # ═══ 🆕 Trigger 监控 ═══
    def _trigger_watch(self):
        while self.trig_mon and self.running:
            try:
                root = self.trigger_root_var.get().strip()
                if not root: time.sleep(TRIG_CHECK); continue
                line = self.line_var.get().strip(); station = self.station_var.get().strip()
                cmd_path = os.path.join(root, "trigger", f"line_{line}", f"{station}_cmd.json").replace("\\","/")
                if os.path.exists(cmd_path):
                    self._log(f"🔔 检测到 Trigger: {cmd_path}")
                    try:
                        with open(cmd_path, encoding="utf-8") as f: cmd = json.load(f)
                    except Exception as ex:
                        self._log(f"❌ Trigger 读取失败: {ex}"); time.sleep(TRIG_CHECK); continue
                    cl,cs = cmd.get("line",""), cmd.get("station_type","")
                    if cl!=line or cs!=station:
                        self._log(f"⚠️ Trigger 线别/站别({cl}/{cs})不匹配本机({line}/{station})，跳过")
                        time.sleep(TRIG_CHECK); continue
                    tr = cmd.get("target_root", root)
                    sp = cmd.get("source_paths", [])
                    if sp:
                        self._log(f"🚀 执行远程采集 → {tr} ({len(sp)}源)")
                        cp,sk,er = self._sync(paths=sp, target_root=tr)
                        # 写完成标记
                        dp = cmd_path.replace("_cmd.json","_done.json")
                        os.makedirs(os.path.dirname(dp), exist_ok=True)
                        with open(dp,"w",encoding="utf-8") as f:
                            json.dump({"status":"done" if er==0 else "partial","line":line,
                                        "station_type":station,"device_id":self.device_var.get().strip(),
                                        "timestamp":datetime.now().isoformat(),
                                        "files_copied":cp,"files_skipped":sk,"errors":er}, f, indent=2)
                        self._log(f"✅ 完成标记 → {dp}")
                    # 消费 trigger
                    try: os.remove(cmd_path); self._log("🗑 Trigger 已消费")
                    except: pass
            except Exception as ex: self._log(f"⚠️ Trigger监控异常: {ex}")
            time.sleep(TRIG_CHECK)
        self._log("【Trigger】监控线程退出")

    # ═══ 托盘 ═══
    def _tray_img(self):
        img = Image.new('RGB',(64,64),color=(30,41,59))
        ImageDraw.Draw(img).rectangle((16,16,48,48),fill=(16,185,129)); return img

    def _tray(self):
        self.root.withdraw()
        menu = pystray.Menu(
            pystray.MenuItem("▶ 显示界面", self._restore, default=True),
            pystray.MenuItem("🛑 退出", self._quit))
        self.icon = pystray.Icon("Sync", self._tray_img(), "数据同步(挂机中)", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def _restore(self, icon, item): icon.stop(); self.root.after(0, self.root.deiconify)
    def _quit(self, icon, item):
        icon.stop(); self.running=False; self.trig_mon=False; self.root.after(0, self.root.destroy); os._exit(0)


if __name__ == "__main__":
    root = tk.Tk(); App(root); root.mainloop()
