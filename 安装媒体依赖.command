#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  print -u2 '找不到 Python 3，请安装 Python 3.9 或更新版本。'
  read -r 'answer?按回车关闭窗口。'
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv || exit 1
fi
./.venv/bin/python -m pip install -r requirements.txt
result=$?
if (( result == 0 )); then
  print '安装完成。双击“媒体整理助手.app”或“开始扫描.command”使用双端一致模式。'
else
  print -u2 '安装未完成，请查看上面的错误；原媒体没有改动。'
fi
read -r 'answer?按回车关闭窗口。'
exit $result
