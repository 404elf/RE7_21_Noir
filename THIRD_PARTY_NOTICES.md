# 第三方组件 / Third-party notices

项目 MIT 协议只覆盖自有部分。下列组件保留各自许可；同目录 `licenses/` 附上原文。玩家包将这些文件放在 `docs/` 下。

| 组件 | 版本 / 许可 | 来源 |
| --- | --- | --- |
| Python | 3.12；PSF 及其许可历史 | https://www.python.org/downloads/release/python-31210/ |
| pygame-ce | 2.5.8；LGPL 2.1 或之后版本 | https://github.com/pygame-community/pygame-ce/tree/2.5.8 |
| SDL、SDL_image、SDL_mixer、FreeType、图像和音频编解码组件 | 按各自许可，见 `licenses/` | 随 pygame-ce 的 Windows 发行包提供 |
| pygame 附带的备用字体 FreeSans Bold | 内嵌版权：2002 Free Software Foundation；内嵌声明：GNU GPL，见 `licenses/FreeSans-NOTICE.txt` | https://www.gnu.org/software/freefont/ |
| PyInstaller 引导程序 | GPL 2.0 或之后版本，带发行例外 | https://pyinstaller.org/en/stable/license.html |

这里同时保留 pygame-ce 2.5.8 源码包提供的第三方许可集合，并不表示集合中每个可选组件都被本程序调用或分发。pygame-ce 源码包来自 PyPI，SHA-256：`3c8e69088ead310037972c391306ea58e74d7296b35d1890067749235fd554ba`。

pygame-ce 的源码、构建材料与版本历史可从上述版本链接取得。该依赖未在本项目中修改。完整项目源码可直接以 Python 运行，也可用 `build.ps1` 重新构建；Windows 包采用目录形式保留动态库。你可按 LGPL 为调试依赖修改而替换依赖或重新构建，本项目不附加禁止此类修改或逆向调试的限制。

宣传视频的 FFmpeg 编码工具仅用于制作，未随玩家包分发。宣传片使用本项目生成的画面和合成声音，没有导入商业歌曲、原游戏音效或原游戏录像。

The MIT license does not replace the licenses of bundled dependencies. License texts from the pygame-ce 2.5.8 source distribution and the local Python distribution are retained in `licenses/`. The unmodified dependency sources and build materials are available from the links above. The application is distributed as a directory with dynamic libraries and can also run or be rebuilt from source. No restriction is imposed on replacing the LGPL dependency or reverse-engineering to debug modifications to that dependency.
