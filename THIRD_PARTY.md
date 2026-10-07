# Desktop dependency notices

The desktop build includes the dependency distributions' LICENSE/COPYING/NOTICE files under `licenses/`, plus `FFmpeg-license.txt` obtained from the shipped executable. Preserve those files when redistributing an app.

The project implements its own media management logic. JavBoss and JvedioNext are design references; their source code is not copied into these apps.

Source projects and license information:

- Python: https://www.python.org/downloads/source/ (PSF license).
- Pillow: https://github.com/python-pillow/Pillow (HPND and bundled library notices).
- pillow-heif/libheif: https://github.com/bigcat88/pillow_heif and https://github.com/strukturag/libheif (BSD-3-Clause / LGPL and decoder notices).
- imageio-ffmpeg and binary build recipes: https://github.com/imageio/imageio-ffmpeg . FFmpeg's upstream source: https://ffmpeg.org/download.html . The shipped executable's `-L` output determines the applicable LGPL/GPL configuration; do not assume a permissive license for the binary. Preserve the corresponding source/build information when redistributing.
- pywebview: https://github.com/r0x0r/pywebview (BSD-3-Clause).
- PyInstaller: https://github.com/pyinstaller/pyinstaller (GPL with the bootloader exception).
- PyObjC: https://github.com/ronaldoussoren/pyobjc (MIT).
- Python.NET: https://github.com/pythonnet/pythonnet (MIT).
- Send2Trash: https://github.com/arsenetar/send2trash (BSD-3-Clause).
- pywin32: https://github.com/mhammond/pywin32 (PSF-based license).

Third-party versions are declared in requirements files and resolved by the platform build. Installer artifacts are currently intended for the owner's private use; distribution beyond that requires preserving all dependency license and corresponding-source obligations.
