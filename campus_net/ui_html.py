"""设置窗口与迷你看板的内嵌 HTML 页面（纯字符串模块，无逻辑）。

页面通过 window.pywebview.api 调用 ui.UiApi 暴露的方法。
支持浅色/深色主题：跟随系统（prefers-color-scheme）或经 data-theme
属性强制指定，主题选择持久化在 config.ini（Settings.ui_theme）。
"""
from . import __version__

# 深色主题的 CSS 变量（data-theme=dark 与系统深色两处共用）
_DARK_VARS = """
    --bg: #0f172a; --text: #e2e8f0; --heading: #f8fafc;
    --card-bg: #1e293b; --card-border: #334155;
    --muted: #94a3b8; --hint: #64748b;
    --input-bg: #0f172a; --input-border: #475569;
    --btn2-bg: #334155; --btn2-text: #e2e8f0;
    --danger-bg: #7f1d1d; --danger-text: #fecaca;
    --result-ok: #34d399; --result-err: #f87171;
    --banner-bg: #422006; --banner-border: #f59e0b;
    --lbl: #94a3b8; --summary: #94a3b8;
"""

_BASE_CSS = """
  :root {
    --bg: #f1f5f9; --text: #1e293b; --heading: #0f172a;
    --card-bg: #ffffff; --card-border: #e2e8f0;
    --muted: #64748b; --hint: #94a3b8;
    --input-bg: #ffffff; --input-border: #cbd5e1;
    --btn2-bg: #e2e8f0; --btn2-text: #334155;
    --danger-bg: #fee2e2; --danger-text: #b91c1c;
    --result-ok: #059669; --result-err: #dc2626;
    --banner-bg: #fffbeb; --banner-border: #f59e0b;
    --lbl: #475569; --summary: #64748b;
  }
  :root[data-theme="dark"] {""" + _DARK_VARS + """  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {""" + _DARK_VARS + """  }
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "Microsoft YaHei", "Segoe UI", sans-serif; background: var(--bg); color: var(--text); font-size: 14px; }
  .wrap { max-width: 520px; margin: 0 auto; padding: 14px; }
  h1 { font-size: 17px; color: var(--heading); }
  .card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 14px; padding: 15px 17px; margin-bottom: 12px; box-shadow: 0 1px 2px rgba(15,23,42,.08); }
  .card-title { display: flex; align-items: center; gap: 8px; font-weight: 700; color: var(--heading); margin-bottom: 10px; }
  .card-title::before { content: ""; width: 4px; height: 14px; border-radius: 2px; background: #10b981; }
  .status-row { display: flex; align-items: center; gap: 10px; }
  .dot { width: 13px; height: 13px; border-radius: 50%; background: #94a3b8; flex: none; box-shadow: 0 0 0 4px rgba(148,163,184,.15); }
  #state-text { font-size: 16px; font-weight: 600; }
  .muted { color: var(--muted); font-size: 12px; }
  .meta { display: flex; gap: 14px; margin-top: 8px; flex-wrap: wrap; }
  label { display: block; margin: 10px 0 4px; font-weight: 600; }
  label .req { color: #ef4444; }
  input[type=text], input[type=password], input[type=number], input[type=time], select { width: 100%; padding: 8px 10px; border: 1px solid var(--input-border); border-radius: 8px; font-size: 13px; background: var(--input-bg); color: var(--text); }
  input:focus, select:focus { outline: none; border-color: #10b981; box-shadow: 0 0 0 3px rgba(16,185,129,.15); }
  .hint { font-size: 12px; color: var(--hint); margin-top: 4px; line-height: 1.5; }
  details { margin-top: 12px; }
  summary { cursor: pointer; font-weight: 600; color: var(--summary); }
  .btns { display: flex; gap: 9px; margin-top: 12px; }
  button { padding: 9px 0; border: none; border-radius: 8px; font-size: 14px; cursor: pointer; font-weight: 600; transition: filter .15s; }
  .btns button { flex: 1; }
  button:hover { filter: brightness(.95); }
  button:active { filter: brightness(.88); }
  .btn-primary { background: #10b981; color: #fff; }
  .btn-secondary { background: var(--btn2-bg); color: var(--btn2-text); }
  .btn-danger { background: var(--danger-bg); color: var(--danger-text); }
  .btn-sm { background: var(--btn2-bg); color: var(--btn2-text); padding: 7px 12px; font-size: 13px; flex: none; }
  #result { margin-top: 10px; font-size: 13px; display: none; }
  #result.ok { color: var(--result-ok); }
  #result.err { color: var(--result-err); white-space: pre-line; }
  .checkline { display: flex; align-items: center; gap: 6px; margin-top: 6px; font-size: 13px; }
  .checkline input { width: auto; }
  .checkline label { margin: 0; font-weight: 400; }
  .timer-big { font-size: 26px; font-weight: 700; color: var(--heading); font-variant-numeric: tabular-nums; }
  .timer-big.inactive { color: var(--hint); font-size: 16px; font-weight: 600; }
  .timer-row { display: flex; align-items: center; gap: 8px; margin-top: 9px; flex-wrap: wrap; }
  .timer-row input[type=number] { width: 84px; }
  .timer-row input[type=time] { width: 120px; }
  .timer-row .lbl { font-size: 13px; color: var(--lbl); flex: none; }
  #timer-msg { margin-top: 6px; font-size: 12px; color: #f59e0b; min-height: 14px; }
  #check-overlay { position: fixed; left: 0; top: 0; right: 0; bottom: 0; background: rgba(15, 23, 42, .55); display: none; align-items: center; justify-content: center; z-index: 999; }
  #check-card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 14px; padding: 26px 34px; text-align: center; box-shadow: 0 12px 36px rgba(0,0,0,.35); min-width: 230px; }
  .check-spinner { width: 34px; height: 34px; border: 4px solid rgba(59, 130, 246, .25); border-top-color: #3b82f6; border-radius: 50%; animation: cn-spin .9s linear infinite; margin: 0 auto 12px; }
  @keyframes cn-spin { to { transform: rotate(360deg); } }
  #check-title { font-size: 15px; font-weight: 600; color: var(--heading); }
  #check-detail { font-size: 12px; color: var(--muted); margin-top: 6px; }
  p { margin: 6px 0; line-height: 1.7; }
"""

_SETTINGS_CSS = _BASE_CSS + """
  .banner { background: var(--banner-bg); border: 1px solid var(--banner-border); border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; font-size: 13px; display: none; }
  .title-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .title-actions { display: flex; gap: 8px; align-items: center; }
  .theme-select { width: auto; padding: 6px 10px; font-size: 13px; flex: none; }
  .guide-step { font-size: 12px; color: var(--muted); font-weight: 400; margin-left: 8px; }
"""

_SETTINGS_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>校园网守护 - 设置</title>
<style>""" + _SETTINGS_CSS + """
</style>
</head>
<body>
<div class="wrap">
  <div class="title-row">
    <h1>校园网自动登录守护 __VERSION__</h1>
    <div class="title-actions">
      <select id="theme-select" class="theme-select" onchange="changeTheme(this.value)">
        <option value="system">主题：跟随系统</option>
        <option value="light">主题：浅色</option>
        <option value="dark">主题：深色</option>
      </select>
      <button class="btn-sm" onclick="toggleGuide()">帮助</button>
    </div>
  </div>
  <div class="banner" id="config-banner">尚有必填项未完成：填写下方带 * 的项后点击「保存并应用」即可开始守护；直接关闭窗口将退出程序。</div>

  <div class="card" id="guide-card" style="display:none">
    <div class="card-title">新手引导 <span class="guide-step" id="guide-step">第 1 / 3 步</span></div>
    <div id="guide-body"></div>
    <div class="btns">
      <button class="btn-secondary" onclick="guidePrev()">上一步</button>
      <button class="btn-primary" id="guide-next" onclick="guideNext()">下一步</button>
    </div>
  </div>

  <div class="card">
    <div class="card-title">运行状态</div>
    <div class="status-row"><span class="dot" id="dot"></span><span id="state-text">加载中...</span></div>
    <div id="state-msg" class="muted" style="margin-top:5px"></div>
    <div class="meta muted"><span id="last-check">上次检测: -</span><span id="counts">成功 0 次 / 失败 0 次</span></div>
    <div id="check-overlay">
      <div id="check-card">
        <div id="check-spinner" class="check-spinner"></div>
        <div id="check-title"></div>
        <div id="check-detail"></div>
      </div>
    </div>
    <div class="hint">临时计时器已移至托盘左键的迷你看板中。</div>
  </div>

  <div class="card">
    <div class="card-title">账号与认证</div>
    <label>登录账号 <span class="req">*</span></label>
    <input type="text" id="username" placeholder="一般是学号">
    <label>登录密码 <span class="req">*</span></label>
    <input type="password" id="password">
    <div class="checkline"><input type="checkbox" id="show-pass"><label for="show-pass">显示密码</label></div>
    <label>认证页地址 login_url <span class="req">*</span></label>
    <input type="text" id="login_url" placeholder="http://10.1.1.55/srun_portal_pc?ac_id=1">
    <div class="hint">断网时随便打开一个网页被跳转到的那个地址，需以 http:// 或 https:// 开头</div>
    <label>内网探测地址 internal_test_url <span class="req">*</span></label>
    <input type="text" id="internal_test_url" placeholder="认证服务器 IP，如 10.1.1.55">
  </div>

  <div class="card">
    <div class="card-title">探测与高级</div>
    <label>外网探测地址 external_test_url</label>
    <input type="text" id="external_test_url" placeholder="www.baidu.com">
    <label>检测间隔（秒）</label>
    <input type="number" id="check_interval" min="5">
    <details>
      <summary>门户元素 ID / 重试参数 / 通知</summary>
      <label>账号输入框 ID（username_input_id）</label>
      <input type="text" id="username_input_id">
      <label>密码输入框 ID（password_input_id）</label>
      <input type="text" id="password_input_id">
      <label>登录按钮 ID（login_btn_id）</label>
      <input type="text" id="login_btn_id">
      <label>注销按钮 ID（logout_btn_id）</label>
      <input type="text" id="logout_btn_id">
      <div class="hint">换学校门户时按 F12 查看对应元素的 id，可点上方「帮助」查看图文说明</div>
      <label>登录额外重试次数（quick_retry_times）</label>
      <input type="number" id="quick_retry_times">
      <label>退避阈值（max_retries）</label>
      <input type="number" id="max_retries">
      <label>退避等待秒数（retry_delay）</label>
      <input type="number" id="retry_delay">
      <label>退出登录兜底方式（logout_fallback）</label>
      <select id="logout_fallback">
        <option value="browser">自动确认失败后 → 系统浏览器手动退出</option>
        <option value="window">自动确认失败后 → 应用内窗口手动退出</option>
        <option value="manual">强制应用内窗口手动退出（跳过自动流程）</option>
        <option value="none">不使用兜底</option>
      </select>
      <div class="hint">一键退出登录自动确认失败时，按此方式打开认证页供手动退出；触发后守护自动暂停，手动完成后需手动恢复守护。门户注销弹窗非标准、自动点击无效时选「强制应用内窗口」。</div>
      <div class="checkline"><input type="checkbox" id="show_notifications"><label for="show_notifications">登录成功/失败时显示系统气泡通知</label></div>
    </details>
    <div class="btns">
      <button class="btn-secondary" onclick="triggerLogin()">立即检测</button>
      <button class="btn-primary" onclick="saveForm()">保存并应用</button>
      <button class="btn-secondary" onclick="closeWindow()">关闭</button>
    </div>
    <div id="result"></div>
  </div>
</div>
<script>
const FIELDS = ['username', 'password', 'login_url', 'internal_test_url', 'external_test_url',
  'check_interval', 'username_input_id', 'password_input_id', 'login_btn_id', 'logout_btn_id',
  'quick_retry_times', 'max_retries', 'retry_delay', 'logout_fallback'];

function api() { return window.pywebview && window.pywebview.api; }
// 必须等到具体方法挂载完成：pywebview 注入分两步（先建空 api 对象，
// 反射遍历后才挂方法），开机繁忙时间隙可达数百毫秒，只判对象存在
// 会在间隙里放行，后续 api().get_status 报 TypeError 且轮询循环死掉
async function waitApi() { while (!api() || !api().get_status) { await new Promise(r => setTimeout(r, 200)); } }

// ---- 低占用可见性开关：窗口隐藏时全部轮询跳过，Python 侧 show/hide 调 __setUiVisible ----
window.__cnVisible = false;
function __setUiVisible(v) {
  window.__cnVisible = v;
  if (v) {
    refreshStatus().catch(() => {});
    refreshTheme().catch(() => {});
  }
}

// ---- 主题 ----
function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'light' || theme === 'dark') { root.dataset.theme = theme; }
  else { delete root.dataset.theme; }  // system：交给 prefers-color-scheme 决定
}

async function refreshTheme() {
  if (!api()) return;
  const t = await api().get_theme();
  if (t && t.theme) {
    applyTheme(t.theme);
    const sel = document.getElementById('theme-select');
    if (sel && sel.value !== t.theme) { sel.value = t.theme; }
  }
}

async function changeTheme(value) {
  applyTheme(value);  // 本窗即时反馈
  const res = await api().set_theme(value);
  if (!res.ok) { showResult(res.problem, false); refreshTheme(); }
}

const STATE_COLOR = { connected: '#10b981', logging_in: '#3b82f6', logging_out: '#3b82f6',
  portal_blocked: '#ef4444', offline: '#ef4444', paused: '#94a3b8', waiting_config: '#f59e0b' };

// ---- 手动检测反馈：全页覆盖面板 ----
// pending 态无定时器、保持到本次请求的完成到达；结果展示 3 秒后自动消失。
// checkSeqAtRequest 是 seq 哨兵：点击时已知的最新完成序号，旧检测的迟到
// 完成（seq 不大于哨兵）不会覆盖/关闭面板。
let lastManualSeq = 0;
let manualSeqReady = false;
let checkHideTimer = null;
let checkElapsedTimer = null;
let checkOverlayPending = false;
let checkSeqAtRequest = null;

function showCheckOverlay() {
  const overlay = document.getElementById('check-overlay');
  if (checkHideTimer) { clearTimeout(checkHideTimer); checkHideTimer = null; }
  if (checkElapsedTimer) { clearInterval(checkElapsedTimer); checkElapsedTimer = null; }
  const spinner = document.getElementById('check-spinner');
  if (spinner) { spinner.style.display = 'block'; }
  const title = document.getElementById('check-title');
  title.textContent = '正在检测网络状态…';
  title.style.color = '';
  document.getElementById('check-detail').textContent = '已进行 0 秒';
  overlay.style.display = 'flex';
  checkOverlayPending = true;
  const started = Date.now();
  checkElapsedTimer = setInterval(function () {
    document.getElementById('check-detail').textContent =
      '已进行 ' + Math.floor((Date.now() - started) / 1000) + ' 秒';
  }, 1000);
}

function showCheckResult(ok, text) {
  if (checkElapsedTimer) { clearInterval(checkElapsedTimer); checkElapsedTimer = null; }
  const spinner = document.getElementById('check-spinner');
  if (spinner) { spinner.style.display = 'none'; }
  const title = document.getElementById('check-title');
  title.textContent = text;
  title.style.color = ok ? 'var(--result-ok)' : '#f59e0b';
  document.getElementById('check-detail').textContent = '面板将在 3 秒后自动关闭';
  checkOverlayPending = false;
  if (checkHideTimer) { clearTimeout(checkHideTimer); checkHideTimer = null; }
  checkHideTimer = setTimeout(function () {
    document.getElementById('check-overlay').style.display = 'none';
    checkHideTimer = null;
  }, 3000);
}

function pollManualCheck(s) {
  const mc = s.manual_check;
  if (!mc) return;
  if (!manualSeqReady) {
    // 首次见到完成记录：默认作为历史基线。但面板等待中且晚于点击哨兵的
    // 记录是本次点击的结果——app 刚启动时的第一次检测没有历史基线，
    // 若一并吞掉，用户的第一次检测永远看不到结果（生产实锤过的 bug）。
    if (checkOverlayPending && checkSeqAtRequest !== null && mc.seq > checkSeqAtRequest) {
      lastManualSeq = mc.seq;
      manualSeqReady = true;
      const skipped0 = String(mc.text || '').indexOf('跳过') !== -1;
      showCheckResult(!skipped0, (mc.time ? mc.time + ' · ' : '') + (mc.text || '手动检测完成'));
      return;
    }
    lastManualSeq = mc.seq;
    manualSeqReady = true;
    return;
  }
  if (mc.seq > lastManualSeq) {
    lastManualSeq = mc.seq;
    if (mc.source !== 'dashboard') return;  // 托盘来源走系统气泡
    if (checkOverlayPending && checkSeqAtRequest !== null && mc.seq <= checkSeqAtRequest) {
      return;  // 旧检测的迟到完成：不属于本次点击，不覆盖面板
    }
    const skipped = String(mc.text || '').indexOf('跳过') !== -1;
    showCheckResult(!skipped, (mc.time ? mc.time + ' · ' : '') + (mc.text || '手动检测完成'));
  }
}

async function refreshStatus() {
  if (!api()) return;
  const s = await api().get_status();
  document.getElementById('dot').style.background = STATE_COLOR[s.state] || '#94a3b8';
  document.getElementById('state-text').textContent = s.state_text;
  document.getElementById('state-msg').textContent = s.message || '';
  document.getElementById('last-check').textContent = '上次检测: ' + s.last_check_time;
  document.getElementById('counts').textContent = '成功 ' + s.success_count + ' 次 / 失败 ' + s.fail_count + ' 次';
  document.getElementById('config-banner').style.display = (s.state === 'waiting_config') ? 'block' : 'none';
  pollManualCheck(s);
  if (s.state === 'waiting_config' && !guideAutoShown) {
    guideAutoShown = true;
    document.getElementById('guide-card').style.display = 'block';
    renderGuide();
  }
}

async function loadConfig() {
  await waitApi();
  // 轮询先注册、单次失败不中断；隐藏时跳过（低占用，重见时 __setUiVisible 即刷一轮）
  setInterval(() => { if (window.__cnVisible === false) return; refreshStatus().catch(() => {}); }, 1000);
  setInterval(() => { if (window.__cnVisible === false) return; refreshTheme().catch(() => {}); }, 1000);
  try {
    const c = await api().get_config();
    for (const f of FIELDS) { document.getElementById(f).value = c[f]; }
    document.getElementById('show_notifications').checked = !!c.show_notifications;
  } catch (e) {
    showResult('配置加载失败：' + e, false);
  }
  refreshStatus().catch(() => {});
  refreshTheme().catch(() => {});
}

function showResult(text, ok) {
  const box = document.getElementById('result');
  box.className = ok ? 'ok' : 'err';
  box.style.display = 'block';
  box.textContent = text;
}

async function saveForm() {
  const data = {};
  for (const f of FIELDS) { data[f] = document.getElementById(f).value; }
  data.show_notifications = document.getElementById('show_notifications').checked;
  const res = await api().save_config(data);
  if (res.ok) {
    showResult('已保存，配置即时生效', true);
    setTimeout(() => api().close_settings(), 900);
  } else {
    showResult('保存失败：\\n' + res.problems.join('\\n'), false);
  }
}

async function triggerLogin() {
  showCheckOverlay();
  try {
    await waitApi();
    // 取点击时刻的精确完成哨兵：面板只认晚于该序号的完成，历史不干扰
    const snapshot = await api().get_status();
    const seq = snapshot.manual_check ? snapshot.manual_check.seq : 0;
    checkSeqAtRequest = seq;
    if (seq > lastManualSeq) { lastManualSeq = seq; }
    manualSeqReady = manualSeqReady || !!snapshot.manual_check;
    await api().trigger_login();
  } catch (e) {
    showCheckResult(false, '触发失败：' + e);
  }
}

function closeWindow() { api().close_settings(); }

document.getElementById('show-pass').addEventListener('change', function () {
  document.getElementById('password').type = this.checked ? 'text' : 'password';
});

// ---- 新手引导向导 ----
const GUIDE_STEPS = [
  '<p><b>这个工具做什么？</b></p>' +
  '<p>它会常驻系统托盘，定期检测能否上外网；发现被校园网门户拦截时，自动打开认证页、填入账号密码并点击登录，全程无需人工操作。</p>' +
  '<p>你只需要完成 4 个必填项。点「下一步」学习怎么找它们。</p>',
  '<p><b>怎么找认证页地址？</b></p>' +
  '<p>1. 在未登录校园网的状态下，用浏览器随便打开一个网页（如 example.com）</p>' +
  '<p>2. 浏览器会自动跳转到校园网认证页</p>' +
  '<p>3. 复制地址栏的<b>完整地址</b>，填入「认证页地址 login_url」</p>' +
  '<p>4. 「内网探测地址」一般填认证服务器地址（地址栏 http:// 后面的主机部分，如 10.1.1.55）</p>',
  '<p><b>登录失败时：用 F12 找元素 ID</b></p>' +
  '<p>不同学校门户的网页元素不同。在认证页按 <b>F12</b>，点击开发者工具左上角的箭头图标，再点击页面上的账号输入框，就能看到类似 <b>&lt;input id="username"&gt;</b> 的代码。</p>' +
  '<p>依次确认账号框、密码框、登录按钮、以及登录成功后页面上「注销」按钮的 id，填入「探测与高级」里即可。默认值适配大多数 Dr.COM 类门户。</p>' +
  '<p>填完 4 个必填项后点「保存并应用」就大功告成了！</p>'
];
let guideStep = 1;
let guideAutoShown = false;

function renderGuide() {
  document.getElementById('guide-body').innerHTML = GUIDE_STEPS[guideStep - 1];
  document.getElementById('guide-step').textContent = '第 ' + guideStep + ' / 3 步';
  document.getElementById('guide-next').textContent = guideStep < 3 ? '下一步' : '开始填写';
}

function guideNext() {
  if (guideStep < 3) { guideStep++; renderGuide(); }
  else { document.getElementById('guide-card').style.display = 'none'; }
}

function guidePrev() {
  if (guideStep > 1) { guideStep--; renderGuide(); }
}

function toggleGuide() {
  const card = document.getElementById('guide-card');
  const show = card.style.display === 'none';
  card.style.display = show ? 'block' : 'none';
  if (show) renderGuide();
}

loadConfig();
</script>
</body>
</html>
"""

_DASHBOARD_CSS = _BASE_CSS + """
  .wrap { max-width: 380px; padding: 14px; }
"""

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>校园网守护 - 看板</title>
<style>""" + _DASHBOARD_CSS + """
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="card-title">运行状态</div>
    <div class="status-row"><span class="dot" id="dot"></span><span id="state-text">加载中...</span></div>
    <div id="state-msg" class="muted" style="margin-top:5px"></div>
    <div class="meta muted"><span id="last-check">上次检测: -</span><span id="counts">成功 0 次 / 失败 0 次</span></div>
    <div id="check-overlay">
      <div id="check-card">
        <div id="check-spinner" class="check-spinner"></div>
        <div id="check-title"></div>
        <div id="check-detail"></div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">临时计时器</div>
    <div id="timer-line" class="timer-big inactive">未启动</div>
    <div class="timer-row">
      <span class="lbl">时长</span>
      <input type="number" id="timer-minutes" min="1" value="120"> 分钟
      <button class="btn-sm" onclick="timerStart()">开始</button>
    </div>
    <div class="timer-row">
      <span class="lbl">到点</span>
      <input type="time" id="timer-time" value="22:30">
      <button class="btn-sm" onclick="timerUntil()">到点退出</button>
    </div>
    <div class="timer-row">
      <span class="lbl">延长</span>
      <input type="number" id="timer-extend-minutes" min="1" value="30"> 分钟
      <button class="btn-sm" onclick="timerExtend()">延长</button>
      <button class="btn-sm" onclick="timerCancel()">取消</button>
    </div>
    <div id="timer-msg"></div>
  </div>

  <div class="btns">
    <button class="btn-secondary" onclick="triggerLogin()">立即检测</button>
    <button class="btn-secondary" id="pause-btn" onclick="togglePause()">暂停守护</button>
  </div>
  <div class="btns">
    <button class="btn-secondary" onclick="openSettings()">打开设置</button>
    <button class="btn-secondary" onclick="closeWindow()">关闭</button>
  </div>
  <div class="btns">
    <button class="btn-danger" onclick="requestLogout()">退出登录（成功后自动暂停守护）</button>
  </div>
  <div class="hint">自动退出失败时，会按设置页「探测与高级」中选择的兜底方式打开认证页供手动退出。</div>
</div>
<script>
function api() { return window.pywebview && window.pywebview.api; }
// 必须等到具体方法挂载完成：pywebview 注入分两步（先建空 api 对象，
// 反射遍历后才挂方法），开机繁忙时间隙可达数百毫秒，只判对象存在
// 会在间隙里放行，后续 api().get_status 报 TypeError 且轮询循环死掉
async function waitApi() { while (!api() || !api().get_status) { await new Promise(r => setTimeout(r, 200)); } }

// ---- 低占用可见性开关：窗口隐藏时全部轮询跳过，Python 侧 show/hide 调 __setUiVisible ----
window.__cnVisible = false;
function __setUiVisible(v) {
  window.__cnVisible = v;
  if (v) {
    refreshStatus().catch(() => {});
    refreshTimer().catch(() => {});
  }
}

// ---- 主题 ----
function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'light' || theme === 'dark') { root.dataset.theme = theme; }
  else { delete root.dataset.theme; }
}

async function refreshTheme() {
  if (!api()) return;
  const t = await api().get_theme();
  if (t && t.theme) { applyTheme(t.theme); }
}

const STATE_COLOR = { connected: '#10b981', logging_in: '#3b82f6', logging_out: '#3b82f6',
  portal_blocked: '#ef4444', offline: '#ef4444', paused: '#94a3b8', waiting_config: '#f59e0b' };

// ---- 手动检测反馈：全页覆盖面板 ----
// pending 态无定时器、保持到本次请求的完成到达；结果展示 3 秒后自动消失。
// checkSeqAtRequest 是 seq 哨兵：点击时已知的最新完成序号，旧检测的迟到
// 完成（seq 不大于哨兵）不会覆盖/关闭面板。
let lastManualSeq = 0;
let manualSeqReady = false;
let checkHideTimer = null;
let checkElapsedTimer = null;
let checkOverlayPending = false;
let checkSeqAtRequest = null;

function showCheckOverlay() {
  const overlay = document.getElementById('check-overlay');
  if (checkHideTimer) { clearTimeout(checkHideTimer); checkHideTimer = null; }
  if (checkElapsedTimer) { clearInterval(checkElapsedTimer); checkElapsedTimer = null; }
  const spinner = document.getElementById('check-spinner');
  if (spinner) { spinner.style.display = 'block'; }
  const title = document.getElementById('check-title');
  title.textContent = '正在检测网络状态…';
  title.style.color = '';
  document.getElementById('check-detail').textContent = '已进行 0 秒';
  overlay.style.display = 'flex';
  checkOverlayPending = true;
  const started = Date.now();
  checkElapsedTimer = setInterval(function () {
    document.getElementById('check-detail').textContent =
      '已进行 ' + Math.floor((Date.now() - started) / 1000) + ' 秒';
  }, 1000);
}

function showCheckResult(ok, text) {
  if (checkElapsedTimer) { clearInterval(checkElapsedTimer); checkElapsedTimer = null; }
  const spinner = document.getElementById('check-spinner');
  if (spinner) { spinner.style.display = 'none'; }
  const title = document.getElementById('check-title');
  title.textContent = text;
  title.style.color = ok ? 'var(--result-ok)' : '#f59e0b';
  document.getElementById('check-detail').textContent = '面板将在 3 秒后自动关闭';
  checkOverlayPending = false;
  if (checkHideTimer) { clearTimeout(checkHideTimer); checkHideTimer = null; }
  checkHideTimer = setTimeout(function () {
    document.getElementById('check-overlay').style.display = 'none';
    checkHideTimer = null;
  }, 3000);
}

function pollManualCheck(s) {
  const mc = s.manual_check;
  if (!mc) return;
  if (!manualSeqReady) {
    // 首次见到完成记录：默认作为历史基线。但面板等待中且晚于点击哨兵的
    // 记录是本次点击的结果——app 刚启动时的第一次检测没有历史基线，
    // 若一并吞掉，用户的第一次检测永远看不到结果（生产实锤过的 bug）。
    if (checkOverlayPending && checkSeqAtRequest !== null && mc.seq > checkSeqAtRequest) {
      lastManualSeq = mc.seq;
      manualSeqReady = true;
      const skipped0 = String(mc.text || '').indexOf('跳过') !== -1;
      showCheckResult(!skipped0, (mc.time ? mc.time + ' · ' : '') + (mc.text || '手动检测完成'));
      return;
    }
    lastManualSeq = mc.seq;
    manualSeqReady = true;
    return;
  }
  if (mc.seq > lastManualSeq) {
    lastManualSeq = mc.seq;
    if (mc.source !== 'dashboard') return;  // 托盘来源走系统气泡
    if (checkOverlayPending && checkSeqAtRequest !== null && mc.seq <= checkSeqAtRequest) {
      return;  // 旧检测的迟到完成：不属于本次点击，不覆盖面板
    }
    const skipped = String(mc.text || '').indexOf('跳过') !== -1;
    showCheckResult(!skipped, (mc.time ? mc.time + ' · ' : '') + (mc.text || '手动检测完成'));
  }
}

async function refreshStatus() {
  if (!api()) return;
  const s = await api().get_status();
  document.getElementById('dot').style.background = STATE_COLOR[s.state] || '#94a3b8';
  document.getElementById('state-text').textContent = s.state_text;
  document.getElementById('state-msg').textContent = s.message || '';
  document.getElementById('last-check').textContent = '上次检测: ' + s.last_check_time;
  document.getElementById('counts').textContent = '成功 ' + s.success_count + ' 次 / 失败 ' + s.fail_count + ' 次';
  document.getElementById('pause-btn').textContent = (s.state === 'paused') ? '恢复守护' : '暂停守护';
  pollManualCheck(s);
}

async function refreshTimer() {
  if (!api()) return;
  const t = await api().get_timer();
  const line = document.getElementById('timer-line');
  line.textContent = t.active ? ('剩余 ' + t.remaining_text) : '未启动';
  line.className = t.active ? 'timer-big' : 'timer-big inactive';
}

function showTimerMsg(text) {
  const box = document.getElementById('timer-msg');
  box.textContent = text || '';
  if (text) setTimeout(() => { box.textContent = ''; }, 3000);
}

async function timerStart() {
  const m = parseInt(document.getElementById('timer-minutes').value, 10);
  const res = await api().timer_start(m);
  showTimerMsg(res.ok ? '' : res.problem);
  refreshTimer();
}

async function timerUntil() {
  const v = document.getElementById('timer-time').value;
  const res = await api().timer_until(v);
  showTimerMsg(res.ok ? '' : res.problem);
  refreshTimer();
}

async function timerExtend() {
  const m = parseInt(document.getElementById('timer-extend-minutes').value, 10);
  const res = await api().timer_extend(m);
  showTimerMsg(res.ok ? '' : res.problem);
  refreshTimer();
}

async function timerCancel() {
  await api().timer_cancel();
  showTimerMsg('计时已取消');
  refreshTimer();
}

async function triggerLogin() {
  showCheckOverlay();
  try {
    await waitApi();
    // 取点击时刻的精确完成哨兵：面板只认晚于该序号的完成，历史不干扰
    const snapshot = await api().get_status();
    const seq = snapshot.manual_check ? snapshot.manual_check.seq : 0;
    checkSeqAtRequest = seq;
    if (seq > lastManualSeq) { lastManualSeq = seq; }
    manualSeqReady = manualSeqReady || !!snapshot.manual_check;
    await api().trigger_login();
  } catch (e) {
    showCheckResult(false, '触发失败：' + e);
  }
}
async function togglePause() { await api().toggle_pause(); }
async function openSettings() { await api().open_settings(); }
async function requestLogout() { await api().request_logout(); }
function closeWindow() { api().close_dashboard(); }

async function start() {
  await waitApi();
  // 轮询先注册、单次失败不中断；隐藏时跳过（低占用，重见时 __setUiVisible 即刷一轮）
  setInterval(() => { if (window.__cnVisible === false) return; refreshStatus().catch(() => {}); }, 1000);
  setInterval(() => { if (window.__cnVisible === false) return; refreshTimer().catch(() => {}); }, 1000);
  setInterval(() => { if (window.__cnVisible === false) return; refreshTheme().catch(() => {}); }, 1000);
  refreshStatus().catch(() => {});
  refreshTimer().catch(() => {});
  refreshTheme().catch(() => {});
}
start();
</script>
</body>
</html>
"""


def _fill(template):
    return template.replace('__VERSION__', __version__)


def settings_html():
    return _fill(_SETTINGS_HTML)


def dashboard_html():
    return _fill(_DASHBOARD_HTML)
