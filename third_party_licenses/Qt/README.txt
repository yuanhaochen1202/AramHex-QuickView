Qt / Qt for Python 第三方许可与源码说明
======================================

本目录适用于随此程序分发的 Qt、PySide6 和 Shiboken6 运行库。
版本：6.11.2。分发目录内 Qt6Core.dll 的文件和产品版本为 6.11.2.0。
本程序包含 Qt Core、Gui、Network、Widgets、Svg 运行库，以及 PySide6、
Shiboken6 的相应 Python 绑定和运行库；本项目未修改这些库的源代码。

1. 许可文本与署名

这些 Qt / Qt for Python 组件采用其适用的开源许可。对于可选择 LGPLv3
的组件，本分发采用 LGPLv3；本目录完整保留 LGPL-3.0-only.txt 及其引用的
GPL-3.0-only.txt。Qt-GPL-exception-1.0.txt 仅在上游明确适用的文件或组件
范围内适用，不意味着整个程序或所有 Qt 组件都有该例外。

Qt 及 Qt for Python 的版权属于 The Qt Company Ltd. 和相应贡献者。
各嵌入第三方组件的原始版权、许可和声明见以下目录，文本按官方
v6.11.2 源码原样保存：

  qtbase-6.11.2/src/3rdparty/
  qtsvg-6.11.2/src/svg/
  qtbase-6.11.2/LICENSES/
  qtsvg-6.11.2/LICENSES/

包含 QtBase 官方第三方 attribution 文件及相应 LICENSE / COPYING / NOTICE
文本，以及 QtSvg 的 XSVG 声明和 Windows IAccessible2 的版权与许可信息。
为避免遗漏，QtBase 的许可参考文本和第三方声明保留了比本 Windows 构建
实际使用范围更广的集合；其出现不表示程序采用了 Android、Linux 或其他
未分发的功能。适用范围以具体组件的源码声明和 qt_attribution.json 为准，
不是目录内所有许可同时适用于本程序。少数上游 attribution 文件采用 Qt
的扩展 JSON 格式，原文保留，不保证可被严格 JSON 解析器直接读取。

PySide6_Essentials-METADATA.txt、Shiboken6-METADATA.txt 来自官方 PyPI
同版本 Windows x64 wheel，保留版本、项目来源和声明的许可表达式。
原 AramHex 作者的 MIT 许可另外保留于程序的 LICENSE；该 MIT 许可不会
替代或取消本目录第三方组件的许可和版权要求。

2. 对应版本的官方源码

Qt Core / Gui / Network / Widgets：QtBase v6.11.2
  https://github.com/qt/qtbase/tree/v6.11.2
  https://download.qt.io/archive/qt/6.11/6.11.2/submodules/qtbase-everywhere-src-6.11.2.zip

Qt Svg：QtSvg v6.11.2
  https://github.com/qt/qtsvg/tree/v6.11.2
  https://download.qt.io/archive/qt/6.11/6.11.2/submodules/qtsvg-everywhere-src-6.11.2.zip

PySide6 / Shiboken6：两者的源码同属 pyside-setup v6.11.2
  https://github.com/pyside/pyside-setup/tree/v6.11.2
  https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.zip

版本源码目录与归档列表：
  https://download.qt.io/archive/qt/6.11/6.11.2/submodules/
  https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/

源码中的 README、CMakeLists.txt 和构建说明描述构建步骤。Qt for Python
的 README 还说明 Qt 安装路径、Shiboken 与 PySide 的构建配置。
这些链接指向官方相同版本的源码；本次已确认对应 tag 和归档列表可访问，
并下载、逐文件核验本目录采用的小型许可文本。没有下载、校验完整源码
压缩包，也没有据此重编译并证明产物与原作者分发的二进制逐字节一致。
下载源码时应同时取得官方目录提供的校验信息，再核对完整归档。

3. 修改和替换动态库

本程序将 Qt / PySide6 / Shiboken6 作为独立动态库分发，并未将这些 DLL
不可替换地合入 AramHex 主程序。需要使用自己修改后的兼容库时：

  a. 退出助手，并备份助手目录；无需修改游戏文件。
  b. 依据上述对应版本源码及官方构建说明，构建兼容的 Windows x64 库。
     应匹配 Qt、PySide6、Shiboken6 的版本、ABI、编译器运行时和所需插件。
     官方绑定 wheel 为 cp310-abi3-win_amd64；本程序随附 Python 3.13
     运行时。版本号相同不代表任意自编译产物必定 ABI 兼容。
  c. 在 _internal/PySide6/ 替换相应 Qt DLL、PySide 运行库、.pyd 和需要的
     插件；在 _internal/shiboken6/ 替换匹配的 Shiboken DLL 和 .pyd。
     保持依赖库完整和原有相对目录结构，避免混用不兼容的新旧库。
  d. 启动助手验证窗口、显示、网络和识别功能；有兼容问题时恢复备份。

本项目不限制 LGPL 许可允许的库修改，以及为调试这些修改而进行的
反向工程。更新器在使用者同意更新后可能按官方发布文件清单替换改过的
库；自行替换库的使用者应保留备份，并在接受更新前检查该版本变化。
本说明不是对任意替换 DLL 都可以工作的技术保证。

4. 来源与核验范围

UPSTREAM_FILES.json 记录许可原文的官方 URL、Git blob 标识和 SHA-256。
SOURCE_PROVENANCE.json 记录 wheel 元数据来源及本次部分读取的核验范围。
SHA256SUMS.txt 是本目录交付文件的 SHA-256 清单。

官方 wheel 使用少量 HTTPS Range 请求提取许可和元数据，提取项通过 ZIP
CRC 校验；没有下载整个 wheel，因此不能把 PyPI 公布的整包 SHA-256
说成本次已完成的整包校验。第三方声明依据对应 Qt 版本的源码整理，
未取得原作者这份构建的完整编译选项或逐组件构建清单，不能据此声称
已经证明每项源码到最终二进制的对应关系。本目录保留已核对的许可和
取得源码的方法，不作超出这些核验范围的完整法律合规保证。
