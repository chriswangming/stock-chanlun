/**
 * README 界面截图自动生成脚本（Playwright + sharp）。
 *
 * 依次用 PC 与 iPhone 13 两种视口截取 10 个页面，压缩后写入 assets/screenshots/，
 * README 第 99-143 行直接引用这些图片。
 *
 * 要点：
 * - 不参与项目构建/运行，是纯工具；依赖独立装在 scripts/ 下，避免污染前端 package.json
 * - 运行前需另起前端 dev server（推荐 npm run dev:screenshots，固定 5188）与后端
 * - 端口自动探测：HTML 里含 ChanStock 字样才算命中，避免扫到别的 Vite 项目
 * - 截图前等选择器 + 额外等待，截后校验 title/关键节点，防止把 404 页静默入库
 * - animations: 'disabled' 且非整页截图，保证多次生成结果稳定可 diff
 *
 * 用法：
 *   cd scripts && npm install && npx playwright install chromium && npm run screenshots
 * 手动指定地址：
 *   SCREENSHOT_BASE_URL=http://localhost:5188/stock-chanlun npm run screenshots
 */
import { chromium, devices } from 'playwright';
import { mkdir, unlink } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT_DIR = path.resolve(__dirname, '../assets/screenshots');
const BASE_PATH = '/stock-chanlun';
const PORT_START = Number(process.env.SCREENSHOT_PORT_START || 5173);
const PORT_END = Number(process.env.SCREENSHOT_PORT_END || 5188);

/** PC 视口（1440×900）待截页面：route 为路由，selector 为渲染完成标志，extraMs 为数据加载的额外等待。 */
const pcPages = [
  { file: 'pc-home.png', route: '/', selector: '.nav-brand', extraMs: 2000 },
  { file: 'pc-stock.png', route: '/stock/600519', selector: '.nav-brand', extraMs: 5000 },
  { file: 'pc-watchlist.png', route: '/watchlist', selector: '.nav-brand', extraMs: 2000 },
  { file: 'pc-screen.png', route: '/screen', selector: '.nav-brand', extraMs: 2000 },
  { file: 'pc-sector.png', route: '/sector/半导体', selector: '.nav-brand', extraMs: 3000 },
];

/** 移动端视口（iPhone 13）待截页面，字段含义同 pcPages。 */
const mobilePages = [
  { file: 'mobile-home.png', route: '/m/', selector: '.tab-bar', extraMs: 2000 },
  { file: 'mobile-stock.png', route: '/m/stock/600519', selector: '.tab-bar', extraMs: 5000 },
  { file: 'mobile-watchlist.png', route: '/m/watchlist', selector: '.page-title', extraMs: 2000 },
  { file: 'mobile-screen.png', route: '/m/screen', selector: '.page-title', extraMs: 2000 },
  { file: 'mobile-sector.png', route: '/m/sector/半导体', selector: '.ph-title', extraMs: 3000 },
];

/**
 * 探测单个端口上跑的是不是本项目前端。
 *
 * 要点：
 * - 127.0.0.1 与 localhost 都试一次（本机 IPv6/hosts 配置不同，命中情况不一样）
 * - 只认 HTTP 200 且 HTML 含 ChanStock 的响应，避免撞上同端口的其它 Vite 应用
 * - 4 秒超时，探测失败静默继续，不打断端口扫描
 */
async function probePort(port) {
  for (const host of ['127.0.0.1', 'localhost']) {
    const probe = `http://${host}:${port}${BASE_PATH}/`;
    try {
      const res = await fetch(probe, { signal: AbortSignal.timeout(4000) });
      if (!res.ok) continue;
      const html = await res.text();
      if (/ChanStock|chanstock/i.test(html)) {
        return `http://${host}:${port}${BASE_PATH}`;
      }
    } catch {
      /* try next host */
    }
  }
  return null;
}

/**
 * 确定前端基地址：优先 SCREENSHOT_BASE_URL，否则先试首选端口再扫 PORT_START-PORT_END。
 *
 * 返回: 形如 http://localhost:5188/stock-chanlun 的基地址（末尾无斜杠）
 */
async function detectBaseUrl() {
  const env = process.env.SCREENSHOT_BASE_URL?.replace(/\/+$/, '');
  if (env) return env;

  const preferred = Number(process.env.SCREENSHOT_PREFERRED_PORT || 5188);
  const ports = [preferred];
  for (let port = PORT_START; port <= PORT_END; port++) {
    if (port !== preferred) ports.push(port);
  }

  for (const port of ports) {
    const base = await probePort(port);
    if (base) {
      console.log(`✓ 检测到开发服务器: ${base}`);
      return base;
    }
  }
  throw new Error(
    `未找到 ChanStock 前端（优先 ${preferred}，范围 ${PORT_START}-${PORT_END}）。请先启动后端与 npm run dev:screenshots。`,
  );
}

/**
 * 压缩 PNG：转调色板（最多 256 色）+ 最高压缩级别，压缩完删掉临时文件。
 */
async function compressPng(src, dest) {
  await sharp(src)
    .png({ compressionLevel: 9, palette: true, quality: 80 })
    .toFile(dest);
  await unlink(src);
}

/**
 * 截单个页面：打开 → 等渲染 → 校验 → 截图 → 压缩。
 *
 * 要点：
 * - waitUntil 用 domcontentloaded 而非 networkidle：行情接口是轮询的，永远等不到空闲
 * - 校验条件是「title 含 ChanStock/缠论」或「页面存在 .nav-brand/.tab-bar」，
 *   两者都没有才判定为异常页（如 404）并抛错，避免把坏图静默写进仓库
 * - fullPage: false 只截首屏，配合 animations: 'disabled' 保证每次结果一致
 */
async function shot(page, base, { file, route, selector, extraMs }) {
  const url = `${base}${route}`;
  console.log(`→ ${url}`);
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector(selector, { timeout: 30000 });
  if (extraMs > 0) await page.waitForTimeout(extraMs);

  const title = await page.title();
  if (!/ChanStock|缠论/i.test(title) && !await page.locator('.nav-brand, .tab-bar').count()) {
    throw new Error(`页面校验失败: ${url}（title="${title}"）`);
  }

  const tmp = path.join(OUT_DIR, `.tmp-${file}`);
  const out = path.join(OUT_DIR, file);
  await page.screenshot({ path: tmp, fullPage: false, animations: 'disabled' });
  await compressPng(tmp, out);
  console.log(`  saved ${file}`);
}

/**
 * 主流程：建输出目录 → 探测地址 → 起无头浏览器 → PC 与移动各一个 page 依次截图。
 *
 * 两种视口各复用同一个 page，避免反复开关页面拖慢整体耗时。
 */
async function main() {
  await mkdir(OUT_DIR, { recursive: true });
  const base = await detectBaseUrl();
  const browser = await chromium.launch({ headless: true });

  const pc = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  for (const p of pcPages) await shot(pc, base, p);
  await pc.close();

  const iPhone = devices['iPhone 13'];
  const mobile = await browser.newPage({ ...iPhone, viewport: iPhone.viewport });
  for (const p of mobilePages) await shot(mobile, base, p);
  await mobile.close();

  await browser.close();
  console.log(`Done. Screenshots in ${OUT_DIR}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
