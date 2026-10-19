# -*- coding: utf-8 -*-
"""把 ps_snippets.py 中的脚本 + 架构字段标准化 + persist 声明应用到四个清单。

清单里那几百行 PowerShell 是**生成**出来的：本文件与 ps_snippets.py 是唯一源文件，
bucket/*.json 只是产物。改钩子脚本请改这里，然后重新生成，不要手改 JSON——
手改会在下一次生成时被静默覆盖，CI 也会因为对不上而失败。

用法：
    python tools/manifests/make_manifests.py                # 重新生成 bucket/*.json
    python tools/manifests/make_manifests.py --check        # 只校验：JSON 是否与生成器一致（CI 用）
    python tools/manifests/make_manifests.py --export DIR    # 额外导出 *.ps1 供语法检查 / 夹具测试
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ps_snippets import SCRIPTS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
BUCKET_DIR = os.path.join(REPO, 'bucket')


def load(name):
    with io.open(os.path.join(BUCKET_DIR, name), encoding='utf-8') as f:
        return json.load(f)


def to_arch64(m):
    """把顶层 url/hash 迁移进标准的 architecture.64bit 对象（幂等：已是新布局则原样返回）。"""
    if isinstance(m.get('architecture'), dict) and '64bit' in m['architecture']:
        return m['architecture']
    arch = {'64bit': {'url': m.pop('url'), 'hash': m.pop('hash')}}
    return arch


def serialize(m):
    return json.dumps(m, indent=2, ensure_ascii=False) + '\n'


def dump(name, m):
    path = os.path.join(BUCKET_DIR, name)
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(serialize(m))
    print('wrote', path)


def build_openbrowser():
    old = load('OpenBrowser.json')
    m = {
        'version': old['version'],
        'description': old['description'],
        'homepage': old['homepage'],
        'license': old['license'],
        'architecture': to_arch64(old),
        'persist': 'UserData',
        'bin': old['bin'],
        'shortcuts': [['runtime\\OpenBrowser.exe', 'OpenBrowser']],
        'pre_install': SCRIPTS['OpenBrowser.pre_install'](),
        'post_install': SCRIPTS['OpenBrowser.post_install'](),
        'pre_uninstall': SCRIPTS['OpenBrowser.pre_uninstall'](),
        'notes': (
            'OpenBrowser：本地指纹浏览器管理器（多环境隔离、代理 / 指纹 / 扩展、批量管理、窗口同步、本地 RPA、'
            'Local API + MCP 仅监听本机回环 127.0.0.1:50325，不对外网暴露）。\n'
            '快捷方式：安装后在桌面创建 OpenBrowser.lnk（指向 current\\runtime\\OpenBrowser.exe，图标取包内 logo.ico），'
            '开始菜单的「Scoop Apps」里也会有一个 OpenBrowser；卸载时只删除指向本包的那个桌面快捷方式，'
            '你自己改指向或另建的快捷方式不会被删。\n'
            '用户数据（Scoop persist）：%APPDATA%\\openbrowser 为目录联接，指向 <scoop根目录>\\persist\\OpenBrowser\\UserData；'
            '普通卸载保留数据，scoop uninstall -p OpenBrowser 才会删除。\n'
            '若该位置已经是别的工具建立的联接（本包不接管）：联接与数据都不动，安装照常完成，'
            '但数据不会进入本包 persist，详见 docs/data-and-uninstall.md。\n'
            '安装时会对主程序做一次兼容修补（修复 Scoop 联接环境下「untrusted IPC document」导致本地设置按钮失效的问题）；'
            '上游结构变化时会中止安装并提示更新本包。\n'
            '要用 C++ 指纹内核时，「自定义内核」指向 <scoop根目录>\\fingerprint-chromium\\current\\chrome.exe。'
            '命令行输入 OpenBrowser 即可启动。'
        ),
    }
    return 'OpenBrowser.json', m


def build_twinkstar():
    old = load('Twinkstar.json')
    m = {
        'version': old['version'],
        'description': old['description'],
        'homepage': old['homepage'],
        'license': old['license'],
        'architecture': to_arch64(old),
        'persist': 'UserData',
        'installer': old['installer'],
        'shortcuts': old['shortcuts'],
        'bin': old['bin'],
        'post_install': SCRIPTS['Twinkstar.post_install'](),
        'pre_uninstall': SCRIPTS['Twinkstar.pre_uninstall'](),
        'notes': (
            '星愿浏览器（Twinkstar）：Chromium 内核的免费浏览器，内置漫画阅读模式、视频下载 / 弹窗播放、鼠标手势、'
            '超级拖拽、多线程下载，兼容 Chrome 扩展。仅支持 x64。\n'
            '用户数据（Scoop persist）：%LOCALAPPDATA%\\Twinkstar 为目录联接，指向 <scoop根目录>\\persist\\Twinkstar\\UserData；'
            '普通卸载保留数据，scoop uninstall -p Twinkstar 才会删除。\n'
            '若该位置已经是别的工具建立的联接（本包不接管）：联接与数据都不动，安装照常完成，'
            '但数据不会进入本包 persist，详见 docs/data-and-uninstall.md。\n'
            'GPU 问题排障：若启动即退出并反复报 \'GPU process exited unexpectedly\'（多见于旧显卡驱动或精简系统），'
            '建议先升级显卡驱动、检查系统更新；确需临时绕过时可在启动命令 / 快捷方式目标末尾追加 --disable-gpu-sandbox'
            '（该参数会降低渲染进程隔离，仅作排障用，不建议长期使用）。'
        ),
        'checkver': old['checkver'],
        'autoupdate': old['autoupdate'],
    }
    return 'Twinkstar.json', m


def build_fpc():
    old = load('fingerprint-chromium.json')
    m = {
        'version': old['version'],
        'description': old['description'],
        'homepage': old['homepage'],
        'license': old['license'],
        'architecture': to_arch64(old),
        'persist': 'UserData',
        'bin': old['bin'],
        'pre_install': SCRIPTS['fingerprint-chromium.pre_install'](),
        'post_install': SCRIPTS['fingerprint-chromium.post_install'](),
        'pre_uninstall': SCRIPTS['fingerprint-chromium.pre_uninstall'](),
        'notes': (
            '开源指纹浏览器内核：基于 Ungoogled Chromium 的 C++ 级指纹补丁，32 位 seed 确定性指纹，'
            '可伪装成真实 Chrome（User-Agent / Client Hints 均报 Chrome）。\n'
            '启用指纹：chrome.exe --fingerprint <32位seed> --fingerprint-platform windows --fingerprint-brand Chrome --timezone <IANA时区>。\n'
            '用户数据（Scoop persist）：%LOCALAPPDATA%\\Chromium\\User Data 为目录联接，'
            '指向 <scoop根目录>\\persist\\fingerprint-chromium\\UserData；普通卸载保留数据，scoop uninstall -p fingerprint-chromium 才会删除。\n'
            '若该位置已经是别的工具建立的联接（本包不接管）：联接与数据都不动，安装照常完成，'
            '但数据不会进入本包 persist，详见 docs/data-and-uninstall.md。\n'
            '配合 OpenBrowser 使用时（「自定义内核」指向 <scoop根目录>\\fingerprint-chromium\\current\\chrome.exe），'
            'OpenBrowser 指定的环境由 OpenBrowser 自己管理，不在此目录。'
        ),
    }
    return 'fingerprint-chromium.json', m


def build_typora():
    old = load('typora0.11.18.json')
    m = {
        'version': old['version'],
        'description': old['description'],
        'homepage': old['homepage'],
        'license': old['license'],
        'architecture': old['architecture'],
        'innosetup': old['innosetup'],
        'persist': 'TyporaData',
        'bin': old['bin'],
        'shortcuts': old['shortcuts'],
        'post_install': SCRIPTS['typora0.11.18.post_install'](),
        'pre_uninstall': SCRIPTS['typora0.11.18.pre_uninstall'](),
        'notes': (
            'Typora 0.11.18（官方最后一个免费版本；提供 64 位与 32 位架构，默认 64 位，'
            '32 位可用 scoop install --arch 32bit 安装）。\n'
            '内置修复：① 预置 HKCU\\SOFTWARE\\Typora 的 IDate 并设置按记录的拒绝权限，'
            '消除「This beta version of Typora is expired」过期弹窗（卸载时逐条核验撤销；'
            '撤销不成功就中止卸载并保留记录，不会误删其他值）；'
            '② 预置用户配置关闭「自动检查更新」并跳过当前最新版本，启动时不再弹出更新提示窗口。\n'
            '用户数据（Scoop persist）：%APPDATA%\\Typora 为目录联接，指向 <scoop根目录>\\persist\\typora0.11.18\\TyporaData；'
            'C 盘不保存实际数据，升级 / 重装数据保留。\n'
            '卸载：普通卸载仅移除联接、保留数据；scoop uninstall -p typora0.11.18 才会连数据一起删除。'
            '若注册表撤销失败（例如记录文件损坏），卸载会中止且数据保留，按提示处理后重新执行即可。\n'
            '启动：命令行输入 typora，或从开始菜单打开「Typora」。\n'
            '若 %APPDATA%\\Typora 已被别的工具建成联接：本包不接管它，联接、数据与 profile.data 都不动，'
            '数据不会进入本包 persist，更新提示屏蔽也不会生效（消除过期弹窗的注册表修复照常执行）。'
            '希望这种情况下直接让安装失败，可设置环境变量 BAOBAO_STRICT_FOREIGN_LINK=1。'
        ),
    }
    return 'typora0.11.18.json', m


BUILDERS = (build_openbrowser, build_twinkstar, build_fpc, build_typora)


def export_scripts(export_dir):
    os.makedirs(export_dir, exist_ok=True)
    for key, maker in sorted(SCRIPTS.items()):
        script = maker()
        out = os.path.join(export_dir, key + '.ps1')
        with io.open(out, 'w', encoding='utf-8', newline='\n') as f:
            f.write(script)
        print('exported', out, len(script), 'chars')


def check():
    """校验 bucket/*.json 是否就是生成器的输出（忽略行尾差异）。不写任何文件。"""
    bad = []
    for fn in BUILDERS:
        name, m = fn()
        path = os.path.join(BUCKET_DIR, name)
        with io.open(path, encoding='utf-8') as f:
            actual = f.read().replace('\r\n', '\n')
        if actual != serialize(m):
            bad.append(name)
    if bad:
        print('MISMATCH: these manifests are not what tools/manifests generates: ' + ', '.join(bad))
        print('Fix: python tools/manifests/make_manifests.py  (then commit the result)')
        return 1
    print('OK: bucket/*.json matches tools/manifests.')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--check', action='store_true', help='only verify that bucket/*.json matches the generator')
    ap.add_argument('--export', metavar='DIR', help='also export each hook script as <name>.ps1 into DIR')
    args = ap.parse_args()

    if args.check:
        raise SystemExit(check())

    for fn in BUILDERS:
        name, m = fn()
        dump(name, m)

    if args.export:
        export_scripts(args.export)

    # 快速自检：脚本里不应残留下未替换的占位符
    for key, maker in SCRIPTS.items():
        s = maker()
        if '{HINT}' in s or '@@' in s:
            raise SystemExit('placeholder left in ' + key)
    print('OK: all manifests built.')


if __name__ == '__main__':
    main()
