# baobao-apps — 本地指纹浏览器（指纹内核 + 管理器）的 Scoop 化

把 [fingerprint-chromium](https://github.com/adryfish/fingerprint-chromium)（内核）+ [OpenBrowser](https://github.com/sheying2013/OpenBrowser)（管理器）包成 Scoop 应用，**纯本地、可移植、CI 自动更新**。

## 一、装（任意 Windows 机器，一条命令换机即用）

```powershell
# 加 bucket（公开仓库，无需 token）
scoop bucket add baobao-apps https://github.com/baobao666-bgm/baobao-apps.git

# 装两个
scoop install baobao-apps/fingerprint-chromium   # 指纹内核（C++ 级，seed 确定性，可报真 Chrome）
scoop install baobao-apps/OpenBrowser            # 管理器（批量面板 + MCP 127.0.0.1:50325 + RPA）

# 之后统一维护
scoop update *
scoop checkup
```

全部落位 `D:\ScoopApps\apps\`（跟随你的 scoop root），符合 D 盘习惯。

## 二、把 OpenBrowser 的内核指向 fingerprint-chromium

OpenBrowser 自带 Wayfern 内核；本套你要用 C++ 级、可报 `brand=Chrome` 的
fingerprint-chromium，打开 OpenBrowser → 设置 → 内核路径，指向：

```
D:\ScoopApps\apps\fingerprint-chromium\current\chrome.exe
```

## 三、CI 自动更新（档位3 的"一劳永逸"）

`.github/workflows/auto-update.yml` 每天 06:00 UTC 检查两个上游 release，
发新版就自动 bump `version / url` 并重算 `sha256` 回写仓库。
你也可以在 Actions 页手动 Run（`workflow_dispatch`）立即刷。

## 四、上游资产

| 包 | 上游 | 资产 |
|---|---|---|
| fingerprint-chromium | adryfish/fingerprint-chromium | `ungoogled-chromium_<tag>-1.1_windows_x64.zip` |
| OpenBrowser | sheying2013/OpenBrowser（由 lyu0805 转移） | `OpenBrowser-Windows-x86_64-with-kernel.zip` |

> 两个包都是便携版/二进制，运行时不强依赖 nodejs（OpenBrowser 若走"源码模式"才需要 Node LTS）。
