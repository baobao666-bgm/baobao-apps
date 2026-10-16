# Vendored Scoop manifest schema

`scoop-schema.json` 是从 Scoop 官方仓库复制的 manifest JSON schema，供 CI 做清单校验。

- 来源：ScoopInstaller/Scoop（本地取自已安装的 Scoop v0.6.0，`apps/scoop/current/schema.json`）
- 抄录日期：2026-10-17
- 校验脚本：`.github/scripts/validate_manifests.py`（CI 由 validate 工作流执行）

升级办法：Scoop 发布新版本后，用新版本安装目录中的 `schema.json` 覆盖本文件，并在 PR 里说明来源版本号。
