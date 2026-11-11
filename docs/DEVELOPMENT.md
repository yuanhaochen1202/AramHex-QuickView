# 用户版开发与构建

## 本地运行

在 Windows 10/11 x64 上安装 Python 3.13，再从仓库根目录运行。发布依赖对应 Python 3.13.16。

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -c packaging/constraints.txt -r requirements.txt
.venv\Scripts\python -m lolhex
```

`lolhex/__main__.py` 是程序入口。调试时可用环境变量 `ARAMHEX_HOME` 指定独立目录，保存配置、缓存和日志。

## 目录

| 路径 | 内容 |
|---|---|
| `lolhex/vision/` | 截屏、选人和海克斯文字识别、本局加载日志和英雄卡片定位 |
| `lolhex/engine.py`、`lolhex/ui/` | 识别调度、主窗口、托盘和浮窗 |
| `lolhex/data/` | 聚合统计、官方资料和内置数据 |
| `lolhex/custom_update.py`、`lolhex/custom_install.py` | 签名更新清单、文件校验及安装 |
| `lolhex/release_config.py` | 用户版程序更新的 HTTPS 地址和验签公钥 |
| `scripts/` | 数据格式校验、资料准备和上游数据工具 |
| `packaging/` | PyInstaller、许可文件整理和 Inno Setup 构建入口 |

程序通过截屏和本局加载日志识别游戏状态。浮窗将焦点交还游戏时使用 `SetForegroundWindow`。程序启动后从固定 COS 地址检查一次软件更新，打包版本在使用者确认后下载变化文件并安装。直接从源码运行时只检查版本。官方资料和统计数据库通过各自的数据地址刷新。

## 离线测试

```powershell
.venv\Scripts\python scripts/check_release_data.py
$env:QT_QPA_PLATFORM = 'offscreen'
$env:ARAMHEX_HOME = Join-Path ([System.IO.Path]::GetTempPath()) ('aramhex-test-' + [guid]::NewGuid())
.venv\Scripts\python -m pytest -q
```

界面测试使用 Qt 离屏模式，测试夹具为每项测试分配自己的临时目录。需要 HTTP 响应的测试采用模拟响应或本机临时服务。

## 构建

使用 PowerShell 7，在仓库根目录运行。仓库已包含构建所需的统计快照。

```powershell
.venv\Scripts\python -m pip install -r requirements-build.txt
pwsh -NoProfile -File packaging/build.ps1 -PortableOnly
```

输出位于 `build/release/user/`，包括 `AramHex-Portable-<版本>-user.zip` 和 `SHA256SUMS.txt`。便携包内的顶层目录名为 `AramHex`。构建会校验内置数据、复制项目和第三方许可，并运行图形界面及文字识别自检。在安装 Inno Setup 6 的电脑上，去掉 `-PortableOnly` 可同时生成安装包。构建脚本只生成本地文件。

## 云控项

本地定制版在读取缓存和网络响应时均过滤云端字段。允许范围如下；不在表中的远程配置不生效。

| 项 | 默认 | 说明 |
|---|---|---|
| `site` | 内置数据域名 | 仅供数据请求迁移，不能替换软件更新地址或界面官网链接 |
| `mirrors.data` / `mirrors.official` | 内置数据源 | 统计数据、官方资料的下载源 |
| `update.data_minutes` / `update.official_minutes` | 本地默认值 | 数据检查周期，不能控制程序检查或安装 |
| `event` | 默认活动 | 活动数据选择 |
| `augments.aliases` / `augments.rarity_fix` | 内置资料 | 海克斯名称、稀有度修正 |
| `rank.enabled` / `rank.api` / `rank.message` / `rank.as_of` | 本地默认值 | 保留已有在线分段查询服务；不能强制覆盖用户设置 |

远端 `settings`、`features`、`ui`、公告、最低程序版本、程序下载地址和日志点名上传均禁用。
