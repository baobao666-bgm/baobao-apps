# baobao-apps — 开源指纹浏览器套件的 Scoop 桶（Windows）

一个 Scoop bucket，把「指纹浏览器内核 + 本地管理器」封装成可一键安装、可自动更新、换机即用的两个包：

| 包 | 上游 | 作用 | License |
|---|---|---|---|
| `fingerprint-chromium` | [adryfish/fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium) | 指纹内核：基于 Ungoogled Chromium 的 C++ 级指纹补丁，32 位 seed 确定性指纹，可伪装成真 Chrome（User-Agent / Client Hints 均报 Chrome） | BSD-3-Clause |
| `OpenBrowser` | [sheying2013/OpenBrowser](https://github.com/sheying2013/OpenBrowser)（原 lyu0805/OpenBrowser） | 管理器：多环境隔离、代理 / 指纹 / 扩展管理、窗口同步、本地 RPA、Local API + MCP（`127.0.0.1:50325`），内置 Wayfern 内核开箱即用 | MIT |

## 前置要求

- Windows 10 / 11（x64）
- 已安装 [Scoop](https://scoop.sh)（桶内包均为便携版，Scoop 根目录放哪个盘都能用）

## 安装

```powershell
# 1. 添加本桶（公开仓库，无需 token）
scoop bucket add baobao-apps https://github.com/baobao666-bgm/baobao-apps.git

# 2. 安装
scoop install baobao-apps/fingerprint-chromium   # 指纹内核（小，秒装）
scoop install baobao-apps/OpenBrowser            # 管理器（约 1.1 GB，稍慢）
```

装完即可获得两个命令：

- `chrome` → fingerprint-chromium 的 `chrome.exe`（指纹内核，可加指纹参数直启）
- `OpenBrowser` → 管理器主程序

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

- **自动更新（CI）**：本桶的 GitHub Actions 每天 06:00 UTC 检查两个上游的最新 release，自动更新 manifest 的 `version / url / sha256` 并回写本仓库；也可在 [Actions](https://github.com/baobao666-bgm/baobao-apps/actions) 页面手动 Run workflow 立即刷新。
- **安装目录兼容修补**：OpenBrowser 包在安装时会自动修补主程序，使其在以 junction / 符号链接方式链接安装目录的环境（例如 Scoop 的 `current` 目录）中正常工作，避免「本地设置」页按钮报错 `untrusted IPC document`；`scoop update` 重新拉取新版后，该修补会在 `pre_install` 阶段自动重新应用，无需手工处理。

## 常见问题

- **「本地设置」页按钮无反应 / 报 `untrusted IPC document`**：安装或更新会自动应用上述修补；若仍出现，请完全退出 OpenBrowser 后重新执行 `scoop install baobao-apps/OpenBrowser`（幂等覆盖安装）。
- **更新时报文件占用 / 无法删除旧版本**：先通过托盘图标完全退出 OpenBrowser，再执行 `scoop update`。
- **内核状态区一直「未选择」**：需先在 OpenBrowser 内完成一次内核下载或自定义内核选择，管理器才会真正调度指纹内核。

## 许可

- 本桶（manifest / 脚本 / CI）：**0BSD**
- `fingerprint-chromium`：BSD-3-Clause（上游）
- `OpenBrowser`：MIT（上游）
