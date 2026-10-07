# 开发与兼容规则

本项目同时支持 macOS 与 Windows 10/11。两平台共用业务逻辑、网页界面和数据格式，原生能力通过平台层接入；后续改动须遵守 [AGENTS.md](AGENTS.md)。

## 改动应该放在哪里

| 能力 | 位置 | 要求 |
| --- | --- | --- |
| 文件句柄、目录遍历、原子发布和可用空间 | `portable_fs.py` | POSIX 保持原有实现，Windows 使用相对目录句柄；保留不跟随链接、不覆盖目标 |
| 跨进程文件锁 | `portable_lock.py` | POSIX 使用 flock，Windows 使用 LockFileEx；关闭句柄自动释放 |
| 选目录、默认打开、定位、回收站 | `system_integration.py` | 两平台用户行为一致；回收失败保留原件 |
| 图片/视频后端选择与启动 | `media_backend.py` | 两端默认共用 Pillow/FFmpeg 后端与 requirements.txt；Mac 原生组件仅为明确降级路径 |
| 扫描、分类、标签、计划、候选篮 | 共用 Python 业务模块 | 不直接增加平台专用依赖；路径与序列化分开处理 |
| 变化扫描 | `scan_cache.py` | 只复用身份一致的解析与小图，不缓存查重哈希；保持遍历、完整 SHA-256 与操作前检查 |
| 影片作品、版本和分段 | `movie_grouping.py` | 规则与人工修正共用；跨端报告用纯路径解析；修正只写应用资料 |
| 影片及附件整组复制 | `movie_bundle.py` / `file_operations.py` | 仅扩展明确预览的复制，不扩展清理；附件沿用身份核对、无覆盖、完整副本校验、锁、日志和安全停止 |
| 附件完整性核对 | `movie_attachments.py` | 只读报告；影片详情、封面关联与整组复制共用规则，不读取个人原片来检查 |
| 页面交互 | 共用 HTML/JS | 使用通用文件管理器与清理术语，支持两平台路径显示 |

## 每次功能改动的验证

1. 确认同一功能在两平台的入口、正常流程、取消和失败处理；需要原生接口时一并实现两平台适配。
2. 更新使用临时样例的行为测试；既验证正常结果，也验证相关保护仍有效。禁止测试访问真实媒体或真实回收站。
3. 本机运行测试，更新 README 的依赖与使用方式，并在 PR 中说明实际验证的平台。
4. 等待 GitHub Actions 的 macOS/Windows、Python 3.9/3.12/3.14 全部通过；桌面安装包与原生窗口测试也必须全部通过。汇总检查 `Dual-platform compatibility` 成功才允许合并。

macOS 安装 `requirements.txt` 后运行 `./.venv/bin/python -m unittest -v`，验证共用媒体路径；原生降级测试另需编译 Mac 原生组件。应用启动脚本与资料库入口都必须使用同一项目 `.venv`。
开发环境另需 Node.js 22+ 来测试页面实际使用的 JavaScript；仅测试需要，应用运行不依赖 Node.js。两平台 CI 都显式安装它，不通过跳过浏览行为测试来隐藏差异。
Windows 先运行 `安装Windows依赖.bat`，再运行 `.\.venv\Scripts\python.exe -X utf8 -m unittest -v`。
GitHub Actions 会在两端自动安装同一依赖并编译 Mac 降级后端。保持一套业务与页面代码；不得增加两份扫描或资料库实现来适配平台。缺少符号链接权限可以明确跳过符号链接样例，Windows 目录联接测试仍应运行。

## 将兼容检查设为 GitHub 合并硬性条件

仓库中的规则和 PR 检查项提醒开发者；**GitHub 分支保护/Ruleset 才能阻止绕过测试合并**。
仓库维护者在 GitHub 的 **Settings → Rules → Rulesets** 中给 `main` 配置保护规则：

- 要求通过 Pull Request 合并。
- 启用 **Require status checks to pass**，添加 **`Dual-platform compatibility`**。这个名称来自 workflow 的汇总 job，只有整个两平台矩阵成功才会通过；测试失败、取消或跳过都会使汇总失败。
- 要求分支与目标分支保持更新，并尽可能关闭常规开发者的绕过权限和直接推送。

汇总检查需要 workflow 至少运行一次后才能从 GitHub 的已识别检查列表中选择。仓库文件不能自行开启 GitHub 端的保护设置；需要仓库管理权限配置。本地添加 workflow 不表示远程检查已通过或保护已开启。

## 支持范围的调整

不得通过删掉一个平台、缩小测试矩阵、改为允许失败、取消安全检查来修复红色 CI。确需调整系统或 Python 支持范围时，先由维护者明确决定，同步修改 README、AGENTS、依赖和 CI，并在 PR 描述中说明用户影响。

## 桌面打包

`requirements-desktop.txt` 的原生窗口组件独立于扫描依赖，两端发布包均使用 Python 3.12。不要为 Python 3.14 安装尚不兼容的 pythonnet；该版本源码版继续使用相同的浏览器界面。CI 同时安装验证各版本的适用依赖，在 Mac arm64、Mac x86_64 和 Windows x64 构建，并运行内置媒体与原生窗口检查。安装包只能在对应目标系统生成。应用内不自动安装依赖。
