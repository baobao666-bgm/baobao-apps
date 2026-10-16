'use strict';
/**
 * update.js 单元测试 —— 运行：node --test .github/scripts/test/
 * 覆盖：版本比较/更新决策（降级、同版本换包、fpc 构建号豁免）、资产选择、
 *       重定向链（相对 Location/上限）、404 直败、429/5xx 重试、超时、
 *       Content-Length 校验、魔数校验、HTTPS 策略。
 * 全部使用本地 HTTP 服务 + 传输注入，不访问真实网络。
 */
process.env.UPDATE_RETRY_DELAY_MS = '20';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');

const upd = require('../update.js');

const fpc = upd.PACKAGES.find((p) => p.key === 'fingerprint-chromium');
const openbrowser = upd.PACKAGES.find((p) => p.key === 'OpenBrowser');
const twinkstar = upd.PACKAGES.find((p) => p.key === 'twinkstar');

function tmpFile(name) {
  return path.join(os.tmpdir(), `upd-test-${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}-${name}`);
}

/** 起一个本地 HTTP 服务并把传输层注入 update.js */
async function withServer(handler, fn) {
  const sockets = new Set();
  const srv = http.createServer(handler);
  srv.on('connection', (s) => {
    sockets.add(s);
    s.on('close', () => sockets.delete(s));
  });
  await new Promise((resolve) => srv.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${srv.address().port}`;
  upd.__setTestTransport(http, { allowInsecure: true });
  try {
    return await fn(base);
  } finally {
    upd.__resetTestHooks();
    for (const s of sockets) s.destroy();
    await new Promise((resolve) => srv.close(resolve));
  }
}

// ------------------------------------------------------------------ 版本比较
test('cmpVersion：数字点分比较', () => {
  assert.equal(upd.cmpVersion('1.0.24', '1.0.23'), 1);
  assert.equal(upd.cmpVersion('11.5.1000.2609', '11.5.1000.2609'), 0);
  assert.equal(upd.cmpVersion('150.0.7871.186', '149.0.9999.999'), 1);
  assert.equal(upd.cmpVersion('1.2', '1.2.0'), 0);
  assert.ok(Number.isNaN(upd.cmpVersion('abc', '1.0')));
});

// ------------------------------------------------------------------ 更新决策
test('decideUpdate：无变化 -> changed=false', () => {
  const cur = { version: '1.0.24', url: 'https://e.com/a.zip', hash: 'aa' };
  const up = { version: '1.0.24', url: 'https://e.com/a.zip' };
  assert.equal(upd.decideUpdate(openbrowser, cur, up, 'aa').changed, false);
});

test('decideUpdate：升级放行', () => {
  const cur = { version: '1.0.24', url: 'https://e.com/a.zip', hash: 'aa' };
  const up = { version: '1.0.25', url: 'https://e.com/b.zip' };
  const plan = upd.decideUpdate(openbrowser, cur, up, 'bb');
  assert.equal(plan.changed, true);
  assert.equal(plan.version, '1.0.25');
  assert.equal(plan.url, 'https://e.com/b.zip');
});

test('decideUpdate：拒绝降级', () => {
  const cur = { version: '1.0.24', url: 'https://e.com/a.zip', hash: 'aa' };
  const up = { version: '1.0.23', url: 'https://e.com/old.zip' };
  assert.throws(() => upd.decideUpdate(openbrowser, cur, up, 'cc'), /downgrade/);
});

test('decideUpdate：同版本换 URL（无豁免的包）直接拒绝', () => {
  const cur = { version: '1.0.24', url: 'https://e.com/a.zip', hash: 'aa' };
  const up = { version: '1.0.24', url: 'https://e.com/swapped.zip' };
  assert.throws(() => upd.decideUpdate(openbrowser, cur, up, 'bb'), /same version/);
});

test('decideUpdate：fingerprint-chromium 构建号严格递增时放行', () => {
  const tag = '150.0.7871.186';
  const cur = {
    version: tag,
    url: `https://github.com/adryfish/fingerprint-chromium/releases/download/${tag}/ungoogled-chromium_${tag}-1.1_windows_x64.zip`,
    hash: 'aa',
  };
  const up = {
    version: tag,
    url: `https://github.com/adryfish/fingerprint-chromium/releases/download/${tag}/ungoogled-chromium_${tag}-1.2_windows_x64.zip`,
  };
  assert.equal(upd.decideUpdate(fpc, cur, up, 'bb').changed, true);
});

test('decideUpdate：fpc 同版本构建号未递增 / URL 形状变化 -> 拒绝', () => {
  const tag = '150.0.7871.186';
  const cur = {
    version: tag,
    url: `https://github.com/adryfish/fingerprint-chromium/releases/download/${tag}/ungoogled-chromium_${tag}-1.1_windows_x64.zip`,
    hash: 'aa',
  };
  const lower = {
    version: tag,
    url: `https://github.com/adryfish/fingerprint-chromium/releases/download/${tag}/ungoogled-chromium_${tag}-1.0_windows_x64.zip`,
  };
  assert.throws(() => upd.decideUpdate(fpc, cur, lower, 'bb'), /same-version change rejected/);
  const weird = { version: tag, url: 'https://evil.example.com/pkg.zip' };
  assert.throws(() => upd.decideUpdate(fpc, cur, weird, 'bb'), /same-version change rejected/);
});

test('decideUpdate：读取标准 architecture.64bit 布局（含大小写归一）', () => {
  const cur = {
    version: '1.0.24',
    architecture: { '64bit': { url: 'https://e.com/a.zip', hash: 'AABB' } },
  };
  const up = { version: '1.0.24', url: 'https://e.com/a.zip' };
  assert.equal(upd.decideUpdate(openbrowser, cur, up, 'aabb').changed, false);
});

// ------------------------------------------------------------------ 资产选择
test('fpc selectAsset：唯一 x64 zip 命中，忽略 arm64/符号包', () => {
  const tag = '150.0.7871.186';
  const assets = [
    { name: `ungoogled-chromium_${tag}-1.1_windows_arm64.zip` },
    { name: `ungoogled-chromium_${tag}-1.1_windows_x64.zip` },
    { name: `ungoogled-chromium_${tag}-1.1_windows_x64_symbols.zip` },
    { name: 'ungoogled-chromium_150.0.7871.186-1.1_windows_x64.tar.gz' },
  ];
  const hit = fpc.selectAsset(assets, tag);
  assert.equal(hit.name, `ungoogled-chromium_${tag}-1.1_windows_x64.zip`);
});

test('fpc selectAsset：多个构建号 -> 歧义报错', () => {
  const tag = '150.0.7871.186';
  const assets = [
    { name: `ungoogled-chromium_${tag}-1.1_windows_x64.zip` },
    { name: `ungoogled-chromium_${tag}-1.2_windows_x64.zip` },
  ];
  assert.throws(() => fpc.selectAsset(assets, tag), /ambiguous/);
});

test('fpc selectAsset：无精确匹配 -> 拒绝（不再兜底任意 zip）', () => {
  const tag = '150.0.7871.186';
  assert.throws(() => fpc.selectAsset([{ name: 'something_windows_other.zip' }], tag), /no asset matches/);
});

test('OpenBrowser selectAsset：必须精确命中 with-kernel 资产', () => {
  const assets = [
    { name: 'OpenBrowser-Windows-x86_64.zip' },
    { name: 'OpenBrowser-Windows-x86_64-with-kernel.zip' },
  ];
  assert.equal(openbrowser.selectAsset(assets, 'v1.0.24').name, 'OpenBrowser-Windows-x86_64-with-kernel.zip');
  assert.throws(() => openbrowser.selectAsset([{ name: 'OpenBrowser-Windows-x86_64.zip' }], 'v1.0.24'), /refusing fuzzy fallback/);
});

// ------------------------------------------------------------------ Twinkstar 解析
test('resolveTwinkstar：解析 + 官方 HTTPS 地址 + 浏览器 UA', async () => {
  const calls = [];
  const stub = async (url, opts) => {
    calls.push({ url, opts });
    return Buffer.from('... Twinkstar_v11.5.1000.2609_Release.exe ...');
  };
  const r = await upd.resolveTwinkstar(twinkstar, stub);
  assert.equal(r.version, '11.5.1000.2609');
  assert.equal(r.url, 'https://d1.twinkstar.com/win/Twinkstar_v11.5.1000.2609_Release.exe#/dl.exe');
  assert.ok(calls[0].opts.headers['User-Agent'].includes('Mozilla/5.0'));
});

test('resolveTwinkstar：版本格式异常 -> 拒绝', async () => {
  const stub = async () => Buffer.from('Twinkstar_v11.5.1000_Release.exe');
  await assert.rejects(upd.resolveTwinkstar(twinkstar, stub), /unexpected version format/);
});

test('resolveTwinkstar：页面无版本 -> 拒绝', async () => {
  const stub = async () => Buffer.from('nothing here');
  await assert.rejects(upd.resolveTwinkstar(twinkstar, stub), /version not found/);
});

// ------------------------------------------------------------------ 网络策略
test('默认策略：拒绝非 HTTPS URL', async () => {
  await assert.rejects(upd.requestOnce('http://example.com/x'), /only HTTPS/);
});

test('重定向链：支持相对 Location，最多 5 跳内完成', async () => {
  const hits = [];
  await withServer((req, res) => {
    hits.push(req.url);
    if (req.url === '/a') {
      res.writeHead(302, { Location: '/b' });
      res.end();
    } else if (req.url === '/b') {
      res.writeHead(301, { Location: 'c' }); // 相对地址 -> /c
      res.end();
    } else {
      res.writeHead(200);
      res.end('hello');
    }
  }, async (base) => {
    const buf = await upd.fetchBuffer(`${base}/a`);
    assert.equal(buf.toString(), 'hello');
  });
  assert.deepEqual(hits, ['/a', '/b', '/c']);
});

test('重定向：超过 5 跳失败（防环）', async () => {
  let n = 0;
  await withServer((req, res) => {
    n++;
    res.writeHead(302, { Location: '/loop' });
    res.end();
  }, async (base) => {
    await assert.rejects(upd.fetchBuffer(`${base}/loop`), /too many redirects/);
  });
  assert.equal(n, upd.__private.MAX_REDIRECTS + 1);
});

test('404 直接失败：不重试（只请求一次）', async () => {
  let n = 0;
  await withServer((req, res) => {
    n++;
    res.writeHead(404);
    res.end('nope');
  }, async (base) => {
    await assert.rejects(upd.fetchBuffer(`${base}/missing`), /HTTP 404/);
  });
  assert.equal(n, 1);
});

test('5xx 重试 2 次（共 3 次尝试）后失败', async () => {
  let n = 0;
  await withServer((req, res) => {
    n++;
    res.writeHead(503);
    res.end('boom');
  }, async (base) => {
    await assert.rejects(upd.fetchBuffer(`${base}/x`), /HTTP 503/);
  });
  assert.equal(n, upd.__private.RETRY_LIMIT + 1);
});

test('429 重试后失败', async () => {
  let n = 0;
  await withServer((req, res) => {
    n++;
    res.writeHead(429);
    res.end('slow down');
  }, async (base) => {
    await assert.rejects(upd.fetchBuffer(`${base}/x`), /HTTP 429/);
  });
  assert.equal(n, upd.__private.RETRY_LIMIT + 1);
});

// ------------------------------------------------------------------ 下载
test('downloadToFile：成功下载 + sha256 + 字节数', async () => {
  const payload = Buffer.concat([Buffer.from('PK\x03\x04'), crypto.randomBytes(4096)]);
  const dest = tmpFile('ok.bin');
  await withServer((req, res) => {
    res.writeHead(200, { 'Content-Length': payload.length });
    res.end(payload);
  }, async (base) => {
    const r = await upd.downloadToFile(`${base}/file.bin`, dest);
    assert.equal(r.bytes, payload.length);
    assert.equal(r.sha256, crypto.createHash('sha256').update(payload).digest('hex'));
    assert.ok(fs.readFileSync(dest).equals(payload));
  });
  fs.rmSync(dest, { force: true });
});

test('downloadToFile：Content-Length 不符 / 连接提前关闭 -> 报错且重试', async () => {
  let n = 0;
  const dest = tmpFile('bad.bin');
  await withServer((req, res) => {
    n++;
    // Connection: close 会让服务端在响应体不完整时立即断开，模拟被截断的下载
    res.writeHead(200, { 'Content-Length': '100000', Connection: 'close' });
    res.write('short');
    res.end();
  }, async (base) => {
    await assert.rejects(
      upd.downloadToFile(`${base}/bad.bin`, dest),
      /connection closed before download completed|content-length mismatch/
    );
  });
  assert.equal(n, upd.__private.RETRY_LIMIT + 1);
  fs.rmSync(dest, { force: true });
});

test('downloadToFile：无数据超时（idle）', async () => {
  const dest = tmpFile('idle.bin');
  await withServer((req, res) => {
    res.writeHead(200, { 'Content-Length': '100' });
    res.write('x'); // 之后一直不结束
  }, async (base) => {
    await assert.rejects(
      upd.downloadToFile(`${base}/idle.bin`, dest, { idleTimeoutMs: 150, totalTimeoutMs: 5000 }),
      /stalled|closed before download completed/
    );
  });
  fs.rmSync(dest, { force: true });
});

// ------------------------------------------------------------------ 魔数校验
test('checkFileMagic：zip 与 exe 魔数', () => {
  const f = tmpFile('magic.bin');
  fs.writeFileSync(f, Buffer.from('PK\x03\x04rest'));
  assert.equal(upd.checkFileMagic(f, 'zip').ok, true);
  fs.writeFileSync(f, Buffer.from('MZ\x90\x00'));
  assert.equal(upd.checkFileMagic(f, 'zip').ok, false);
  assert.equal(upd.checkFileMagic(f, 'exe').ok, true);
  fs.writeFileSync(f, Buffer.from('G'));
  assert.equal(upd.checkFileMagic(f, 'exe').ok, false);
  fs.rmSync(f, { force: true });
});

// ------------------------------------------------------------------ 清单字段工具
test('getUrlHash / setUrlHash：两种布局', () => {
  const flat = { url: 'https://e/a.zip', hash: 'AA' };
  assert.deepEqual(upd.getUrlHash(flat), { url: 'https://e/a.zip', hash: 'aa', slot: flat });
  upd.setUrlHash(flat, 'https://e/b.zip', 'bb');
  assert.equal(flat.url, 'https://e/b.zip');
  assert.equal(flat.hash, 'bb');

  const arch = { architecture: { '64bit': { url: 'https://e/c.zip', hash: 'CC' } } };
  upd.setUrlHash(arch, 'https://e/d.zip', 'dd');
  assert.equal(arch.architecture['64bit'].url, 'https://e/d.zip');
  assert.equal(arch.architecture['64bit'].hash, 'dd');
  assert.equal(arch.url, undefined);
});
