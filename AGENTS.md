# AGENTS.md — baobao-apps 桶维护规则

本仓库是 Windows Scoop 桶（manifests 位于 `bucket/`）。以下规则适用于所有新增 / 修改包的工作（含自动化代理）。评审基准以本文件和各 manifest 的 Notes 为准。

## 基本原则

- 优先采用 Scoop 原生机制（`persist`、`installer`、`checkver`、`uninstall -p`），不另造安装、升级、版本和数据管理流程。
- 新增包必须先调查该软件的**默认用户数据位置**（`%APPDATA%` / `%LOCALAPPDATA%` / 安装目录），再决定持久化方案，并写入 manifest 的 Notes。
- 不追求清除 Windows 使用痕迹，不增加全盘或注册表关键词扫描/清理。

## 用户数据（persist + 目录联接）

- 真实数据一律放在 Scoop `persist` 目录；C 盘默认位置只保留指向它的**目录联接（junction）**。
- 联接由包脚本管理：`post_install` 建立/修复，`pre_uninstall` **只移除联接本身**；数据留在 `persist`，只有 `scoop uninstall -p` 会删数据（那是 Scoop 的行为，不是钩子）。
- 数据迁移必须遵循：**先扫描嵌套联接 → 再复制 → 逐项核对（文件清单、大小、SHA-256 哈希）→ 通过后才删除源**。源目录里发现任何联接/符号链接即中止并列出全部路径：`robocopy /XJ` 会静默跳过它们，校验也会跟着"失明"，最终联接凭空消失。核对失败时保留源数据并报错，不宣称成功、不盲目重试覆盖。
- 禁止自动清空数据；禁止合并两处非空数据（必须停止并报告两个位置）；禁止删除/接管来源不明的联接；不得递归删除联接目标（已实测：PowerShell 5.1 的 `Remove-Item -Recurse` 不跟随联接，但代码仍不应依赖这一点去删别人的数据）。
- 卸载时若本包的联接删不掉：重试后**中止卸载**并给出 `rd "<路径>"` 手工命令，不留下指向空路径的悬空入口。

### 外来联接（该位置已被别的工具建成联接）

| 情况 | 处理 |
|---|---|
| 联接指向本包 `persist` 或旧版本目录 | 正常修复/迁移，属本包管理范围 |
| 联接指向**任何其他目标** | 不改指向、不迁移、不删除，**也不得写穿它**（不得往联接目标里写任何文件） |
| 同上，且用户显式要求硬失败 | 环境变量 `BAOBAO_STRICT_FOREIGN_LINK=1` → 在任何修改之前中止安装 |

- 通用三包（OpenBrowser / Twinkstar / fingerprint-chromium）遇到外来联接只提示并继续安装，因为它们没有"写穿"副作用；数据不会进入本包 persist，这一点必须写进 Notes 与 `docs/`。
- `typora0.11.18` 是唯一带"写穿"风险的包（`profile.data` 位于 `%APPDATA%\Typora` 之下）：外来联接时**跳过 `profile.data` 修改**，只保留让它能启动的注册表修复——不要为了统一行为把这个注册表修复也删掉，删了软件就无法启动。
- 任何"数据一定进入 persist""C 盘不保存实际数据"的表述都必须带外来联接的限定条件。

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
- 记录文件（`<persist>\.registry-state.json`）是"哪些规则属于本包"的**唯一依据**：记录损坏、schema 未知、或键打不开时**不猜、不重推**，保留文件并中止安装/卸载并给出人工处理命令。
- 撤销必须回读核验（规则是否存在、值是否存在、值类型是否一致）。规则没撤干净要中止；`IDate` 这类值恢复失败则**如实报告并保留记录**、不中止——它通常是被他人 ACL 挡住，中止只会让卸载卡死。
- 先撤销注册表、再删 `%APPDATA%` 联接：这样中止时应用仍是完整可用的状态，而不是"应用还在、数据入口没了"。

## 版本与自动更新（CI）

- GitHub 上游只接受**最新稳定 Release**（排除草稿/预发布）；安装包必须**精确匹配**（包名/系统/架构/是否含内核），禁止"任意 Windows ZIP"兜底。
- 阻止自动降级；同版本 URL/哈希变化**一律停止**并要求人工核验——包括上游在同一版本下递增构建号（`-x.y`）重发资产的情况：版本号不变时 Scoop 看不到升级，写进清单对已安装用户是空操作。
- 更新脚本必须整批检查：**任一包失败则本次不写任何清单并以非零退出**；版本/URL/哈希均未变化时不重写文件。
- 自动更新只创建/更新单个 PR（不直推 main）；`scoop update` 由人工审查合并。
- `auto-update` 默认用 `GITHUB_TOKEN`：由它产生的 `pull_request` 事件不会启动工作流，bot PR 的必需检查因此不会上报，PR 会一直停在 `blocked`。**手动 `workflow_dispatch` 补跑没有用**——检查会显示通过，但分支保护不认（实测合并返回 "required status checks are expected"）。
  - 推荐：在仓库 Secrets 里加 `AUTO_UPDATE_TOKEN`（细粒度 PAT，`contents: write` + `pull-requests: write`）。工作流的 **checkout、`git push`、`gh pr create` 三处都改用它**：只给 `gh` 配 PAT 是不够的，`git push` 走 `GITHUB_TOKEN` 时，PR 分支的 `synchronize` 事件同样被抑制，第二次更新起 validate 不会再跑。
  - 没有 PAT 时：在 PR 页点一次 "Approve workflows to run"，让那条 `action_required` 的 run 执行。
- 失败通知放在**所有发布步骤之后**，并按阶段标记（check / push / pr）区分，且同时覆盖 `failure()` 与 `cancelled()`。
- 单包下载预算 15 分钟 × 4 个包串行 < job 的 `timeout-minutes`：调小或调大任何一边都要重新核对这个不等式，否则超时表现为"取消"，最容易漏通知。
- `typora0.11.18` 为固定版本，不参与自动更新——它是**唯一**适用专属版本使用限制的包。

## 文案与文档

- Notes / README / `docs/` 必须与脚本的实际行为一致；**禁止"换机即用""无残留"等超出实际能力的承诺**。
- 安装修复类命令必须符合 Scoop 实际行为：同版本重复 `scoop install` **不会**重装，应使用 `scoop update -f baobao-apps/<app>`。
- 保留原有的有效说明（用法、参数、路径），仅修正过时内容或追加必要说明。
- 新增 / 移除包或安装行为变化时，同步更新 `README.md` 的收录表与 `docs/` 对应页面。

## 已知平台怪癖

- Start Menu 快捷方式名含非 ASCII 字符（如「星愿浏览器」）时，Scoop 内部的 WScript.Shell 会按**系统 ANSI 代码页**做文件名转换：在非对应代码页的系统（如英文系统的 CI runner）上会保存失败并被跳过——stderr 出现一条非致命错误，**安装本身照常成功**；中文系统上正常创建。属平台限制，无需"修复"；CI 的 E2E 已按此预期放行。
- Scoop 的 `scoop install` 在同版本重复执行时不会重装（提示改用 `scoop update`）；重跑安装钩子必须用 `scoop update -f`。

## 清单脚本的唯一源文件

`bucket/*.json` 里那几百行 PowerShell 是**生成产物**，源文件只有两个：

- `tools/manifests/ps_snippets.py`（钩子脚本文本）
- `tools/manifests/make_manifests.py`（拼装清单 + 架构字段 + Notes）

改钩子脚本 = 改 `ps_snippets.py` → `python tools/manifests/make_manifests.py` → 提交两者。**不要直接手改 JSON**：下一次生成会静默覆盖，CI 也会因为对不上而失败（`--check`）。审阅时请指向 `tools/manifests/`，不要指向压缩成单行的清单文件。

## 验证要求

提交前必须通过仓库 CI（`.github/workflows/validate.yml`）：

1. `node --check` + `node --test .github/scripts/test/*.test.js`（update.js 逻辑）
2. `python tools/manifests/make_manifests.py --check`（清单必须等于生成器输出）
3. Scoop Schema 校验（`.github/schema/`，`validate_manifests.py`）
4. 嵌入脚本的 PowerShell AST 语法检查（`check-manifests.ps1`）
5. 钩子夹具测试（`hook-tests.ps1`：迁移/冲突/外来联接/幂等/注册表，全部在沙箱中执行）
6. Windows 端到端生命周期测试（`e2e-test.ps1`：安装/强更/数据联接/卸载）

本地在 Windows 上跑 `hook-tests.ps1` / `check-manifests.ps1` 时，请显式传路径参数（`-RepoRoot` / `-BucketDir`）：用 `powershell -File` 从某些宿主调用时 `$PSScriptRoot` 为空，参数默认值会先于脚本执行而报错。
