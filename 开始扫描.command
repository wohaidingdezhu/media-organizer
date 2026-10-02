#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  print -u2 '找不到 Python 3，请安装 Python 3.9 或更新版本。'
  print '按回车关闭窗口。'
  read -r answer
  exit 1
fi
if (( $# == 0 )); then
  python3 ./media_gui.py
else
  python3 ./media_scan.py --edit-tags "$@"
fi
result=$?
if (( result != 0 )); then
  print "程序以状态 $result 结束。按回车关闭窗口。"
  read -r answer
fi
exit $result
