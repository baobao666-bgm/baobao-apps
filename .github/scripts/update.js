#!/usr/bin/env node
// 档位3 CI：拉取两个上游的最新 release，重写 manifest 的 version/url/hash（scoop 校验字段）。
// 上游发新版 => 下一次 schedule/手动运行自动 bump，manifest 始终跟着走。

const fs = require('fs');
const crypto = require('crypto');
const https = require('https');

const ROOT = process.cwd();

function httpGet(url) {
  return new Promise((resolve, reject) => {
    https.get(url, { headers: { 'User-Agent': 'scoop-bucket-ci' } }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return httpGet(res.headers.location).then(resolve, reject);
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

function jsonGet(url) {
  const token = process.env.GITHUB_TOKEN;
  return httpGet(url)
    .then((b) => b.toString('utf8'))
    .then((s) => {
      if (token) s = s; // token only used for rate limit; fine without
      return JSON.parse(s);
    });
}

async function sha256Of(url, outPath) {
  // 下载并流式算 sha256（大文件友好）
  const { spawn } = require('child_process');
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

// 每个包：上游 repo + 资产名正则 + 版本取自 tag 的规则 + 本地 manifest 路径
const PACKAGES = [
  {
    key: 'fingerprint-chromium',
    repo: 'adryfish/fingerprint-chromium',
    manifest: 'fingerprint-chromium.json',
    asset: (tag) => `ungoogled-chromium_${tag}-1.1_windows_x64.zip`,
    versionFromTag: (tag) => tag, // tag 本身即版本（如 150.0.7871.186）
  },
  {
    key: 'OpenBrowser',
    repo: 'sheying2013/OpenBrowser',
    manifest: 'OpenBrowser.json',
    asset: (tag) => `OpenBrowser-Windows-x86_64-with-kernel.zip`,
    versionFromTag: (tag) => tag.replace(/^v/, ''),
  },
];

async function main() {
  for (const p of PACKAGES) {
    const mfPath = `${ROOT}/${p.manifest}`;
    const current = readManifest(mfPath);

    const rel = await jsonGet(`https://api.github.com/repos/${p.repo}/releases?per_page=1`);
    if (!Array.isArray(rel) || rel.length === 0) {
      console.error(`${p.key}: no release found, skip`);
      continue;
    }
    const tag = rel[0].tag_name;
    const assetName = p.asset(tag);
    const asset = rel[0].assets.find((a) => a.name === assetName);
    const browserAsset = asset || rel[0].assets.find((a) => /windows.*\.zip$/i.test(a.name));
    if (!browserAsset) {
      console.error(`${p.key}: no windows zip asset in ${tag}`);
      continue;
    }
    const newUrl = browserAsset.browser_download_url;
    const newVersion = p.versionFromTag(tag);
    const sha = await sha256Of(newUrl, `/tmp/${p.key}.zip`);

    const changed =
      current.url !== newUrl ||
      current.version !== newVersion ||
      (current.hash || current.sha256) !== sha;

    current.url = newUrl;
    current.version = newVersion;
    current.hash = sha; // scoop 校验字段（无 "type:" 前缀时默认按 sha256 解析）
    delete current.sha256; // 清理历史遗留的错字段，避免再次混入
    writeManifest(mfPath, current);
    console.log(
      `${p.key}: ${current.version}  url=${newUrl}  hash=${sha}  ${changed ? '(updated)' : '(unchanged)'}`
    );
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
