# AGENTS.md — baobao-apps 桶维护规则

本仓库是 Windows Scoop 桶（manifests 位于 `bucket/`）。以下规则适用于所有新增 / 修改包的工作（含自动化代理）。评审基准以本文件和各 manifest 的 Notes 为准。

## 基本原则

- 优先采用 Scoop 原生机制（`persist`、`installer`、`checkver`、`uninstall -p`），不另造安装、升级、版本和数据管理流程。
- 新增包必须先调查该软件的**默认用户数据位置**（`%APPDATA%` / `%LOCALAPPDATA%` / 安装目录），再决定持久化方案，并写入 manifest 的 Notes。
- 不追求清除 Windows 使用痕迹，不增加全盘或注册表关键词扫描/清理。

## 用户数据（persist + 目录联接）

- 真实数据一律放在 Scoop `persist` 目录；C 盘默认位置只保留指向它的**目录联接（junction）**。
- 联接由包脚本管理：`post_install` 建立/修复，`pre_uninstall` **仅移除联接、绝不删除数据**。
- 数据迁移必须遵循：**先复制 → 逐项核对（文件清单、大小、SHA-256 哈希）→ 通过后才删除源**；核对失败时保留源数据并报错，不宣称成功、不盲目重试覆盖。
- 禁止自动清空数据；禁止合并两处非空数据（必须停止并报告两个位置）；禁止删除/接管来源不明的联接，也不得递归删除联接目标。

## 卸载与版本管理

- 普通卸载：保留 persist 数据，仅移除联接。
- `scoop uninstall -p <app>`：才会删除持久化数据。
- 升级 / 重装（`scoop update -f`、uninstall+install）不得清除用户数据。注意：**升级时旧版本的 `pre_uninstall` / `post_uninstall` 也会执行**，所有钩子脚本必须幂等。
- 版本切换继续使用 Scoop 原生机制；多版本共用持久化数据，不承诺任意跨版本回退都保持软件自身的数据兼容性。

## 进程处理

- 需要停止应用时放在 `pre_uninstall`（及 `pre_install`）：**优先优雅关闭（CloseMainWindow），最多等待 15 秒；仍在运行则中止操作并提示用户关闭**，禁止默认强制结束，避免未保存内容丢失。
- 进程匹配必须按"本包目录前缀"过滤（`受管目录\*`），不得只按进程名结束其他程序。

## 注册表

- 只允许**记录式**修改：安装时记录原状（键是否存在、原值、本包添加的 ACL 规则），卸载时精确撤销；已有键恢复原状态，本包新建的键在确认可删除时清理。
- 不得以整键删除代替恢复；不得移除不属于本包的 Deny 等规则；不得增加其他注册表扫描。

## 版本与自动更新（CI）

- GitHub 上游只接受**最新稳定 Release**（排除草稿/预发布）；安装包必须**精确匹配**（包名/系统/架构/是否含内核），禁止"任意 Windows ZIP"兜底。
- 阻止自动降级；同版本 URL/哈希变化必须人工核验后手工修改。
  - 唯一豁免：`fingerprint-chromium` 允许上游在同一版本下递增构建号（`-x.y`）重发资产，但必须**唯一匹配 x64 ZIP** 且构建号严格递增。
- 更新脚本必须整批检查：**任一包失败则本次不写任何清单并以非零退出**；版本/URL/哈希均未变化时不重写文件。
- 自动更新只创建/更新单个 PR（不直推 main）；`scoop update` 由人工审查合并。
- `typora0.11.18` 为固定版本，不参与自动更新——它是**唯一**适用专属版本使用限制的包。

## 文案与文档

- Notes / README / `docs/` 必须与脚本的实际行为一致；**禁止"换机即用""无残留"等超出实际能力的承诺**。
- 安装修复类命令必须符合 Scoop 实际行为：同版本重复 `scoop install` **不会**重装，应使用 `scoop update -f baobao-apps/<app>`。
- 保留原有的有效说明（用法、参数、路径），仅修正过时内容或追加必要说明。
- 新增 / 移除包或安装行为变化时，同步更新 `README.md` 的收录表与 `docs/` 对应页面。

## 已知平台怪癖

- Start Menu 快捷方式名含非 ASCII 字符（如「星愿浏览器」）时，Scoop 内部的 WScript.Shell 会按**系统 ANSI 代码页**做文件名转换：在非对应代码页的系统（如英文系统的 CI runner）上会保存失败并被跳过——stderr 出现一条非致命错误，**安装本身照常成功**；中文系统上正常创建。属平台限制，无需"修复"；CI 的 E2E 已按此预期放行。
- Scoop 的 `scoop install` 在同版本重复执行时不会重装（提示改用 `scoop update`）；重跑安装钩子必须用 `scoop update -f`。

## 验证要求

提交前必须通过仓库 CI（`.github/workflows/validate.yml`）：

1. `node --check` + `node --test .github/scripts/test/*.test.js`（update.js 逻辑）
2. Scoop Schema 校验（`.github/schema/`，`validate_manifests.py`）
3. 嵌入脚本的 PowerShell AST 语法检查（`check-manifests.ps1`）
4. 钩子夹具测试（`hook-tests.ps1`：迁移/冲突/外来联接/幂等/注册表，全部在沙箱中执行）
5. Windows 端到端生命周期测试（`e2e-test.ps1`：安装/强更/数据联接/卸载）
