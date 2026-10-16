# 数据与卸载

> 返回 [README](../README.md)

各包的真实用户数据统一持久化在 `<scoop根目录>\persist\<包名>\`，Windows 默认位置（C 盘）只保留指向它的目录联接：

| 包 | C 盘联接位置 | 实际数据位置 |
|---|---|---|
| `OpenBrowser` | `%APPDATA%\openbrowser` | `<scoop根目录>\persist\OpenBrowser\UserData` |
| `Twinkstar` | `%LOCALAPPDATA%\Twinkstar` | `<scoop根目录>\persist\Twinkstar\UserData` |
| `fingerprint-chromium` | `%LOCALAPPDATA%\Chromium\User Data` | `<scoop根目录>\persist\fingerprint-chromium\UserData` |
| `typora0.11.18` | `%APPDATA%\Typora` | `<scoop根目录>\persist\typora0.11.18\TyporaData` |

## 安装与迁移

- **首次安装**：若对应 C 盘位置已存在真实数据，安装脚本会先复制到 persist、逐项核对（文件清单 / 大小 / SHA-256）后再删除源目录并建立联接；核对失败会保留源数据并报错。
- **两处都有数据**时脚本会拒绝合并并停止（列出两个位置），需要手动处理后重试。

## 卸载与升级

- **普通卸载**：仅移除 C 盘联接，数据保留在 persist；`scoop uninstall -p <应用>` 才会连数据一起删除。
- **升级 / 重装**（`scoop update`、`scoop update -f`）不会删除数据。
- 若从**旧版本**（数据在安装目录、尚无 persist 的版本）升级：请直接用 `scoop update -f baobao-apps/<应用>`（会自动把旧数据迁入 persist）；**不要先卸载再安装**——旧版卸载会按旧语义删除数据。

## 常见问题

- **更新 / 卸载时报文件占用**：先通过托盘图标完全退出对应应用（含托盘），再重试；脚本也会尝试优雅关闭，等待 15 秒仍未退出时会中止并提示。
- **数据在哪？想彻底删掉怎么办？**：见上表；彻底删除用 `scoop uninstall -p <应用>`（C 盘联接会被一并移除）。
