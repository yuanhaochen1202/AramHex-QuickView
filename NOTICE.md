# 第三方组件与数据

安装包和便携版里带了下面这些组件，各自遵循自己的协议：

| 组件 | 用途 | 协议 |
|---|---|---|
| [Qt for Python (PySide6)](https://www.qt.io/qt-for-python) | 界面 | LGPL-3.0（以独立的动态库形式随程序分发，可自行替换） |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) 及其 PaddleOCR 模型 | 文字识别 | Apache-2.0 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | 运行识别模型 | MIT |
| [OpenCV](https://opencv.org/) | 图像处理 | Apache-2.0 |
| [NumPy](https://numpy.org/) | 计算 | BSD-3-Clause |
| [Pillow](https://python-pillow.org/) | 图像处理 | MIT-CMU |
| [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) | 名称模糊匹配 | MIT |
| [python-mss](https://github.com/BoboTiG/python-mss) | 截屏 | MIT |
| [Requests](https://requests.readthedocs.io/) | 网络请求 | Apache-2.0 |
| [PyInstaller](https://pyinstaller.org/) | 打包 | GPL-2.0（带 bootloader 例外，不影响本程序的协议） |
| [Inno Setup](https://jrsoftware.org/isinfo.php) | 安装程序 | Inno Setup License |

数据：
- `lolhex/data/bundled/`：自采统计数据，来自 [czgreat/aramhex-data](https://github.com/czgreat/aramhex-data)，协议为 CC BY-NC 4.0。
- `lolhex/data/hero_aliases.json`：英雄别名，整理自 Hexdata（hexdata.com.cn）。
- `lolhex/data/hero_nicknames.json`：网上常见的英雄外号，人工整理。
- `lolhex/data/static_bundled.json`、`hero_bundled.json` 及内置图标包含来自腾讯官方公开接口（game.gtimg.cn、mlol.qt.qq.com）的英雄、海克斯、装备、出装和加点资料，个别海克斯中文名由 CommunityDragon 补全；这些资料和图像随程序分发，版权归原权利人所有。玩家模式从内置快照读取，并可通过自有资料源更新，不直接向上述第三方接口回源；显式资料采集工具仍可访问第三方接口。

## 定制版署名与许可

以上为原项目随 v1.1.5 发布的声明。AramHex 由 **czgreat** 开发，本定制版基于该版本独立维护。原代码的版权声明为 `Copyright (c) 2026 czgreat`。再分发软件或其中的主要部分时，须保留完整版权与许可声明。[原项目](https://github.com/czgreat/aramhex) · [上游 v1.1.5](https://github.com/czgreat/aramhex/releases/tag/v1.1.5) · [MIT 许可全文](LICENSE)

海克斯大乱斗统计数据（[github.com/czgreat/aramhex-data](https://github.com/czgreat/aramhex-data)），[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)。本版沿用上游 v1.1.5 内置的统计文件，调整了胜率的显示方式。分享统计数据时须保留来源、署名和许可信息，若修改数据还须注明改动。该许可仅涵盖非商业用途。[数据许可全文](DATA-LICENSE.txt)

英雄联盟相关游戏名称、商标和美术素材归各自权利人所有。本定制版由社区独立维护，未获腾讯或 Riot Games 背书或赞助，也不代表 Riot Games 或其游戏运营人员的观点。Riot Games 及其相关标识为 Riot Games, Inc. 的商标或注册商标。[Riot 社区创作政策](https://www.riotgames.com/en/legal) · [Riot 开发者政策](https://developer.riotgames.com/policies/general)

第三方组件、模型的许可原文、版权声明及来源记录见源码中的 `third_party_licenses/INDEX.txt`，或解压后程序目录下的 `_internal/THIRD-PARTY-LICENSES/INDEX.txt`。
