# 开发与兼容规则

本项目同时支持 macOS 与 Windows 10/11。两平台共用业务逻辑、网页界面和数据格式，原生能力通过平台层接入；后续改动须遵守 [AGENTS.md](AGENTS.md)。

## 改动应该放在哪里

| 能力 | 位置 | 要求 |
| --- | --- | --- |
| 文件句柄、目录遍历、原子发布和可用空间 | `portable_fs.py` | POSIX 保持原有实现，Windows 使用相对目录句柄；保留不跟随链接、不覆盖目标 |
| 跨进程文件锁 | `portable_lock.py` | POSIX 使用 flock，Windows 使用 LockFileEx；关闭句柄自动释放 |
| 选目录、默认打开、定位、回收站 | `system_integration.py` | 两平台用户行为一致；回收失败保留原件 |
| 图片/视频后端选择与启动 | `media_backend.py` | macOS 优先原生后端；Windows 使用声明的 Python/FFmpeg 后端 |
| 扫描、分类、标签、计划、候选篮 | 共用 Python 业务模块 | 不直接增加平台专用依赖；路径与序列化分开处理 |
| 页面交互 | 共用 HTML/JS | 使用通用文件管理器与清理术语，支持两平台路径显示 |

## 每次功能改动的验证

1. 确认同一功能在两平台的入口、正常流程、取消和失败处理；需要原生接口时一并实现两平台适配。
2. 更新使用临时样例的行为测试；既验证正常结果，也验证相关保护仍有效。禁止测试访问真实媒体或真实回收站。
3. 本机运行测试，更新 README 的依赖与使用方式，并在 PR 中说明实际验证的平台。
4. 等待 GitHub Actions 的 macOS/Windows、Python 3.9/3.12/3.14 全部通过。汇总检查 `Dual-platform compatibility` 成功才允许合并。

macOS 本机可运行 `python3 -m unittest -v`，先编译原生图片与视频组件；测试可移植媒体后端时另安装 `requirements-windows.txt` 中适用的平台依赖。
Windows 先运行 `安装Windows依赖.bat`，再运行 `.\.venv\Scripts\python.exe -X utf8 -m unittest -v`。
GitHub Actions 会自动安装媒体测试依赖并编译 Mac 后端。缺少符号链接权限可以明确跳过符号链接样例，Windows 目录联接测试仍应运行。

## 将兼容检查设为 GitHub 合并硬性条件

仓库中的规则和 PR 检查项提醒开发者；**GitHub 分支保护/Ruleset 才能阻止绕过测试合并**。
仓库维护者在 GitHub 的 **Settings → Rules → Rulesets** 中给 `main` 配置保护规则：

- 要求通过 Pull Request 合并。
- 启用 **Require status checks to pass**，添加 **`Dual-platform compatibility`**。这个名称来自 workflow 的汇总 job，只有整个两平台矩阵成功才会通过；测试失败、取消或跳过都会使汇总失败。
- 要求分支与目标分支保持更新，并尽可能关闭常规开发者的绕过权限和直接推送。

汇总检查需要 workflow 至少运行一次后才能从 GitHub 的已识别检查列表中选择。仓库文件不能自行开启 GitHub 端的保护设置；需要仓库管理权限配置。本地添加 workflow 不表示远程检查已通过或保护已开启。

## 支持范围的调整

不得通过删掉一个平台、缩小测试矩阵、改为允许失败、取消安全检查来修复红色 CI。确需调整系统或 Python 支持范围时，先由维护者明确决定，同步修改 README、AGENTS、依赖和 CI，并在 PR 描述中说明用户影响。
