/**
 * 「研究计划」面板的真实浏览器渲染验证。
 *
 * 为什么需要它:test_plan_parser.mjs 用一个 DOM 替身,只验解析结果,render()
 * 写出的 HTML 从来没人看过 —— 面板在浏览器里到底长不长得出来、CSS 有没有生效、
 * 有没有运行时报错,全是盲区。这个脚本用无头 Chromium 加载真 CSS,把真日志喂
 * 进真 render(),再从页面里读回结构统计。
 *
 * 不需要起服务、不调任何 LLM、不花钱。
 *
 * 运行:node tests/ui_render_check.mjs [--keep]
 *       --keep 保留生成的 HTML 和截图,便于人眼复核
 *
 * 依赖 chromium 在 PATH 上(`chromium` 或 `chromium-browser`);找不到就跳过。
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';

const ROOT = path.resolve(import.meta.dirname, '..');
const KEEP = process.argv.includes('--keep');

const findChromium = () => {
  const dirs = (process.env.PATH || '').split(path.delimiter).filter(Boolean);
  for (const bin of ['chromium', 'chromium-browser', 'google-chrome-stable', 'google-chrome']) {
    for (const d of dirs) {
      const full = path.join(d, bin);
      if (fs.existsSync(full)) return full;
    }
  }
  return null;
};

const chromium = findChromium();
if (!chromium) {
  console.log('跳过:PATH 上找不到 chromium,无法做浏览器渲染验证');
  process.exit(0);
}

// ---- 从 scripts.js 里抠出 investmentPlan 模块(与解析器测试同一手法)----
const src = fs.readFileSync(path.join(ROOT, 'frontend/scripts.js'), 'utf8');
const start = src.indexOf('const investmentPlan = (() => {');
const end = src.indexOf('return { consume, reset, finish, toggleLevel };', start);
assert.ok(start > 0 && end > start, '未能在 scripts.js 中定位 investmentPlan 模块');
const moduleSrc = src.slice(start, end) + 'return { consume, reset, finish, toggleLevel };\n})();';

const fixtures = JSON.parse(
  fs.readFileSync(path.join(ROOT, 'tests/fixtures/plan_log_lines.json'), 'utf8'),
);

const work = fs.mkdtempSync(path.join(os.tmpdir(), 'ui-render-'));
for (const css of ['styles.css', 'investment-theme.css']) {
  fs.copyFileSync(path.join(ROOT, 'frontend', css), path.join(work, css));
}

// 页面自己把结论写进 <pre>,再由 --dump-dom 读回 —— 避免在外面 grep DOM
// 时把内联脚本自身的字符串也数进去(第一版就踩了这个坑,数字全是假的)。
const page = (label, lines) => `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<link rel="stylesheet" href="./styles.css"><link rel="stylesheet" href="./investment-theme.css">
<style>body{padding:24px;max-width:1000px;margin:0 auto}</style></head><body>
<div class="margin-div plan-container" id="planContainer" style="display:none">
  <h2>研究计划</h2>
  <div class="plan-route" id="planRoute"></div>
  <div class="plan-status" id="planStatus"></div>
  <div class="plan-tree" id="planTree"></div>
  <div class="plan-stats" id="planStats"></div>
</div>
<pre id="__report" style="display:none"></pre>
<script>
const errs = [];
window.onerror = (m, s, l, c, e) => errs.push(String((e && e.stack) || m));
${moduleSrc}
const LINES = ${JSON.stringify(lines)};
LINES.forEach((l, i) => {
  try { investmentPlan.consume(l); }
  catch (e) { errs.push('consume#' + i + ' ' + ((e && e.stack) || e)); }
});
try { investmentPlan.finish(); } catch (e) { errs.push('finish ' + ((e && e.stack) || e)); }
const q = (sel) => document.querySelectorAll(sel).length;
document.getElementById('__report').textContent = '@@' + JSON.stringify({
  label: ${JSON.stringify(label)},
  errors: errs,
  containerDisplay: document.getElementById('planContainer').style.display,
  levels: q('.plan-level'),
  levelHeads: q('.plan-level .level-head[data-level]'),
  nodes: q('.plan-node'),
  companyChips: q('.company-chip'),
  statusText: (document.getElementById('planStatus').textContent || '').trim(),
}) + '@@';
</script></body></html>`;

const render = (label, lines) => {
  const file = path.join(work, `${label}.html`);
  fs.writeFileSync(file, page(label, lines));
  const args = [
    '--headless', '--disable-gpu', '--no-sandbox',
    '--virtual-time-budget=3000', '--dump-dom', `file://${file}`,
  ];
  const dom = execFileSync(chromium, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
  const m = dom.match(/@@(\{.*\})@@/);
  assert.ok(m, `${label}:页面没有写出结论,可能在脚本执行前就崩了`);
  if (KEEP) {
    execFileSync(chromium, [
      '--headless', '--disable-gpu', '--no-sandbox', '--virtual-time-budget=3000',
      '--window-size=1000,1400', `--screenshot=${path.join(work, label + '.png')}`, `file://${file}`,
    ], { stdio: 'ignore' });
  }
  return JSON.parse(m[1]);
};

let passed = 0;
const check = (name, fn) => {
  try { fn(); console.log(`  ✅ ${name}`); passed++; }
  catch (e) { console.error(`  ❌ ${name}\n     ${e.message}`); process.exitCode = 1; }
};

console.log(`\n浏览器渲染验证(${path.basename(chromium)})\n`);

for (const [label, lines] of Object.entries(fixtures)) {
  const r = render(label, lines);
  check(`${label}:渲染无运行时报错`, () => {
    assert.deepEqual(r.errors, [], r.errors.join('\n'));
  });
  check(`${label}:面板从隐藏变为可见`, () => {
    assert.equal(r.containerDisplay, 'block');
  });
  check(`${label}:每一层都有可折叠的表头`, () => {
    assert.ok(r.levels >= 1, `层级数 ${r.levels}`);
    assert.equal(r.levelHeads, r.levels, `${r.levels} 层里只有 ${r.levelHeads} 个可折叠表头`);
  });
  check(`${label}:状态栏有内容`, () => {
    assert.ok(r.statusText.length > 0, '状态栏是空的,长等待期间界面上没有任何反馈');
  });
}

// 公司标签只在当前措辞的 fixture 里有(旧日志采集于措辞修改之前)
const cf = render('current_format', fixtures.current_format);
check('当前措辞:公司标签渲染出来了', () => {
  assert.equal(cf.companyChips, 5, `标签数 ${cf.companyChips}`);
  assert.equal(cf.nodes, 4, `节点数 ${cf.nodes}`);
});

console.log(`\n${passed} 项通过`);
if (KEEP) console.log(`产物保留在 ${work}`);
else fs.rmSync(work, { recursive: true, force: true });
