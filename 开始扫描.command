#!/bin/zsh
cd -- "${0:A:h}" || exit 1
print '媒体整理助手：只扫描、生成报告，不移动或删除文件。'
if ! command -v python3 >/dev/null 2>&1; then
  print -u2 '找不到 Python 3.9 或更新版本，请先安装 Python。'
  print '按回车关闭窗口。'
  read -r answer
  exit 1
fi
if [[ ! -x native/image_probe || native/image_probe.swift -nt native/image_probe ]]; then
  if command -v swiftc >/dev/null 2>&1; then
    print '首次启动或图片组件源码有更新，正在编译 macOS ImageIO 图片组件…'
    swiftc native/image_probe.swift -o native/image_probe || print -u2 '图片组件编译失败；本次仍可进行文件 SHA-256 查重。'
  else
    print -u2 '未找到 Swift 编译器；本次仍可进行文件 SHA-256 查重。'
  fi
fi
python3 ./media_scan.py --open "$@"
result=$?
print '\n按回车关闭窗口。'
read -r answer
exit $result
