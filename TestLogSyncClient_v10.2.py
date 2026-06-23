#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
产线测试数据自动汇总客户端 v10.2 (Trigger Edition)
===============================================
基于 v10.1，新增远程触发监控能力。

改动：
  + trigger 监控线程：每 10 秒检查服务器 trigger/line_{我}/{我站别}_cmd.json
  + 读取 trigger JSON 后立即执行同步（使用远程配置覆盖本地 config）
  + 同步完成后写 _done.json 标记
  + 定时器逻辑保持不变（兜底）
"""
import os
import sys
import shutil
import configparser
import threading
import time
import json
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import pystray
from PIL import Image, ImageDraw

# ═══════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════
MAX_LOG_LINES = 500
MAX_RETRIES = 3
RETRY_DELAY = 5
PROGRESS_INTERVAL = 50
TRIGGER_CHECK_INTERVAL = 10  # 触发监控轮询间隔(秒)
TRIGGER_DIR = "trigger"      # 服务器上 trigger 目录名


class PathPoolCopyApp:
    def __init__(self, root):
        self.root = root
        self.root.title("产线测试数据自动汇总客户端 v10.2")
        self.root.geometry("720x720")
        self.root.resizable(False, False)

        # DPI 感知
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        # 路径
        if getattr(sys, 'frozen', False):
            application_path = os.path.dirname(sys.executable)
        else:
            application_path = os.path.dirname(os.path.abspath(__file__))
        self.config_file = os.path.join(application_path, "config.ini")
        self.config = configparser.ConfigParser()

        # 状态变量
        self.line_var = tk.StringVar(value="Line_1")
        self.station_var = tk.StringVar(value="AT")
        self.device_var = tk.StringVar(value="AT_01")
        self.source_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.target_hour = tk.StringVar(value="16")
        self.target_minute = tk.StringVar(value="30")
        self.is_running = False
        self.path_pool = []
        self.trigger_pool = []  # 远程 trigger 带来的临时路径池

        # Trigger 监控变量
        self.trigger_monitoring = False
        self.trigger_thread = None

        self.load_config()
        self.create_widgets()
        self.refresh_pool_listbox()
        self.root.protocol('WM_DELETE_WINDOW', self.minimize_to_tray)

    # ═══════════════════════════════════════════════
    # 配置持久化（同 v10.1）
    # ═══════════════════════════════════════════════
    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                self.config.read(self.config_file, encoding='utf-8')
                self.line_var.set(self.config.get("STATION_INFO", "line_id", fallback="Line_1"))
                self.station_var.set(self.config.get("STATION_INFO", "station_type", fallback="AT"))
                self.device_var.set(self.config.get("STATION_INFO", "device_id", fallback="AT_01"))
                self.target_hour.set(self.config.get("TIMER_CONFIG", "hour", fallback="16"))
                self.target_minute.set(self.config.get("TIMER_CONFIG", "minute", fallback="30"))
                pool_json = self.config.get("PATH_CONFIG", "pool_data", fallback="[]")
                self.path_pool = json.loads(pool_json)
            except Exception:
                self.path_pool = []

    def save_config(self):
        if "STATION_INFO" not in self.config:
            self.config["STATION_INFO"] = {}
        if "PATH_CONFIG" not in self.config:
            self.config["PATH_CONFIG"] = {}
        if "TIMER_CONFIG" not in self.config:
            self.config["TIMER_CONFIG"] = {}
        self.config["STATION_INFO"]["line_id"] = self.line_var.get().strip()
        self.config["STATION_INFO"]["station_type"] = self.station_var.get().strip()
        self.config["STATION_INFO"]["device_id"] = self.device_var.get().strip()
        self.config["TIMER_CONFIG"]["hour"] = self.target_hour.get().strip()
        self.config["TIMER_CONFIG"]["minute"] = self.target_minute.get().strip()
        self.config["PATH_CONFIG"]["pool_data"] = json.dumps(self.path_pool)
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                self.config.write(f)
            return True
        except Exception as e:
            messagebox.showerror("错误", f"保存配置文件失败: {str(e)}")
            return False

    # ═══════════════════════════════════════════════
    # 路径操作（同 v10.1）
    # ═══════════════════════════════════════════════
    def select_source(self):
        path = filedialog.askdirectory(title="选择本地测试数据源文件夹")
        if path:
            self.source_var.set(path.replace('\\', '/'))

    def select_target(self):
        path = filedialog.askdirectory(title="选择内网服务器目标文件夹")
        if path:
            self.target_var.set(path.replace('\\', '/'))

    def add_to_pool(self):
        src = self.source_var.get().strip()
        dst = self.target_var.get().strip()
        if not src or not dst:
            messagebox.showwarning("提示", "请先选择完整的【源路径】和【目的路径】后再添加！")
            return
        for item in self.path_pool:
            if item["src"] == src and item["dst"] == dst:
                messagebox.showwarning("提示", "该路径映射已经存在于池子中！")
                return
        self.path_pool.append({"src": src, "dst": dst})
        self.refresh_pool_listbox()
        self.save_config()
        self.source_var.set("")
        self.target_var.set("")
        self.log_message(f"【池管理】追加一条地址串。当前共有 {len(self.path_pool)} 条任务。")

    def remove_from_pool(self):
        selected_index = self.pool_listbox.curselection()
        if not selected_index:
            messagebox.showwarning("提示", "请先在下方的池子中选中要删除的地址串！")
            return
        if messagebox.askyesno("删除确认", "是否确定将该条路径移出传输池？"):
            index = selected_index[0]
            del self.path_pool[index]
            self.refresh_pool_listbox()
            self.save_config()
            self.log_message("【池管理】已成功移除选中的路径地址串。")

    def refresh_pool_listbox(self):
        self.pool_listbox.delete(0, tk.END)
        for i, item in enumerate(self.path_pool, 1):
            self.pool_listbox.insert(tk.END, f"[{i}] 源: {item['src']} ➔ 目的: {item['dst']}")

    # ═══════════════════════════════════════════════
    # 日志
    # ═══════════════════════════════════════════════
    def log_message(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def append():
            self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
            lines = int(self.log_text.index('end-1c').split('.')[0])
            if lines > MAX_LOG_LINES:
                self.log_text.delete('1.0', f'{lines - MAX_LOG_LINES}.0')
            self.log_text.see(tk.END)

        self.root.after(0, append)

    # ═══════════════════════════════════════════════
    # 定时器（同 v10.1，不变）
    # ═══════════════════════════════════════════════
    def toggle_timer(self):
        if not self.is_running:
            if not self.path_pool:
                messagebox.showwarning("警告", "当前路径池为空！请先添加地址串。")
                return
            if not self.save_config():
                return
            self.is_running = True
            self.start_btn.config(text="⏹️ 停止自动运行", bg="#dc2626")
            self.log_message(
                f"【定时器】守护启动！每日 {self.target_hour.get()}:{self.target_minute.get()} 自动提取并合并当天增量数据。"
            )
            self.timer_thread = threading.Thread(target=self.backend_timer_worker, daemon=True)
            self.timer_thread.start()
            # ---- TRIGGER 监控线程(新增) ----
            self.trigger_monitoring = True
            self.trigger_thread = threading.Thread(target=self.trigger_monitor_worker, daemon=True)
            self.trigger_thread.start()
            self.log_message("【Trigger】远程触发监控已启动。")
            # ----
            self.root.after(1200, self.minimize_to_tray)
        else:
            self.is_running = False
            self.trigger_monitoring = False
            self.start_btn.config(text="▶️ 开始后台自动运行", bg="#16a34a")
            self.log_message("【定时器】已手动退出后台监控状态。")

    def backend_timer_worker(self):
        last_executed_date = ""
        while self.is_running:
            now = datetime.now()
            current_date = now.strftime("%Y-%m-%d")
            current_h = now.hour
            current_m = now.minute
            target_h = int(self.target_hour.get())
            target_m = int(self.target_minute.get())
            in_window = (current_h == target_h and abs(current_m - target_m) <= 1)
            if in_window and last_executed_date != current_date:
                self.log_message("【触发】时间已到！开始执行今日数据提取与增量合并...")
                self.execute_pool_copy_logic()
                last_executed_date = current_date
            near_target = (current_h == target_h and abs(current_m - target_m) <= 5)
            time.sleep(10 if near_target else 45)

    def manual_sync(self):
        if not self.path_pool:
            messagebox.showwarning("警告", "池子为空，没有可同步的数据！")
            return
        if messagebox.askyesno("手动同步", "是否立即执行一次智能合并同步？\n\n(将递归提取今天变动的文件，若服务器已存在旧文件则对比大小后覆盖更新)"):
            threading.Thread(target=self.execute_pool_copy_logic, daemon=True).start()

    # ═══════════════════════════════════════════════
    # 核心：智能递归合并引擎（同 v10.1）
    # ═══════════════════════════════════════════════
    def smart_incremental_sync(self, src, dst, today_date, progress_cb=None):
        copied_files = 0
        skipped_files = 0
        error_files = 0
        try:
            if os.path.isfile(src):
                mtime = os.path.getmtime(src)
                if datetime.fromtimestamp(mtime).date() == today_date:
                    need_copy = True
                    if os.path.exists(dst):
                        try:
                            if os.path.getsize(src) == os.path.getsize(dst):
                                need_copy = False
                        except OSError:
                            pass
                    if need_copy:
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                        copied_files += 1
                    else:
                        skipped_files += 1
                else:
                    skipped_files += 1
            elif os.path.isdir(src):
                try:
                    entries = os.listdir(src)
                except PermissionError:
                    self.log_message(f"【警告】无权限访问 {src}，跳过")
                    return 0, 0, 1
                for item in entries:
                    s_item = os.path.join(src, item)
                    d_item = os.path.join(dst, item)
                    c, s, e = self.smart_incremental_sync(s_item, d_item, today_date, progress_cb)
                    copied_files += c
                    skipped_files += s
                    error_files += e
                    if progress_cb and (copied_files + skipped_files) % PROGRESS_INTERVAL == 0:
                        progress_cb(copied_files, skipped_files)
        except Exception as e:
            self.log_message(f"【警告】处理 {src} 时出错: {e}")
            error_files += 1
        return copied_files, skipped_files, error_files

    # ═══════════════════════════════════════════════
    # 执行池任务（同 v10.1）
    # ═══════════════════════════════════════════════
    def execute_pool_copy_logic(self, source_paths=None, target_root=None):
        """
        执行同步。如果提供 source_paths 和 target_root，则使用远程配置；
        否则使用本地 path_pool。
        """
        line = self.line_var.get().strip()
        station = self.station_var.get().strip()
        device = self.device_var.get().strip()
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        today_date = now.date()

        # 决定使用哪个路径池
        if source_paths and target_root:
            pool = [
                {
                    "src": sp["src"],
                    "dst": os.path.join(target_root, date_str, f"Line_{line}", station, device, sp.get("dst_sub", os.path.basename(sp["src"]))).replace("\\", "/")
                }
                for sp in source_paths
            ]
            self.log_message(f"【Trigger】使用远程配置 — {len(pool)} 条路径")
        else:
            pool = self.path_pool

        total_copied = 0
        total_skipped = 0
        total_errors = 0

        for index, item in enumerate(pool, 1):
            src = item["src"]
            dst = item["dst"]
            self.log_message(f"【进度 {index}/{len(pool)}】扫描源: {src}")
            if not os.path.exists(src):
                self.log_message("【错误】源路径不存在，自动跳过。")
                continue

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    def progress_cb(copied, skipped):
                        self.log_message(f" ↳ 已处理 {copied + skipped} 个文件...")

                    copied_count, skipped_count, error_count = self.smart_incremental_sync(
                        src, dst, today_date, progress_cb
                    )
                    status = "✅" if error_count == 0 else "⚠️"
                    self.log_message(
                        f"【{status}】池任务 {index} 完成。"
                        f"新增/更新 {copied_count} 个，跳过(无变化/旧文件) {skipped_count} 个"
                        + (f"，异常 {error_count} 个" if error_count else "")
                    )
                    total_copied += copied_count
                    total_skipped += skipped_count
                    total_errors += error_count
                    break
                except Exception as e:
                    if attempt < MAX_RETRIES:
                        self.log_message(f"【重试 {attempt}/{MAX_RETRIES}】失败: {e}，{RETRY_DELAY}秒后重试...")
                        time.sleep(RETRY_DELAY)
                    else:
                        self.log_message(f"【放弃】池任务 {index} 重试 {MAX_RETRIES} 次后仍失败: {e}")
                        total_errors += 1

        summary = f"【大功告成】同步结束！更新 {total_copied} / 跳过 {total_skipped}"
        if total_errors:
            summary += f" / 异常 {total_errors}"
        self.log_message(summary)

        return total_copied, total_skipped, total_errors

    # ═══════════════════════════════════════════════
    # 🆕 Trigger 远程触发监控（v10.2 新增）
    # ═══════════════════════════════════════════════
    def trigger_monitor_worker(self):
        """
        监控线程：每 TRIGGER_CHECK_INTERVAL 秒检查服务器上是否有属于自己的 trigger。
        路径: {target_root}/trigger/line_{我}/ {我站别}_cmd.json
        """
        line = self.line_var.get().strip()
        station = self.station_var.get().strip()

        while self.trigger_monitoring and self.is_running:
            try:
                # 从本地 path_pool 推断服务器根目录
                # 取第一个 dst 的公共前缀作为 server_root
                server_root = self._detect_server_root()
                if not server_root:
                    time.sleep(TRIGGER_CHECK_INTERVAL)
                    continue

                trigger_path = os.path.join(
                    server_root, TRIGGER_DIR,
                    f"line_{line}", f"{station}_cmd.json"
                ).replace("\\", "/")

                if os.path.exists(trigger_path):
                    self.log_message(f"【Trigger】检测到触发指令: {trigger_path}")
                    try:
                        with open(trigger_path, "r", encoding="utf-8") as f:
                            cmd = json.load(f)
                    except Exception as e:
                        self.log_message(f"【Trigger】读取失败: {e}")
                        time.sleep(TRIGGER_CHECK_INTERVAL)
                        continue

                    # 验证指令
                    cmd_line = cmd.get("line", "")
                    cmd_station = cmd.get("station_type", "")
                    if cmd_line != line or cmd_station != station:
                        self.log_message(f"【Trigger】指令线别({cmd_line}/{cmd_station})与本机({line}/{station})不匹配，跳过")
                        time.sleep(TRIGGER_CHECK_INTERVAL)
                        continue

                    # 执行远程配置的同步
                    target_root = cmd.get("target_root", server_root)
                    source_paths = cmd.get("source_paths", [])
                    if not source_paths:
                        self.log_message("【Trigger】指令中无 source_paths，跳过")
                    else:
                        self.log_message(
                            f"【Trigger】收到主控指令 → target: {target_root}, "
                            f"source_paths: {len(source_paths)} 个"
                        )
                        copied, skipped, errors = self.execute_pool_copy_logic(
                            source_paths=source_paths,
                            target_root=target_root
                        )

                        # 写完成标记
                        done_path = trigger_path.replace("_cmd.json", "_done.json")
                        done_data = {
                            "status": "done" if errors == 0 else "partial",
                            "line": line,
                            "station_type": station,
                            "device_id": self.device_var.get().strip(),
                            "timestamp": datetime.now().isoformat(),
                            "files_copied": copied,
                            "files_skipped": skipped,
                            "errors": errors,
                        }
                        try:
                            os.makedirs(os.path.dirname(done_path), exist_ok=True)
                            with open(done_path, "w", encoding="utf-8") as f:
                                json.dump(done_data, f, ensure_ascii=False, indent=2)
                            self.log_message(f"【Trigger】完成标记已写入: {done_path}")
                        except Exception as e:
                            self.log_message(f"【Trigger】写完成标记失败: {e}")

                        # 消费 trigger（删除 cmd.json）
                        try:
                            os.remove(trigger_path)
                            self.log_message("【Trigger】指令文件已消费")
                        except Exception:
                            pass

            except Exception as e:
                self.log_message(f"【Trigger】监控异常: {e}")

            time.sleep(TRIGGER_CHECK_INTERVAL)

        self.log_message("【Trigger】监控线程已退出")

    def _detect_server_root(self):
        """从本地 path_pool 的第一个 dst 推断服务器根目录"""
        if not self.path_pool:
            return None
        # 取第一个 dst，向上找 — dst 格式通常是: //server/data/2026-06-23/Line_1/AT/AT_01/xxx
        first_dst = self.path_pool[0].get("dst", "")
        if not first_dst:
            return None
        # 尝试找到 trigger/ 目录的父级
        # 简单策略：取 date_str 的上一级作为 server_root
        parts = first_dst.replace("\\", "/").rstrip("/").split("/")
        # 如果路径包含日期格式 YYYY-MM-DD，取其父目录
        for i, part in enumerate(parts):
            if len(part) == 10 and part[4] == '-' and part[7] == '-':
                return "/".join(parts[:i])
        # Fallback: 往上找三层（跳过 date/line/station）
        if len(parts) >= 4:
            return "/".join(parts[:-4])
        return first_dst

    # ═══════════════════════════════════════════════
    # 系统托盘（同 v10.1）
    # ═══════════════════════════════════════════════
    def create_tray_image(self):
        image = Image.new('RGB', (64, 64), color=(30, 41, 59))
        draw = ImageDraw.Draw(image)
        draw.rectangle((16, 16, 48, 48), fill=(16, 185, 129))
        return image

    def minimize_to_tray(self):
        self.root.withdraw()
        menu = pystray.Menu(
            pystray.MenuItem("▶ 显示主配置界面", self.restore_from_tray, default=True),
            pystray.MenuItem("🛑 完全退出程序", self.quit_app)
        )
        self.tray_icon = pystray.Icon(
            "DataSync", self.create_tray_image(),
            "产线数据增量合并 (挂机中)",
            menu
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def restore_from_tray(self, icon, item):
        icon.stop()
        self.root.after(0, self.root.deiconify)

    def quit_app(self, icon, item):
        icon.stop()
        self.is_running = False
        self.trigger_monitoring = False
        self.root.after(0, self.root.destroy)
        os._exit(0)

    # ═══════════════════════════════════════════════
    # GUI（同 v10.1）
    # ═══════════════════════════════════════════════
    def create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. 机台信息
        config_frame = ttk.LabelFrame(main_frame, text=" 1. 基本机台信息与定时设置 ", padding="10")
        config_frame.pack(fill=tk.X, side=tk.TOP, pady=5)

        ttk.Label(config_frame, text="所属线别:").grid(row=0, column=0, sticky=tk.W, padx=2, pady=5)
        ttk.Combobox(config_frame, textvariable=self.line_var,
                     values=["Line_1", "Line_2", "Line_3", "A03", "A05", "A07"], width=8
                     ).grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(config_frame, text="所属站别:").grid(row=0, column=2, sticky=tk.W, padx=10, pady=5)
        ttk.Combobox(config_frame, textvariable=self.station_var,
                     values=["AT", "FT", "QA"], width=8
                     ).grid(row=0, column=3, sticky=tk.W, padx=5, pady=5)
        ttk.Label(config_frame, text="机台编号:").grid(row=0, column=4, sticky=tk.W, padx=10, pady=5)
        ttk.Entry(config_frame, textvariable=self.device_var, width=12
                  ).grid(row=0, column=5, sticky=tk.W, padx=5, pady=5)

        ttk.Label(config_frame, text="每日触发时间:").grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=2, pady=5)
        time_sub_frame = ttk.Frame(config_frame)
        time_sub_frame.grid(row=1, column=2, columnspan=4, sticky=tk.W, pady=5)
        ttk.Combobox(time_sub_frame, textvariable=self.target_hour,
                     values=[f"{i:02d}" for i in range(24)], width=4
                     ).pack(side=tk.LEFT)
        ttk.Label(time_sub_frame, text="时").pack(side=tk.LEFT, padx=2)
        ttk.Combobox(time_sub_frame, textvariable=self.target_minute,
                     values=[f"{i:02d}" for i in range(60)], width=4
                     ).pack(side=tk.LEFT)
        ttk.Label(time_sub_frame, text="分").pack(side=tk.LEFT, padx=2)

        # 2. 路径配置
        path_frame = ttk.LabelFrame(main_frame, text=" 2. 路径添加操作区 ", padding="10")
        path_frame.pack(fill=tk.X, pady=5)

        ttk.Label(path_frame, text="本地源路径:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(path_frame, textvariable=self.source_var, width=54).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(path_frame, text="浏览...", command=self.select_source, width=8).grid(row=0, column=2, padx=5, pady=5)

        ttk.Label(path_frame, text="服务器目的:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(path_frame, textvariable=self.target_var, width=54).grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(path_frame, text="浏览...", command=self.select_target, width=8).grid(row=1, column=2, padx=5, pady=5)

        action_btn_frame = ttk.Frame(path_frame)
        action_btn_frame.grid(row=2, column=1, columnspan=2, sticky=tk.E, pady=5)
        ttk.Button(action_btn_frame, text="❌ 从池子中删除", command=self.remove_from_pool, width=18).pack(side=tk.RIGHT, padx=5)
        ttk.Button(action_btn_frame, text="➕ 添加入待传路径池", command=self.add_to_pool, width=18).pack(side=tk.RIGHT, padx=5)

        # 3. 路径池
        pool_frame = ttk.LabelFrame(main_frame, text=" 3. 待传路径池 (当前任务清单) ", padding="10")
        pool_frame.pack(fill=tk.X, pady=5)
        self.pool_listbox = tk.Listbox(pool_frame, height=4, font=("Consolas", 9), selectmode=tk.SINGLE)
        self.pool_listbox.pack(fill=tk.X, side=tk.LEFT, expand=True)
        scroll_y = ttk.Scrollbar(pool_frame, command=self.pool_listbox.yview)
        scroll_y.pack(fill=tk.Y, side=tk.RIGHT)
        self.pool_listbox.config(yscrollcommand=scroll_y.set)

        # 4. 日志
        log_frame = ttk.LabelFrame(main_frame, text=" 4. 运行状态日志 ", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_text = tk.Text(log_frame, height=6, bg="#ffffff", font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(fill=tk.Y, side=tk.RIGHT)
        self.log_text.config(yscrollcommand=scrollbar.set)

        # 底部按钮
        btn_frame = ttk.Frame(main_frame, padding="5")
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=5)
        tk.Button(
            btn_frame, text="⚡ 仅同步今日数据 (手动合并)",
            command=self.manual_sync, bg="#0284c7", fg="white",
            font=("微软雅黑", 11, "bold"), width=24
        ).pack(side=tk.LEFT, padx=5, ipady=5)
        self.start_btn = tk.Button(
            btn_frame, text="▶️ 开始后台自动运行",
            command=self.toggle_timer, bg="#16a34a", fg="white",
            font=("微软雅黑", 11, "bold"), width=24
        )
        self.start_btn.pack(side=tk.RIGHT, padx=5, ipady=5)


if __name__ == "__main__":
    root = tk.Tk()
    app = PathPoolCopyApp(root)
    root.mainloop()
