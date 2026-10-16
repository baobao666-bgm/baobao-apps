# typora0.11.18

> 返回 [README](../README.md)

安装包约 68 MB；提供 64 位与 32 位两种架构（32 位用 `scoop install baobao-apps/typora0.11.18 --arch 32bit` 安装）。因上游 0.11.18 为静态免付费旧版，本包**不参与 CI 自动更新**。

## 内置修复说明

安装时预置注册表 `HKCU\SOFTWARE\Typora` 的 `IDate` 为远期时间并设置对应的拒绝权限，用于消除「This beta version of Typora is expired」过期弹窗——所有改动都会被记录，卸载时**按记录精确撤销**（已有键恢复原状、本包新建的键才清理），不会误删其他值。

同时只修改 `%APPDATA%\Typora\profile.data` 中必要字段（关闭「自动检查更新」、跳过当前最新版本）；文件无法解析时保持原样并提示，不做覆盖。

## 数据说明

`%APPDATA%\Typora` 是指向 `<scoop根目录>\persist\typora0.11.18\TyporaData` 的目录联接，C 盘不保存实际数据；普通卸载保留数据，`scoop uninstall -p` 才会删除。通用语义见 [data-and-uninstall.md](data-and-uninstall.md)。
