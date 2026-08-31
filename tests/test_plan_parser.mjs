/**
 * 「研究计划」面板解析器的回归测试(Slice E · UI)。
 *
 * 素材是五条真实用例跑出来的日志(tests/fixtures/plan_log_lines.json),
 * 共 1009 行 —— 上一版解析器就是靠真实日志才发现两个 bug:
 *   1) 上游自己也发 "🔍 Running research for '...'",被误吞后凭空造出假层级
 *   2) "Tesla, Inc.(TSLA)" 被按逗号切开,一家公司裂成两家
 * 所以这里坚持用真日志而不是手写样例。
 *
 * 运行:node tests/test_plan_parser.mjs
 */
import fs from 'node:fs';
import assert from 'node:assert/strict';

// ---- 从 scripts.js 里把 investmentPlan 模块单独抠出来跑 ----
// 它依赖 document,用一个最小替身满足;render 只写 DOM,不影响解析结果。
const src = fs.readFileSync('frontend/scripts.js', 'utf8');
const start = src.indexOf('const investmentPlan = (() => {');
const end = src.indexOf('return { consume, reset, finish, toggleLevel };', start);
assert.ok(start > 0 && end > start, '未能在 scripts.js 中定位 investmentPlan 模块');
const moduleSrc = src.slice(start, end) + 'return { consume, reset, finish, toggleLevel, _state: () => state };\n})();';

const els = new Map();
const stubEl = () => ({ innerHTML: '', textContent: '', className: '', style: {} });
globalThis.document = {
  getElementById: (id) => { if (!els.has(id)) els.set(id, stubEl()); return els.get(id); },
  addEventListener() {},
};
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};

const plan = new Function(`${moduleSrc} return investmentPlan;`)();

const fixtures = JSON.parse(fs.readFileSync('tests/fixtures/plan_log_lines.json', 'utf8'));

let passed = 0;
const check = (name, fn) => {
  try { fn(); console.log(`  ✅ ${name}`); passed++; }
  catch (e) { console.error(`  ❌ ${name}\n     ${e.message}`); process.exitCode = 1; }
};

/** 把一批日志喂进解析器,返回 {state, consumed, passedThrough} */
const run = (lines) => {
  plan.reset();
  let consumed = 0, passedThrough = 0;
  for (const l of lines) (plan.consume(l) ? consumed++ : passedThrough++);
  return { state: plan._state(), consumed, passedThrough };
};

console.log('\n解析器测试(素材:三份真实运行日志)\n');

check('上游的 "Running research for" 不被吞掉', () => {
  const lines = fixtures.value_chain;
  const upstream = lines.filter((l) => /^🔍\s*Running research for/.test(l));
  assert.ok(upstream.length > 20, `样本太少(${upstream.length}),测不出问题`);
  for (const l of upstream) {
    assert.equal(plan.consume(l), false, `被误吞:${l.slice(0, 60)}`);
  }
});

check('value_chain 解析出三层,L1/L2 各有内容', () => {
  const { state } = run(fixtures.value_chain);
  assert.equal(state.label, 'value_chain');
  const tags = state.levels.map((l) => l.tag);
  assert.deepEqual(tags, ['L1', 'L2', 'L3'], `层级不对:${tags}`);
  assert.ok(state.levels[0].nodes.length > 0, 'L1 没有环节');
  assert.ok(state.levels[1].nodes.length > 0, 'L2 没有各环节代表公司');
  // L3 的逐家明细来自新增的 "🔬 <代码> 指标已获取" 日志,历史日志里还没有,
  // 因此单独用合成样例覆盖(见下一条)。
});

check('theme_analysis 同样解析出三层', () => {
  const { state } = run(fixtures.theme_analysis);
  assert.equal(state.label, 'theme_analysis');
  assert.deepEqual(state.levels.map((l) => l.tag), ['L1', 'L2', 'L3']);
  assert.ok(state.levels[0].nodes.length > 0, 'L1 没有受益类别');
  assert.ok(state.levels[1].nodes.length > 0, 'L2 没有各类别代表公司');
});

check('sector_landscape 解析出两层', () => {
  const { state } = run(fixtures.sector_landscape);
  assert.equal(state.label, 'sector_landscape');
  assert.ok(state.levels.length >= 2, `层数不足:${state.levels.length}`);
  assert.ok(state.levels[0].nodes.length > 0, 'L1 没有代表公司');
});

check('L2 的节点带出美股代码', () => {
  const { state } = run(fixtures.value_chain);
  const withTickers = state.levels[1].nodes.filter((n) => n.tickers.length);
  assert.ok(withTickers.length > 0, 'L2 一个代码都没解析出来');
});

check('L3 逐家状态区分成功与失败(新增日志格式,合成样例)', () => {
  // 这几行是手写的:格式本次才加进后端,历史日志里没有。
  // 其余用例一律用真实日志。
  plan.reset();
  plan.consume('🎯 L0-A 标签:value_chain');
  plan.consume('🔍 Level 3:每家公司 2 条,共 16 条检索');
  ['NVDA', 'AMD', 'TSM'].forEach((t) => plan.consume(`🔬 ${t} 指标已获取`));
  plan.consume('🔬 LRCX 指标未取到');

  const lv = plan._state().levels.find((l) => l.tag === 'L3');
  assert.ok(lv, '没有建出 L3');
  assert.equal(lv.companies.length, 4);
  assert.equal(lv.companies.filter((c) => c.ok).length, 3);
  const miss = lv.companies.find((c) => !c.ok);
  assert.equal(miss.ticker, 'LRCX');
});

check('同一家公司重复播报不会产生重复条目', () => {
  plan.reset();
  plan.consume('🔬 NVDA 指标未取到');
  plan.consume('🔬 NVDA 指标已获取');
  const lv = plan._state().levels.find((l) => l.tag === 'L3');
  assert.equal(lv.companies.length, 1, '同一家出现了两次');
  assert.equal(lv.companies[0].ok, true, '后到的状态没有覆盖先前的');
});

check('company_comparison 的公司名含逗号时不被切开', () => {
  const { state } = run(fixtures.company_comparison);
  assert.equal(state.label, 'company_comparison');
  const names = state.levels.flatMap((l) => l.nodes.map((n) => n.name));
  assert.ok(names.length >= 2, `公司数不对:${JSON.stringify(names)}`);
  for (const n of names) assert.ok(!/^\s*(Inc|Ltd|Corp)\.?$/i.test(n), `被切碎:${n}`);
});

check('检索进度计数不超过该层声明的条数', () => {
  const { state } = run(fixtures.value_chain);
  for (const lv of state.levels) {
    if (lv.count) assert.ok((lv.done || 0) <= lv.count, `${lv.tag} 进度 ${lv.done} > ${lv.count}`);
  }
});

check('阶段会随日志推进', () => {
  const { state } = run(fixtures.value_chain);
  assert.ok(state.phase && state.phase !== '正在分析问题类型', `阶段没推进:${state.phase}`);
});

check('finish() 后停在完成态', () => {
  run(fixtures.value_chain);
  plan.finish();
  const st = plan._state();
  assert.equal(st.finished, true);
  assert.equal(st.phase, '研究完成');
});

check('reset() 清空全部状态', () => {
  run(fixtures.value_chain);
  plan.reset();
  const st = plan._state();
  assert.equal(st.label, null);
  assert.equal(st.levels.length, 0);
  assert.equal(st.finished, false);
});

check('绝大多数日志仍然流向滚动日志', () => {
  const { consumed, passedThrough } = run(fixtures.value_chain);
  assert.ok(consumed > 5, `计划面板一条都没接管(${consumed})`);
  assert.ok(passedThrough > consumed * 3, `吞得太多:接管 ${consumed} / 放行 ${passedThrough}`);
});


// ---- 当前措辞的覆盖(修完 38 处措辞之后新增)----------------------------
// 上面四份 fixture 是**改措辞之前**采集的真实日志,里面还留着 "摸 …"/"拆 …"
// 那批旧文案。它们对"解析器别被旧日志噎住"仍然有效,但完全测不到当前代码实际
// 发出的文案 —— 浏览器渲染验证时就因此看到 0 个公司标签,一度以为是功能坏了,
// 实际只是 fixture 里没有 "🔬 <ticker> 指标已获取" 这类行。这一组按当前措辞写。

check('当前措辞:三层都能解析出来', () => {
  const { state } = run(fixtures.current_format);
  assert.equal(state.label, 'theme_analysis');
  assert.deepEqual(state.levels.map((l) => l.tag), ['L1', 'L2', 'L3']);
});

check('当前措辞:受益类别落成 L2 的节点', () => {
  const { state } = run(fixtures.current_format);
  const l2 = state.levels.find((l) => l.tag === 'L2');
  assert.equal(l2.nodes.length, 4, `类别数不对:${JSON.stringify(l2.nodes.map((n) => n.name))}`);
  assert.ok(l2.nodes.some((n) => n.name === '冷却系统'), '无美股的类别被丢掉了');
});

check('当前措辞:公司标签带成功/失败状态', () => {
  const { state } = run(fixtures.current_format);
  const l3 = state.levels.find((l) => l.tag === 'L3');
  assert.equal(l3.companies.length, 5, `标签数不对:${l3.companies.length}`);
  const etn = l3.companies.find((c) => c.ticker === 'ETN');
  assert.ok(etn && etn.ok === false, 'ETN 未取到的状态没有记住');
  assert.equal(l3.companies.filter((c) => c.ok).length, 4);
});

console.log(`\n${passed} 项通过\n`);
