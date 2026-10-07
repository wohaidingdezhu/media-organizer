#!/bin/zsh
cd -- "${0:A:h}" || exit 1
print '正在打开最近一次影片资料库；标签仅保存在本机报告目录。'
if [[ -x .venv/bin/python ]]; then
  media_python=./.venv/bin/python
else
  media_python=python3
fi
"$media_python" ./media_scan.py --serve-library
result=$?
print '\n按回车关闭窗口。'
read -r answer
exit $result
