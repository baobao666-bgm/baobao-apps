#!/usr/bin/env node
/**
 * baobao-apps 自动更新脚本（CI 专用，硬加固版）
 *
 * 设计原则（对应改进方案 二.1 / 二.2）：
 *  - 任何一个包检查失败 => 本次不写任何文件，进程以非零退出（批量事务化）。
 *  - 下载/元数据请求：仅接受 HTTPS；重定向最多 5 跳（支持相对 Location，跨主机剥离 Authorization）；
 *    最终响应必须为 200；网络错误/超时/429/5xx 最多重试 2 次，其他 4xx 直接失败。
 *  - 超时：元数据 30s；安装包下载 60s 无数据超时 + 15 分钟总时限。
 *  - 校验：非空、Content-Length 一致、文件头魔数（zip/exe）、GitHub 提供的 sha256 digest 一致。
 *  - 版本选择：GitHub 只接受最新稳定 Release（/releases/latest，排除草稿与预发布）；
 *    安装包必须精确匹配（不做"任意 Windows ZIP"兜底）；fingerprint-chromium 的构建号
 *    变化必须唯一匹配 x64 ZIP 且构建号严格递增；其余同版本 URL/哈希变化一律拒绝并要求人工核验。
 *  - 阻止自动降级；版本/URL/哈希均未变化时不重写文件。
 *  - 临时文件使用唯一名称，成功或失败后都清理。
 *
 * 用法：
 *   node .github/scripts/update.js            # 刷新全部包（CI 默认）
 *   node .github/scripts/update.js twinkstar  # 只刷新指定包（本地手动用）
 *
 * 环境变量：
 *   GH_TOKEN / GITHUB_TOKEN   可选。提供后使用认证调用 GitHub API（提高限额）。
 *   UPDATE_RETRY_DELAY_MS     可选。重试基础间隔（测试用）。
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const https = require('https');

const ROOT = process.cwd();
const ONLY = process.argv[2] || '';

// ---------------------------------------------------------------- 策略常量
const MAX_REDIRECTS = 5;
const RETRY_LIMIT = 2; // 最多重试 2 次（共 3 次尝试）
const RETRY_DELAY_MS = Number(process.env.UPDATE_RETRY_DELAY_MS || 1000);
const META_TIMEOUT_MS = 30 * 1000;
const DL_IDLE_TIMEOUT_MS = 60 * 1000;
const DL_TOTAL_TIMEOUT_MS = 15 * 60 * 1000;
const MAX_META_BYTES = 20 * 1024 * 1024;
const UA = 'scoop-bucket-ci (baobao-apps)';

// ---------------------------------------------------------------- 错误分类
function policyError(msg) {
  const e = new Error(msg);
  e.policy = true; // 策略/校验失败：绝不重试
  return e;
}
function timeoutError(msg) {
  const e = new Error(msg);
  e.retryable = true;
  return e;
}
function statusError(status, url) {
  const e = new Error(`HTTP ${status} for ${url}`);
  e.status = status;
  if (status === 429 || status >= 500) e.retryable = true;
  else e.policy = true;
  return e;
}
function markNet(e) {
  if (e && !e.policy) e.retryable = true;
  return e;
}
function connectionError(msg) {
  const e = new Error(msg);
  e.retryable = true;
  return e;
}
/** 把底层 'aborted'/'premature close' 之类的流错误规范成可读、可重试的错误。 */
function normalizeStreamError(e, url, what) {
  if (e && e.policy) return e;
  const msg = String((e && e.message) || '');
  if (/aborted|premature/i.test(msg)) {
    return connectionError(`connection closed before ${what} completed (${url})`);
  }
  return markNet(e || new Error(`unknown ${what} stream error (${url})`));
}
const NETWORK_CODES = new Set([
  'ECONNRESET', 'ECONNREFUSED', 'ETIMEDOUT', 'EPIPE', 'ENOTFOUND', 'EAI_AGAIN',
  'EHOSTUNREACH', 'ENETUNREACH', 'ECONNABORTED', 'ERR_STREAM_PREMATURE_CLOSE',
  'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_SOCKET',
]);
function isRetryable(e) {
  if (!e) return false;
  if (e.policy) return false;
  if (e.retryable) return true;
  return !!(e.code && NETWORK_CODES.has(e.code));
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function withRetry(desc, fn) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await fn();
    } catch (e) {
      if (!isRetryable(e) || attempt >= RETRY_LIMIT) throw e;
      console.error(`  [retry ${attempt + 1}/${RETRY_LIMIT}] ${desc}: ${e.message}`);
      await sleep(RETRY_DELAY_MS * (attempt + 1));
    }
  }
}

// --------------------------------------------------- 传输层（含测试注入钩子）
const testHooks = { transport: null, allowInsecure: false };
function __setTestTransport(mod, opts = {}) {
  testHooks.transport = mod;
  testHooks.allowInsecure = !!opts.allowInsecure;
}
function __resetTestHooks() {
  testHooks.transport = null;
  testHooks.allowInsecure = false;
}
function transportFor() {
  return testHooks.transport || https;
}
function assertPolicyUrl(u) {
  if (u.protocol === 'https:') return;
  if (testHooks.allowInsecure && u.protocol === 'http:') return; // 仅测试注入启用
  throw policyError(`only HTTPS urls are accepted (got ${u.protocol}//)`);
}
function stripAuth(headers) {
  if (!headers) return headers;
  const copy = {};
  for (const k of Object.keys(headers)) {
    if (k.toLowerCase() !== 'authorization') copy[k] = headers[k];
  }
  return copy;
}

// ---------------------------------------------------------------- HTTP 原语
/**
 * 发起单次请求（内部处理重定向链）。返回 { res, finalUrl }，最终响应必为 200。
 * 非 200：429/5xx 抛可重试错误，其余 4xx 抛策略错误（直接失败）。
 */
function requestOnce(url, opts = {}) {
  const hop = opts.redirects || 0;
  let u;
  try {
    u = new URL(url);
  } catch (e) {
    return Promise.reject(policyError(`invalid url: ${url}`));
  }
  try {
    assertPolicyUrl(u);
  } catch (e) {
    return Promise.reject(e);
  }

  return new Promise((resolve, reject) => {
    const headers = { 'User-Agent': UA, ...(opts.headers || {}) };
    const req = transportFor().get(u, { headers }, (res) => {
      const status = res.statusCode;
      if (status >= 300 && status < 400) {
        res.resume();
        if (!res.headers.location) return reject(policyError(`redirect (${status}) without location header for ${url}`));
        if (hop >= MAX_REDIRECTS) return reject(policyError(`too many redirects (> ${MAX_REDIRECTS}) for ${url}`));
        let next;
        try {
          next = new URL(res.headers.location, u);
        } catch (e) {
          return reject(policyError(`bad redirect location "${res.headers.location}" for ${url}`));
        }
        const nextHeaders = next.host === u.host ? opts.headers : stripAuth(opts.headers);
        return resolve(requestOnce(next.href, { ...opts, headers: nextHeaders, redirects: hop + 1 }));
      }
      if (status !== 200) {
        res.resume();
        return reject(statusError(status, url));
      }
      resolve({ res, finalUrl: u.href });
    });
    req.on('error', (e) => {
      if (e && !e.policy && !e.retryable) markNet(e);
      reject(e);
    });
    if (opts.headerTimeoutMs) {
      req.setTimeout(opts.headerTimeoutMs, () =>
        req.destroy(timeoutError(`connection/header timeout after ${opts.headerTimeoutMs}ms for ${url}`))
      );
    }
  });
}

/** 拉取元数据（小体积），受总超时与大小上限约束。 */
async function fetchBuffer(url, opts = {}) {
  const timeoutMs = opts.timeoutMs || META_TIMEOUT_MS;
  return withRetry(`fetch ${url}`, async () => {
    const { res } = await requestOnce(url, { headers: opts.headers, headerTimeoutMs: timeoutMs });
    const totalTimer = setTimeout(
      () => res.destroy(timeoutError(`request exceeded total timeout ${timeoutMs}ms for ${url}`)),
      timeoutMs
    );
    try {
      return await new Promise((resolve, reject) => {
        let settled = false;
        const finish = (err, buf) => {
          if (settled) return;
          settled = true;
          if (err) reject(err);
          else resolve(buf);
        };
        const chunks = [];
        let len = 0;
        res.on('error', (e) => finish(normalizeStreamError(e, url, 'response')));
        res.on('close', () => {
          if (!res.complete && !settled) finish(connectionError(`connection closed before response completed (${url})`));
        });
        res.on('data', (c) => {
          len += c.length;
          if (len > MAX_META_BYTES) {
            res.destroy(policyError(`metadata response too large (> ${MAX_META_BYTES} bytes)`));
            return;
          }
          chunks.push(c);
        });
        res.on('end', () => finish(null, Buffer.concat(chunks, len)));
      });
    } finally {
      clearTimeout(totalTimer);
    }
  });
}

/** 下载到文件并流式计算 sha256；校验非空与 Content-Length。 */
async function downloadToFile(url, destPath, opts = {}) {
  const idleMs = opts.idleTimeoutMs || DL_IDLE_TIMEOUT_MS;
  const totalMs = opts.totalTimeoutMs || DL_TOTAL_TIMEOUT_MS;
  return withRetry(`download ${url}`, async () => {
    const { res, finalUrl } = await requestOnce(url, { headers: opts.headers, headerTimeoutMs: idleMs });
    const hash = crypto.createHash('sha256');
    let bytes = 0;
    const out = fs.createWriteStream(destPath);
    let idleTimer = null;
    const totalTimer = setTimeout(
      () => res.destroy(timeoutError(`download exceeded total limit (${totalMs}ms) for ${url}`)),
      totalMs
    );
    const bumpIdle = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(
        () => res.destroy(timeoutError(`download stalled: no data for ${idleMs}ms (${url})`)),
        idleMs
      );
    };
    try {
      await new Promise((resolve, reject) => {
        let settled = false;
        const finish = (err) => {
          if (settled) return;
          settled = true;
          if (err) {
            out.destroy();
            reject(err);
          } else {
            resolve();
          }
        };
        res.on('error', (e) => finish(normalizeStreamError(e, url, 'download')));
        res.on('close', () => {
          if (!res.complete && !settled) finish(connectionError(`connection closed before download completed (${url})`));
        });
        out.on('error', finish);
        out.on('finish', () => finish());
        res.on('data', (c) => {
          bytes += c.length;
          hash.update(c);
          bumpIdle();
        });
        res.pipe(out);
        bumpIdle();
      });
    } finally {
      clearTimeout(totalTimer);
      if (idleTimer) clearTimeout(idleTimer);
    }

    if (bytes === 0) throw markNet(new Error(`empty download (0 bytes, ${finalUrl})`));
    const lenHeader = res.headers['content-length'];
    const contentLength = lenHeader === undefined ? null : Number(lenHeader);
    if (contentLength !== null && Number.isFinite(contentLength) && bytes !== contentLength) {
      throw markNet(new Error(`content-length mismatch for ${finalUrl}: expected ${contentLength}, got ${bytes}`));
    }
    return { sha256: hash.digest('hex'), bytes, finalUrl };
  });
}

// ---------------------------------------------------------------- 校验工具
/** 文件头魔数检查：zip => PK..；exe => MZ。 */
function checkFileMagic(file, format) {
  const fd = fs.openSync(file, 'r');
  const buf = Buffer.alloc(4);
  let n = 0;
  try {
    n = fs.readSync(fd, buf, 0, 4, 0);
  } finally {
    fs.closeSync(fd);
  }
  if (format === 'zip') {
    const ok = n >= 4 && buf[0] === 0x50 && buf[1] === 0x4b &&
      ((buf[2] === 0x03 && buf[3] === 0x04) || (buf[2] === 0x05 && buf[3] === 0x06) || (buf[2] === 0x07 && buf[3] === 0x08));
    return { ok, detail: `first bytes: ${buf.slice(0, Math.max(n, 0)).toString('hex') || '(empty)'}` };
  }
  if (format === 'exe') {
    const ok = n >= 2 && buf[0] === 0x4d && buf[1] === 0x5a;
    return { ok, detail: `first bytes: ${buf.slice(0, Math.max(n, 0)).toString('hex') || '(empty)'}` };
  }
  return { ok: true, detail: 'no format check' };
}

function escapeRegExp(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** 数字点分版本比较；不可解析返回 NaN。 */
function cmpVersion(a, b) {
  const pa = String(a).split('.').map((s) => Number(s));
  const pb = String(b).split('.').map((s) => Number(s));
  if (pa.length === 0 || pb.length === 0 || pa.some(Number.isNaN) || pb.some(Number.isNaN)) return NaN;
  const n = Math.max(pa.length, pb.length);
  for (let i = 0; i < n; i++) {
    const x = pa[i] || 0;
    const y = pb[i] || 0;
    if (x !== y) return x < y ? -1 : 1;
  }
  return 0;
}

/** fingerprint-chromium 资产名约束（构建号允许变化但必须唯一匹配）。 */
function fpcAssetPattern(tag) {
  return new RegExp(`^ungoogled-chromium_${escapeRegExp(tag)}-(\\d+)\\.(\\d+)_windows_x64\\.zip$`, 'i');
}
function fpcBuildFromUrl(url) {
  try {
    const name = path.posix.basename(new URL(url).pathname);
    const m = name.match(/-(\d+)\.(\d+)_windows_x64\.zip$/i);
    if (!m) return null;
    return Number(m[1]) * 1000 + Number(m[2]);
  } catch (e) {
    return null;
  }
}

// ------------------------------------------------------------ 清单读写工具
function readManifest(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}
function writeManifest(file, m) {
  fs.writeFileSync(file, JSON.stringify(m, null, 2) + '\n');
}
/** 兼容两种布局：标准 architecture.64bit 对象 与 旧的顶层 url/hash。 */
function getUrlHash(m) {
  if (m.architecture && m.architecture['64bit'] && m.architecture['64bit'].url) {
    return {
      url: m.architecture['64bit'].url,
      hash: ((m.architecture['64bit'].hash || '') + '').toLowerCase() || null,
      slot: m.architecture['64bit'],
    };
  }
  return { url: m.url, hash: (((m.hash || m.sha256) || '') + '').toLowerCase() || null, slot: m };
}
function setUrlHash(m, url, sha) {
  const { slot } = getUrlHash(m);
  slot.url = url;
  slot.hash = sha;
  delete slot.sha256;
  delete m.sha256; // 清理历史遗留错字段
}

// ------------------------------------------------------------ 各包上游定义
const PACKAGES = [
  {
    key: 'fingerprint-chromium',
    kind: 'github',
    repo: 'adryfish/fingerprint-chromium',
    manifest: 'bucket/fingerprint-chromium.json',
    format: 'zip',
    versionFromTag: (tag) => tag, // tag 本身即版本（如 150.0.7871.186）
    selectAsset: (assets, tag) => {
      const pat = fpcAssetPattern(tag);
      const matches = assets.filter((a) => pat.test(a.name));
      if (matches.length === 0) {
        throw policyError(`fingerprint-chromium: no asset matches ${pat} (refusing fuzzy fallback)`);
      }
      if (matches.length > 1) {
        throw policyError(`fingerprint-chromium: ambiguous assets: ${matches.map((a) => a.name).join(', ')}`);
      }
      return matches[0];
    },
    // 唯一豁免：同版本下仅构建号（-x.y）严格递增时接受（上游会重发同 tag 资产）
    sameVersionChangeAllowed: (currentUrl, up) => {
      const ob = fpcBuildFromUrl(currentUrl);
      const nb = fpcBuildFromUrl(up.url);
      if (ob === null) return `current url does not match the constrained pattern: ${currentUrl}`;
      if (nb === null || !(nb > ob)) return `build number not strictly increased (old=${ob}, new=${nb})`;
      return true;
    },
  },
  {
    key: 'OpenBrowser',
    kind: 'github',
    repo: 'sheying2013/OpenBrowser',
    manifest: 'bucket/OpenBrowser.json',
    format: 'zip',
    versionFromTag: (tag) => String(tag).replace(/^v/, ''),
    selectAsset: (assets, tag) => {
      const NAME = 'OpenBrowser-Windows-x86_64-with-kernel.zip';
      const hit = assets.find((a) => a.name === NAME);
      if (!hit) throw policyError(`OpenBrowser: expected asset "${NAME}" not found in release ${tag} (refusing fuzzy fallback)`);
      return hit;
    },
  },
  {
    key: 'twinkstar',
    kind: 'twinkstar',
    manifest: 'bucket/Twinkstar.json',
    format: 'exe',
    homepage: 'https://www.twinkstar.com/',
    // 官网按 UA 分发链接，必须带浏览器 UA（cURL/scoop 默认 UA 会拿到停更的 32 位旧包）
    ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    regex: /Twinkstar_v([\d.]+)_Release\.exe/,
    buildUrl: (v) => `https://d1.twinkstar.com/win/Twinkstar_v${v}_Release.exe`,
    urlSuffix: '#/dl.exe', // 固定落盘名：Twinkstar.json 的 installer 脚本依赖 $dir\dl.exe
  },
];

// ------------------------------------------------------------ 上游解析
async function fetchGithubLatestRelease(repo, token) {
  const headers = { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const buf = await fetchBuffer(`https://api.github.com/repos/${repo}/releases/latest`, { headers });
  let rel;
  try {
    rel = JSON.parse(buf.toString('utf8'));
  } catch (e) {
    throw policyError(`invalid JSON from GitHub API for ${repo}: ${e.message}`);
  }
  return rel;
}

async function resolveGithub(p, ctx = {}) {
  const rel = await fetchGithubLatestRelease(p.repo, ctx.token);
  if (!rel || !rel.tag_name) throw policyError(`${p.key}: no stable release found for ${p.repo}`);
  if (rel.draft || rel.prerelease) {
    throw policyError(`${p.key}: latest release ${rel.tag_name} is draft/prerelease (unexpected from /releases/latest)`);
  }
  const tag = String(rel.tag_name);
  const version = p.versionFromTag(tag);
  if (!version || !/^[0-9][0-9A-Za-z.+\-_]*$/.test(version)) {
    throw policyError(`${p.key}: unexpected version "${version}" derived from tag "${tag}"`);
  }
  const assets = Array.isArray(rel.assets) ? rel.assets : [];
  const asset = p.selectAsset(assets, tag);
  let digest = null;
  if (typeof asset.digest === 'string' && asset.digest.toLowerCase().startsWith('sha256:')) {
    digest = asset.digest.slice('sha256:'.length).toLowerCase();
  }
  return { version, url: asset.browser_download_url, digest, assetName: asset.name };
}

/** Twinkstar：UA 抓官网 -> 版本格式校验 -> 官方 HTTPS 地址校验。fetchFn 可注入（测试）。 */
async function resolveTwinkstar(p, fetchFn = fetchBuffer) {
  const html = (await fetchFn(p.homepage, { headers: { 'User-Agent': p.ua } })).toString('utf8');
  const m = html.match(p.regex);
  if (!m) throw policyError('twinkstar: version not found on homepage (UA dispatch or page layout changed?)');
  const version = m[1];
  if (!/^\d+\.\d+\.\d+\.\d+$/.test(version)) {
    throw policyError(`twinkstar: unexpected version format "${version}"`);
  }
  const url = p.buildUrl(version) + (p.urlSuffix || '');
  let u;
  try {
    u = new URL(url);
  } catch (e) {
    throw policyError(`twinkstar: invalid download url ${url}`);
  }
  if (u.protocol !== 'https:' || u.hostname !== 'd1.twinkstar.com') {
    throw policyError(`twinkstar: download url is not the official HTTPS endpoint: ${url}`);
  }
  return { version, url, digest: null };
}

async function resolveUpstream(p, current, ctx = {}) {
  if (p.kind === 'twinkstar') return resolveTwinkstar(p);
  return resolveGithub(p, ctx);
}

// ------------------------------------------------------------ 更新决策
/**
 * 决定是否更新。任何可疑变化（降级、同版本换包）都会抛错（批量事务会因此整体失败）。
 * @returns {{changed:boolean, version:string, url:string, sha:string}}
 */
function decideUpdate(p, current, up, sha) {
  const cur = {
    version: current.version,
    ...getUrlHash(current),
  };
  const versionChanged = cur.version !== up.version;
  const urlChanged = cur.url !== up.url;
  const hashChanged = cur.hash !== sha;
  if (!versionChanged && !urlChanged && !hashChanged) {
    return { changed: false, version: up.version, url: up.url, sha };
  }
  if (versionChanged) {
    const c = cmpVersion(up.version, cur.version);
    if (Number.isNaN(c)) {
      throw policyError(`${p.key}: unparseable version: current="${cur.version}" upstream="${up.version}"`);
    }
    if (c < 0) {
      throw policyError(`${p.key}: refusing downgrade ${cur.version} -> ${up.version} (do downgrades manually with review)`);
    }
    return { changed: true, version: up.version, url: up.url, sha };
  }
  // 同版本、URL 或哈希发生变化
  if (p.sameVersionChangeAllowed) {
    const verdict = p.sameVersionChangeAllowed(cur.url, up);
    if (verdict === true) return { changed: true, version: up.version, url: up.url, sha };
    throw policyError(`${p.key}: same-version change rejected (${cur.version}): ${verdict}; manual review required`);
  }
  throw policyError(`${p.key}: url/hash changed at the same version ${cur.version}; manual review required (possible re-release or mirror swap)`);
}

// ------------------------------------------------------------ 主流程
async function main() {
  const keys = PACKAGES.map((p) => p.key);
  if (ONLY && !keys.includes(ONLY)) {
    console.error(`unknown package "${ONLY}" (valid: ${keys.join(', ')})`);
    process.exit(2);
  }
  const selected = PACKAGES.filter((p) => !ONLY || p.key === ONLY);
  const token = process.env.GH_TOKEN || process.env.GITHUB_TOKEN || '';
  console.log(
    `update.js: checking ${selected.length} package(s)${ONLY ? ` (only: ${ONLY})` : ''}${token ? ' [authenticated]' : ' [anonymous api]'}`
  );

  // 阶段一：全部检查（解析 + 下载 + 校验 + 决策），任何失败不写文件
  const results = [];
  for (const p of selected) {
    const tmpPath = path.join(
      os.tmpdir(),
      `scoop-baobao-${p.key}-${process.pid}-${Date.now()}-${crypto.randomBytes(4).toString('hex')}.bin`
    );
    try {
      const mfPath = `${ROOT}/${p.manifest}`;
      const current = readManifest(mfPath);
      const up = await resolveUpstream(p, current, { token });
      let dl;
      try {
        dl = await downloadToFile(up.url, tmpPath);
        if (up.digest && dl.sha256 !== up.digest) {
          throw policyError(
            `${p.key}: sha256 digest mismatch: GitHub reports ${up.digest}, downloaded ${dl.sha256}`
          );
        }
        const magic = checkFileMagic(tmpPath, p.format);
        if (!magic.ok) {
          throw policyError(`${p.key}: unexpected file format (expected ${p.format}); ${magic.detail}`);
        }
      } finally {
        try {
          fs.rmSync(tmpPath, { force: true });
        } catch (e) {
          console.error(`  warn: temp file cleanup failed (${tmpPath}): ${e.message}`);
        }
      }
      const plan = decideUpdate(p, current, up, dl.sha256);
      results.push({ key: p.key, ok: true, plan, upstream: up.version, bytes: dl.bytes });
    } catch (e) {
      results.push({ key: p.key, ok: false, error: e.message });
    }
  }

  console.log('\n---- results ----');
  let failures = 0;
  let pending = 0;
  for (const r of results) {
    if (!r.ok) {
      failures++;
      console.error(`✗ ${r.key}: ${r.error}`);
    } else if (r.plan.changed) {
      pending++;
      console.log(`↑ ${r.key}: ${r.upstream} ready (${r.bytes} bytes)`);
    } else {
      console.log(`= ${r.key}: ${r.upstream} unchanged`);
    }
  }

  if (failures > 0) {
    console.error(`\n✗ ${failures} package(s) failed; NOTHING was written. Fix the issues and re-run.`);
    process.exit(1);
  }

  // 阶段二：全部通过后才写文件，且只写有变化的
  let written = 0;
  for (const r of results) {
    if (!r.plan.changed) continue;
    const p = PACKAGES.find((x) => x.key === r.key);
    const mfPath = `${ROOT}/${p.manifest}`;
    const m = readManifest(mfPath);
    setUrlHash(m, r.plan.url, r.plan.sha);
    m.version = r.plan.version;
    writeManifest(mfPath, m);
    written++;
    console.log(`✓ wrote ${p.manifest} -> ${r.plan.version}`);
  }
  console.log(written ? `\n✓ ${written} manifest(s) updated.` : '\n✓ all up to date, no changes.');
}

module.exports = {
  PACKAGES,
  cmpVersion,
  decideUpdate,
  escapeRegExp,
  checkFileMagic,
  fpcAssetPattern,
  fpcBuildFromUrl,
  getUrlHash,
  setUrlHash,
  requestOnce,
  fetchBuffer,
  downloadToFile,
  withRetry,
  resolveTwinkstar,
  __setTestTransport,
  __resetTestHooks,
  __private: { MAX_REDIRECTS, RETRY_LIMIT, META_TIMEOUT_MS, DL_IDLE_TIMEOUT_MS, DL_TOTAL_TIMEOUT_MS },
};

if (require.main === module) {
  main().catch((e) => {
    console.error(`\nfatal: ${e && e.stack ? e.stack : e}`);
    process.exit(1);
  });
}
