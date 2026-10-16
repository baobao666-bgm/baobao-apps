#!/usr/bin/env node
// 档位3 CI：拉取各上游的最新版本，重写 manifest 的 version/url/hash（scoop 校验字段）。
// 上游发新版 => 下一次 schedule/手动运行自动 bump，manifest 始终跟着走。
//
// 用法：
//   node .github/scripts/update.js            # 刷新全部包（CI 默认）
//   node .github/scripts/update.js twinkstar  # 只刷新指定包（本地手动用）
//
// 支持两类上游：
//   kind: 'github'    —— GitHub Releases（按资产名匹配）
//   kind: 'twinkstar' —— 星愿浏览器官网（浏览器 UA 抓 HTML + 版本正则 + 固定 URL 模板）
//                        注意：官网按 UA 分发链接，cURL/scoop 默认 UA 会拿到停更的 32 位旧包

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const https = require('https');

const ROOT = process.cwd();
const ONLY = process.argv[2] || ''; // 可选：只刷新指定包

function httpGet(url, headers = {}) {
  return new Promise((resolve, reject) => {
    https.get(url, { headers: { 'User-Agent': 'scoop-bucket-ci', ...headers } }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return httpGet(res.headers.location, headers).then(resolve, reject);
      }
      if (res.statusCode !== 200) {
        res.resume();
        return reject(new Error(`HTTP ${res.statusCode} for ${url}`));
      }
      let chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve(Buffer.concat(chunks)));
    }).on('error', reject);
  });
}

async function jsonGet(url) {
  const b = await httpGet(url);
  return JSON.parse(b.toString('utf8'));
}

async function sha256Of(url, outPath) {
  // 下载并流式算 sha256（大文件友好）
  const hash = crypto.createHash('sha256');
  const res = await new Promise((resolve, reject) => {
    https.get(url, (r) => {
      if (r.statusCode >= 300 && r.statusCode < 400) {
        https.get(r.headers.location, resolve);
      } else {
        resolve(r);
      }
    }).on('error', reject);
  });
  const file = fs.createWriteStream(outPath);
  res.pipe(file);
  res.pipe(hash);
  await new Promise((r) => file.on('finish', r));
  return hash.digest('hex');
}

function readManifest(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}
function writeManifest(file, m) {
  fs.writeFileSync(file, JSON.stringify(m, null, 2) + '\n');
}

// 每个包：kind + 上游信息 + 本地 manifest 路径
const PACKAGES = [
  {
    key: 'fingerprint-chromium',
    kind: 'github',
    repo: 'adryfish/fingerprint-chromium',
    manifest: 'bucket/fingerprint-chromium.json',
    asset: (tag) => `ungoogled-chromium_${tag}-1.1_windows_x64.zip`,
    versionFromTag: (tag) => tag, // tag 本身即版本（如 150.0.7871.186）
  },
  {
    key: 'OpenBrowser',
    kind: 'github',
    repo: 'sheying2013/OpenBrowser',
    manifest: 'bucket/OpenBrowser.json',
    asset: () => `OpenBrowser-Windows-x86_64-with-kernel.zip`,
    versionFromTag: (tag) => tag.replace(/^v/, ''),
  },
  {
    key: 'twinkstar',
    kind: 'twinkstar',
    manifest: 'bucket/Twinkstar.json',
    homepage: 'https://www.twinkstar.com/',
    ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    regex: /Twinkstar_v([\d.]+)_Release\.exe/,
    buildUrl: (v) => `https://d1.twinkstar.com/win/Twinkstar_v${v}_Release.exe`,
    urlSuffix: '#/dl.exe', // 固定落盘名：Twinkstar.json 的 installer 脚本依赖 $dir\dl.exe
  },
];

async function resolveUpstream(p) {
  if (p.kind === 'twinkstar') {
    const html = (await httpGet(p.homepage, { 'User-Agent': p.ua })).toString('utf8');
    const m = html.match(p.regex);
    if (!m) throw new Error('version not found on homepage (UA dispatch or page layout changed?)');
    const version = m[1];
    return { version, url: p.buildUrl(version) + (p.urlSuffix || '') };
  }

  // kind === 'github'
  const rel = await jsonGet(`https://api.github.com/repos/${p.repo}/releases?per_page=1`);
  if (!Array.isArray(rel) || rel.length === 0) throw new Error('no release found');
  const tag = rel[0].tag_name;
  const assetName = p.asset(tag);
  const asset = rel[0].assets.find((a) => a.name === assetName) || rel[0].assets.find((a) => /windows.*\.zip$/i.test(a.name));
  if (!asset) throw new Error(`no windows zip asset in ${tag}`);
  return { version: p.versionFromTag(tag), url: asset.browser_download_url };
}

async function main() {
  for (const p of PACKAGES) {
    if (ONLY && p.key !== ONLY) continue;

    const mfPath = `${ROOT}/${p.manifest}`;
    const current = readManifest(mfPath);

    let upstream, sha;
    try {
      upstream = await resolveUpstream(p);
      sha = await sha256Of(upstream.url, path.join(os.tmpdir(), `${p.key}.bin`));
    } catch (e) {
      // 抓取/下载失败：跳过该包，绝不写入坏数据
      console.error(`${p.key}: ${e.message}, skip`);
      continue;
    }

    const changed =
      current.url !== upstream.url ||
      current.version !== upstream.version ||
      (current.hash || current.sha256) !== sha;

    current.url = upstream.url;
    current.version = upstream.version;
    current.hash = sha; // scoop 校验字段（无 "type:" 前缀时默认按 sha256 解析）
    delete current.sha256; // 清理历史遗留的错字段，避免再次混入
    writeManifest(mfPath, current);
    console.log(
      `${p.key}: ${current.version}  url=${upstream.url}  hash=${sha}  ${changed ? '(updated)' : '(unchanged)'}`
    );
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
