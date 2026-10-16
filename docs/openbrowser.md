# OpenBrowser

> 返回 [README](../README.md)

安装包约 1.1 GB，首次安装稍慢属正常。Local API + MCP 仅监听本机回环（`127.0.0.1:50325`），不会对外暴露。

## 把内核指向 fingerprint-chromium

OpenBrowser 自带 Wayfern 内核，不配置也能用。若想使用 C++ 级指纹、可报 `brand=Chrome` 的 fingerprint-chromium：

```powershell
# 查看内核安装路径（形如 <scoop根目录>\apps\fingerprint-chromium\current）
scoop dir fingerprint-chromium
```

1. 启动 `OpenBrowser` → 左侧「本地设置」→「独立浏览器内核」区
2. 点「选择自定义内核」→ 选择上一步目录下的 `chrome.exe`
3. 勾选「优先使用独立内核」，取消「允许手动回退到本机浏览器」

## 代理建议

建议每个环境绑定**独立住宅代理**，并保证 IP 地理位置、时区、语言三者与指纹一致。管理器内置代理管理 / 代理检测页面。

## 安装目录兼容修补

安装时会自动修补主程序，使其在以 junction / 符号链接方式链接安装目录的环境（例如 Scoop 的 `current` 目录）中正常工作，避免「本地设置」页按钮报错 `untrusted IPC document`。修补要求上游代码的锚点全部匹配才会写回；**上游结构变化时会中止安装并提示更新本包**（不会写入"半修补"状态）。`scoop update -f` 重装时会自动重新应用。

## 常见问题

- **「本地设置」页按钮无反应 / 报 `untrusted IPC document`**：安装或更新会自动应用上述修补；若仍出现，请完全退出 OpenBrowser 后执行 `scoop update -f baobao-apps/OpenBrowser` 重新应用。注意：普通重复 `scoop install` 不会重装，Scoop 会提示已安装。
- **内核状态区一直「未选择」**：需先在 OpenBrowser 内完成一次内核下载或自定义内核选择，管理器才会真正调度指纹内核。
