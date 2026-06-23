# 🏭 产线数据采集主控面板 (Line Data Master) v1.0

统一管理多产线多站别测试数据的采集触发与自动分析。

## 定位

部署于 **工程师办公电脑**。当你连入内网后，点「开始采集」→ 远程触发各产线机台同步数据 → 自动分析 → 弹出报告。

## 工作流

```
办公电脑                     服务器(网盘)                    产线机台
┌──────────┐              ┌──────────────┐              ┌──────────┐
│ 主控面板  │──写 trigger──→│ trigger/     │←──监控 trigger│ SyncClient│
│          │              │  line_A03/   │   (改版)     │          │
│          │              │   AT_cmd.json│              │ 立即同步  │
│          │              ├──────────────┤──同步数据───→│          │
│          │              │ 2026-06-23/  │←──────done──│ 写完成标记│
│          │←──直读分析────│              │              └──────────┘
│ Analyzer │  (不落盘)     │              │
│ → report │              └──────────────┘
└──────────┘
```

1. **主控写 trigger JSON** 到服务器 `trigger/line_{id}/{station}_cmd.json`
2. **各机台 SyncClient**（改版）监控到属于自己的 trigger → 立即执行同步 → 写 `done.json`
3. **主控轮询** done 标记，全部到齐后自动触发分析
4. **分析器直读** 网络路径（xls 不落磁盘，绕过办公电脑加密）
5. **报告弹出** + 同步回服务器

## 核心设计

### 线体/站别隔离

- 每条产线、每个站别只读取自己路径下的 trigger
- `trigger/line_A03/AT_cmd.json` — 仅 A03 线 AT 站读取
- `trigger/line_A03/FT_cmd.json` — 仅 A03 线 FT 站读取
- 互不干扰

### Trigger 消息格式

```json
{
  "action": "sync_now",
  "timestamp": "2026-06-23T16:30:00",
  "line": "A03",
  "station_type": "AT",
  "target_root": "//server/data",
  "source_paths": [
    {"src": "D:/TestLog/AT01/TC661", "dst_sub": "AT01"},
    {"src": "D:/TestLog/AT02/TC661", "dst_sub": "AT02"}
  ]
}
```

### 加密规避

- `.xls` 数据文件始终在网络路径上，不落地办公电脑磁盘
- Analyzer 流式读取 ZIP/文件，全程在内存中
- 最终只输出 `.html` 报告（不触发加密）

## 依赖

- Python 3.8+
- tkinter（Python 原生）
- 同机需安装 [AT-Audio-Test-Analyzer](https://github.com/Bowen0011/AT-Audio-Test-Analyzer)

## 快速开始

```bash
pip install -r requirements.txt
python master_control.py
```

## 配套工具

本主控面板需配合改版 [AutoDataSyncClient](https://github.com/Bowen0011/AutoDataSyncClient) 使用（需升级至支持 trigger 监控的版本）。

## License

MIT © 2026
