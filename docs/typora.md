# typora0.11.18

> 返回 [README](../README.md)

安装包约 68 MB；提供 64 位与 32 位两种架构（32 位用 `scoop install baobao-apps/typora0.11.18 --arch 32bit` 安装）。因上游 0.11.18 为静态免付费旧版，本包**不参与 CI 自动更新**。

## 内置修复说明

安装时预置注册表 `HKCU\SOFTWARE\Typora` 的 `IDate` 为远期时间并设置对应的拒绝权限，用于消除「This beta version of Typora is expired」过期弹窗。这两项属于本包**有意为之**的修复：0.11.18 是官方最后一个免费版本，缺少任一项都无法正常启动，因此无论 `%APPDATA%\Typora` 是否由本包管理，都会执行。

改动会被完整记录：只登记**实际新增**的拒绝规则及其精确形状（SID / 权限掩码 / 继承标志），同时记录 `IDate` 的**原值类型**（REG_SZ、REG_DWORD、REG_EXPAND_SZ 等）。卸载时只撤销本包新增的规则，并按原类型写回原值；安装前就已存在的同型规则不会被删除。状态文件缺失或损坏时**不做任何 ACL 删除**，保留文件并给出手工清理说明——宁可留下残留，也不猜测式改动他人设置的权限。

同时只修改 `%APPDATA%\Typora\profile.data` 中必要字段（关闭「自动检查更新」、跳过当前最新版本）；文件无法解析时保持原样并提示，不做覆盖。

## 数据说明

`%APPDATA%\Typora` 是指向 `<scoop根目录>\persist\typora0.11.18\TyporaData` 的目录联接，C 盘不保存实际数据；普通卸载保留数据，`scoop uninstall -p` 才会删除。通用语义见 [data-and-uninstall.md](data-and-uninstall.md)。

若该位置已经是用户自建的联接（本包不接管），安装时**不会**改动或迁移该联接，但上面两项修复仍会执行（写入该联接背后的 `profile.data`、设置注册表），否则软件无法启动。
