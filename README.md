# baobao-apps

> **声明**：作者水平有限（实则完全不懂😂），因习惯使用 Scoop 管理软件顾开发本项目。本项目由 WorkBuddy（Deepseek-V4.1-Flash）创建执行，项目查错改进方案由 GPT-6.1 Sol 驱动。

[![validate](https://github.com/baobao666-bgm/baobao-apps/actions/workflows/validate.yml/badge.svg)](https://github.com/baobao666-bgm/baobao-apps/actions/workflows/validate.yml)
[![auto-update](https://github.com/baobao666-bgm/baobao-apps/actions/workflows/auto-update.yml/badge.svg)](https://github.com/baobao666-bgm/baobao-apps/actions/workflows/auto-update.yml)
[![license: 0BSD](https://img.shields.io/badge/license-0BSD-blue)](LICENSE)

面向 Windows 的 [Scoop](https://scoop.sh) **补充桶（supplementary bucket）**：为了不重复造轮子，本项目收录主流桶（Main、Extras、Versions 等）没有收录的软件，或同一软件的特定变体 / 免费旧版本，为主流桶查漏补缺。

## 为什么用这个桶

收录遵循三条原则：

- **主流桶没有的软件**：不在 Main / Extras 收录范围内的工具（如指纹浏览器内核）；
- **主流桶没有的变体**：特定区域版本、需要固定旧版本、双架构等场景；
- **需要额外适配的软件**：安装时需要数据持久化、目录兼容修补等处理，才能装得干净、卸得干净。

本桶与主流桶互补而非替代——建议同时保留 `main` / `extras`，在本桶查找它们没有的软件或变体。

## 如何安装

要求：Windows 10 / 11（x64；部分包提供 32 位变体）；已安装 Scoop。

```powershell
# 添加本桶（公开仓库，无需 token）
scoop bucket add baobao-apps https://github.com/baobao666-bgm/baobao-apps

# 安装（<应用名> 见下方「收录的软件」）
scoop install baobao-apps/<应用名>

# 例：安装 32 位变体
scoop install baobao-apps/typora0.11.18 --arch 32bit
```

安装完成后可用的命令：

- `chrome` → fingerprint-chromium 指纹内核
- `OpenBrowser` → OpenBrowser 管理器
- `twinkstar` → 星愿浏览器（开始菜单快捷方式在非中文代码页的系统上会被跳过，不影响使用）
- `Typora` → Typora Markdown 编辑器

用户数据统一持久化在 Scoop `persist` 目录：普通卸载保留数据，`scoop uninstall -p` 才彻底删除——详见 [数据与卸载](docs/data-and-uninstall.md)。

## 收录的软件

| 包 | 上游 | 说明 | 更新策略 | 许可 |
|---|---|---|---|---|
| `fingerprint-chromium` | [adryfish/fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium) | 指纹浏览器内核：基于 Ungoogled Chromium 的 C++ 级指纹补丁，32 位 seed 确定性指纹，可伪装成真 Chrome | 跟随上游自动更新 | BSD-3-Clause |
| `OpenBrowser` | [sheying2013/OpenBrowser](https://github.com/sheying2013/OpenBrowser)（原 lyu0805/OpenBrowser） | 指纹浏览器管理器：多环境隔离、代理 / 指纹 / 扩展管理、窗口同步、本地 RPA、Local API + MCP（仅监听本机回环） | 跟随上游自动更新 | MIT |
| `Twinkstar` | [星愿浏览器官网](https://www.twinkstar.com/) | 星愿浏览器：Chromium 内核，内置漫画阅读模式、视频下载 / 弹窗播放、鼠标手势、超级拖拽，兼容 Chrome 扩展 | 跟随上游自动更新 | 免费软件（Freeware） |
| `typora0.11.18` | [typora.io](https://typora.io/) | Markdown 编辑器 0.11.18 免付费旧版（64 位 / 32 位）：所见即所得，内置过期弹窗修复与更新提示屏蔽 | 固定版本，不参与自动更新 | Proprietary（上游） |

## 使用说明

- **数据与卸载**：真实数据在 `<scoop根目录>\persist\`，C 盘只保留目录联接；普通卸载保留，`-p` 彻底删除 → [docs/data-and-uninstall.md](docs/data-and-uninstall.md)
- **fingerprint-chromium**：指纹参数与直启用例 → [docs/fingerprint-chromium.md](docs/fingerprint-chromium.md)
- **OpenBrowser**：指向指纹内核、代理建议、安装目录兼容修补说明 → [docs/openbrowser.md](docs/openbrowser.md)
- **Twinkstar**：解包说明、GPU 闪退排障、快捷方式平台限制 → [docs/twinkstar.md](docs/twinkstar.md)
- **typora0.11.18**：过期弹窗修复原理、profile.data 处理、双架构 → [docs/typora.md](docs/typora.md)
- **更新**：`scoop update *` 跟随上游更新；`typora0.11.18` 为固定版本，可用 `scoop update -f baobao-apps/typora0.11.18` 重装以重新应用修补

## 参与贡献

仓库结构：

```
bucket/                 # 清单文件（每个软件一个 <name>.json）
.github/workflows/      # CI：validate（只读校验）、auto-update（每日更新检查）
.github/scripts/        # 更新脚本与其测试
docs/                   # 使用文档
AGENTS.md               # 维护规则（新增清单、数据持久化约定等）
```

新增收录的要求（完整规则见 [AGENTS.md](AGENTS.md)）：

- 先调查应用的**默认用户数据位置**，参照现有包把真实数据持久化到 Scoop `persist`；
- 不合并两处非空数据、不接管来源不明的目录联接、不自动清空数据；
- 安装脚本必须幂等，升级 / 重装不得删除用户数据。

提交方式：欢迎通过 issue 提议新包（说明来源与理由），或 fork 后直接提交清单 PR。main 分支启用保护，须经 PR 且校验全绿方可合并。CI 会自动检查：

- 清单 JSON + Scoop Schema；
- 更新脚本单元测试；
- 嵌入脚本的 PowerShell 语法检查；
- 钩子夹具测试（迁移 / 冲突 / 外来联接 / 幂等 / 注册表，沙箱中执行）；
- Windows 端到端测试（安装 / 强制更新 / 数据联接 / 卸载）。

**自动更新**：CI 每日 06:00 UTC 检查各上游最新版本（GitHub Releases 与星愿官网），全部包检查通过后创建 / 更新一个更新 PR（`bot/update-manifests`，只包含 `bucket/` 变更）等待人工合并；**任一包检查失败（下载异常、哈希 / digest 不符、资产歧义、疑似降级或同版本换包）都不会产生任何提交**。也可在 [Actions](https://github.com/baobao666-bgm/baobao-apps/actions) 页面手动运行 workflow 立即刷新。

## 许可

本桶（清单 / 脚本 / CI 配置）采用 **0BSD**，见 [LICENSE](LICENSE)。各软件版权归上游所有：

- `fingerprint-chromium` — BSD-3-Clause（[上游](https://github.com/adryfish/fingerprint-chromium)）
- `OpenBrowser` — MIT（[上游](https://github.com/sheying2013/OpenBrowser)）
- `Twinkstar` — 免费软件（[官网](https://www.twinkstar.com/)）
- `typora0.11.18` — Proprietary（[上游](https://typora.io/)）
