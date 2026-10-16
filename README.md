# baobao-apps — 浏览器工具集的 Scoop 桶（Windows）

一个 Scoop bucket，把「指纹浏览器内核 + 本地管理器 + 常用浏览器 + Markdown 编辑器」封装成可一键安装的四个包。**用户数据统一持久化在 Scoop `persist` 目录**（C 盘只保留目录联接），普通卸载保留数据、`scoop uninstall -p` 才彻底删除；升级 / 重装不影响数据。其中 `typora0.11.18` 为静态免付费旧版（64 位 / 32 位双架构），不参与自动更新。

| 包 | 上游 | 作用 | License |
|---|---|---|---|
| `fingerprint-chromium` | [adryfish/fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium) | 指纹内核：基于 Ungoogled Chromium 的 C++ 级指纹补丁，32 位 seed 确定性指纹，可伪装成真 Chrome（User-Agent / Client Hints 均报 Chrome） | BSD-3-Clause |
| `OpenBrowser` | [sheying2013/OpenBrowser](https://github.com/sheying2013/OpenBrowser)（原 lyu0805/OpenBrowser） | 管理器：多环境隔离、代理 / 指纹 / 扩展管理、窗口同步、本地 RPA、Local API + MCP（仅监听本机回环 `127.0.0.1:50325`），内置 Wayfern 内核开箱即用 | MIT |
| `Twinkstar` | [www.twinkstar.com](https://www.twinkstar.com/)（星愿浏览器官网） | 星愿浏览器：Chromium 内核，内置漫画阅读模式、视频下载 / 弹窗播放、鼠标手势、超级拖拽、多线程下载，兼容 Chrome 扩展 | Freeware（免费软件） |
| `typora0.11.18` | [typora.io](https://typora.io/)（0.11.18 免付费旧版） | Markdown 编辑器：所见即所得（WYSIWYG）；64 位 / 32 位双架构，内置过期弹窗修复与更新提示屏蔽；用户数据持久化到 Scoop `persist` | Proprietary（上游） |

## 前置要求

- Windows 10 / 11（x64；typora0.11.18 另有 32 位版，安装方式见下）
- 已安装 [Scoop](https://scoop.sh)（桶内包均为便携版，Scoop 根目录放哪个盘都能用）

## 安装

```powershell
# 1. 添加本桶（公开仓库，无需 token）
scoop bucket add baobao-apps https://github.com/baobao666-bgm/baobao-apps

# 2. 安装
scoop install baobao-apps/fingerprint-chromium   # 指纹内核（小，秒装）
scoop install baobao-apps/OpenBrowser            # 管理器（约 1.1 GB，稍慢）
scoop install baobao-apps/Twinkstar              # 星愿浏览器（约 125 MB）
scoop install baobao-apps/typora0.11.18          # Typora Markdown 编辑器（约 68 MB，64 位，内置过期修复）
scoop install baobao-apps/typora0.11.18 --arch 32bit   # 如需 32 位版本
```

装完即可获得以下入口：

- `chrome` → fingerprint-chromium 的 `chrome.exe`（指纹内核，可加指纹参数直启）
- `OpenBrowser` → 管理器主程序
- `twinkstar` → 星愿浏览器主程序（开始菜单同时会有「星愿浏览器」快捷方式）
- `typora` → Typora Markdown 编辑器主程序（开始菜单同时会有「Typora」快捷方式）

## 数据与卸载

各包的真实数据统一持久化在 `<scoop根目录>\persist\<包名>\`，C 盘默认位置只保留指向它的目录联接：

| 包 | C 盘联接位置 | 实际数据位置 |
|---|---|---|
| `OpenBrowser` | `%APPDATA%\openbrowser` | `<scoop根目录>\persist\OpenBrowser\UserData` |
| `Twinkstar` | `%LOCALAPPDATA%\Twinkstar` | `<scoop根目录>\persist\Twinkstar\UserData` |
| `fingerprint-chromium` | `%LOCALAPPDATA%\Chromium\User Data` | `<scoop根目录>\persist\fingerprint-chromium\UserData` |
| `typora0.11.18` | `%APPDATA%\Typora` | `<scoop根目录>\persist\typora0.11.18\TyporaData` |

- **首次安装**：若对应 C 盘位置已存在真实数据，安装脚本会先复制到 persist、逐项核对（文件清单 / 大小 / SHA-256）后再删除源目录并建立联接；核对失败会保留源数据并报错。
- **两处都有数据**时脚本会拒绝合并并停止（列出两个位置），需要你手动处理后重试。
- **普通卸载**：仅移除 C 盘联接，数据保留在 persist；`scoop uninstall -p <app>` 才会连数据一起删除。
- **升级 / 重装**（`scoop update`、`scoop update -f`）不会删除数据。
- 若从**旧版本**（数据在安装目录、尚无 persist 的版本）升级：请直接用 `scoop update -f baobao-apps/<app>`（会自动把旧数据迁入 persist）；**不要先卸载再安装**——旧版卸载会按旧语义删除数据。

## 把 OpenBrowser 的内核指向指纹内核

OpenBrowser 自带 Wayfern 内核，不配置也能用。若想使用 C++ 级指纹、可报 `brand=Chrome` 的 fingerprint-chromium：

```powershell
# 查看内核安装路径（形如 <scoop根目录>\apps\fingerprint-chromium\current）
scoop dir fingerprint-chromium
```

1. 启动 `OpenBrowser` → 左侧「本地设置」→「独立浏览器内核」区
2. 点「选择自定义内核」→ 选择上一步目录下的 `chrome.exe`
3. 勾选「优先使用独立内核」，取消「允许手动回退到本机浏览器」

## 指纹参数（fingerprint-chromium 直启场景）

```
chrome.exe --fingerprint <32位seed> --fingerprint-platform windows --fingerprint-brand Chrome --timezone Asia/Shanghai
```

- 同一账号请始终使用同一个 seed（seed 变化 = 换了一个浏览器身份，易触发风控）
- `--brand Chrome` 使指纹以真实 Chrome 上报，适合 IG / FB / Google 等网页
- `--timezone` 请与代理 IP 所在地时区保持一致

## 代理

OpenBrowser 中建议每个环境绑定**独立住宅代理**，并保证 IP 地理位置、时区、语言三者与指纹一致。管理器内置代理管理 / 代理检测页面。

## 更新与维护

```powershell
scoop update *    # 跟随上游 release 自动更新
scoop checkup     # 健康检查
```

- **自动更新（CI）**：本桶的 GitHub Actions 每天 06:00 UTC 检查各上游最新版本（GitHub Releases 与星愿官网，官网抓取带浏览器 UA）。全部包检查通过后，CI 会创建或更新一个**更新 PR**（`bot/update-manifests`，只包含 `bucket/` 下的清单变更），由人工审查后合并；**任一包检查失败（下载异常、哈希/digest 不符、资产歧义、疑似降级或同版本换包）都不会产生任何提交**。也可在 [Actions](https://github.com/baobao666-bgm/baobao-apps/actions) 页面手动 Run workflow 立即刷新。
- **质量保障（CI）**：每个 PR 与 main 推送都会运行只读验证——清单 JSON + Scoop Schema 校验、更新脚本单元测试、嵌入脚本的 PowerShell 语法检查、钩子夹具测试（迁移 / 冲突 / 外来联接 / 幂等 / 注册表，全部在沙箱中执行）以及 Windows 端到端生命周期测试（安装 / 强制更新 / 数据联接 / 卸载）。
- **安装目录兼容修补（OpenBrowser）**：安装时会自动修补主程序，使其在以 junction / 符号链接方式链接安装目录的环境（例如 Scoop 的 `current` 目录）中正常工作，避免「本地设置」页按钮报错 `untrusted IPC document`。修补要求上游代码的锚点全部匹配才会写回；**上游结构变化时会中止安装并提示更新本包**（不会写入"半修补"状态）。`scoop update -f` 重装时会自动重新应用。
- **内置修复说明（typora0.11.18）**：安装时预置注册表 `HKCU\SOFTWARE\Typora` 的 `IDate` 为远期时间并设置对应的拒绝权限，用于消除「This beta version of Typora is expired」过期弹窗——所有改动都会被记录，卸载时**按记录精确撤销**（已有键恢复原状、本包新建的键才清理），不会误删其他值。同时只修改 `%APPDATA%\Typora\profile.data` 中必要字段（关闭「自动检查更新」、跳过当前最新版本；文件无法解析时保持原样并提示）。**数据说明**：`%APPDATA%\Typora` 为指向 `<scoop根目录>\persist\typora0.11.18\TyporaData` 的目录联接，C 盘不保存实际数据；普通卸载保留数据，`-p` 才会删除。本包提供 64 位与 32 位两种架构（32 位用 `--arch 32bit` 安装）；因上游 0.11.18 为静态免付费旧版，本包不参与 CI 自动更新。
- **星愿浏览器解包说明**：星愿的安装包是带签名尾部的 NSIS（7-Zip 无法自动识别格式），manifest 以 `-tNsis` 显式解包并取出其中的完整程序文件，安装过程不写注册表、不弹安装向导。

## 常见问题

- **「本地设置」页按钮无反应 / 报 `untrusted IPC document`**：安装或更新会自动应用上述修补；若仍出现，请完全退出 OpenBrowser 后执行 `scoop update -f baobao-apps/OpenBrowser` 重新应用（注意：普通重复 `scoop install` 不会重装，Scoop 会提示已安装）。
- **更新 / 卸载时报文件占用**：先通过托盘图标完全退出对应应用（含托盘），再重试；脚本也会尝试优雅关闭，等待 15 秒仍未退出时会中止并提示。
- **内核状态区一直「未选择」**：需先在 OpenBrowser 内完成一次内核下载或自定义内核选择，管理器才会真正调度指纹内核。
- **星愿浏览器一启动就闪退（日志报 `GPU process exited unexpectedly`）**：多见于旧显卡驱动或精简系统（GPU 进程沙箱初始化失败）。建议先升级显卡驱动、检查系统更新；确需临时绕过时，可在快捷方式「目标」末尾追加 `--disable-gpu-sandbox`（或命令行执行 `twinkstar --disable-gpu-sandbox`）——该参数会降低渲染进程隔离，仅作排障用，不建议长期使用。
- **数据在哪？想彻底删掉怎么办？**：见上文「数据与卸载」表格；彻底删除用 `scoop uninstall -p <app>`（并注意 C 盘联接会被一并移除）。

## 许可

- 本桶（manifest / 脚本 / CI）：**0BSD**
- `fingerprint-chromium`：BSD-3-Clause（上游）
- `OpenBrowser`：MIT（上游）
- `Twinkstar`：免费软件（上游）
- `typora0.11.18`：Proprietary（上游旧版，64 位 / 32 位双架构）
