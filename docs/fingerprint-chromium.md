# fingerprint-chromium

> 返回 [README](../README.md)

指纹浏览器内核，安装包很小、秒装。在 OpenBrowser 内指向它的方法见 [openbrowser.md](openbrowser.md)。

## 指纹参数（直启场景）

```
chrome.exe --fingerprint <32位seed> --fingerprint-platform windows --fingerprint-brand Chrome --timezone Asia/Shanghai
```

- 同一账号请始终使用同一个 seed（seed 变化 = 换了一个浏览器身份，易触发风控）
- `--fingerprint-brand Chrome` 使指纹以真实 Chrome 上报，适合 IG / FB / Google 等网页
- `--timezone` 请与代理 IP 所在地时区保持一致
