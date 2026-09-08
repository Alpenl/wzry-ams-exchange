'use strict';
const $ = id => document.getElementById(id);
let state = {rewards: {}, schedule: {runs: []}, account: {}, logs: []};
let authMode = 'login';
let currentView = 'overview';
let toastTimer;
const labels = {redeemed:'兑换成功', already_satisfied:'已领取', authentication_failed:'登录失效', transient_failure:'等待重试', protocol_failure:'接口异常', rejected:'未满足条件', invalid_reward:'无效奖励', running:'执行中'};
const viewTitles = {overview:['概览','每日奖励，一目了然。','王者荣耀体验服 / 奖励与自动兑换'],rewards:['奖励兑换','发现你的下一份奖励。','活动奖励 / 体验券兑换'],history:['任务记录','每一次执行，都有记录。','自动任务与手动兑换'],credentials:['凭据管理','连接你的游戏账号。','QQ 登录 / 活动身份'],settings:['设置','让奖励按时到来。','每日计划 / 自动执行']};
function icons() { if (window.lucide) window.lucide.createIcons(); }
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,4500); }
async function api(path, options={}) {
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  const data=await response.json();
  if (!response.ok) { if(data.auth_required) await checkAuth(); throw new Error(data.msg || data.detail || '请求失败'); }
  return data;
}
function go(view) {
  if(!viewTitles[view]) view='overview'; currentView=view;
  for(const element of document.querySelectorAll('.view')) element.hidden=element.id!=='view-'+view;
  for(const button of document.querySelectorAll('[data-view]')) button.classList.toggle('active',button.dataset.view===view);
  [$('breadcrumb').textContent,$('page-title').textContent,$('page-description').textContent]=viewTitles[view];
  if(location.hash!=='#'+view) history.replaceState(null,'','#'+view);
}
async function checkAuth() {
  const auth=await api('/api/auth/status');
  $('app-shell').hidden=!auth.authenticated; $('auth-screen').hidden=auth.authenticated;
  $('logout').hidden=!auth.enabled;
  $('logout-top').hidden=!auth.enabled;
  if(!auth.authenticated) {
    authMode=auth.configured?'login':'setup';
    $('auth-title').textContent=auth.configured?'欢迎回来':'创建你的管理台';
    $('auth-subtitle').textContent=auth.configured?'登录兑换管理台':'设置管理员密码';
    $('auth-submit').textContent=auth.configured?'登录':'创建管理员';
    $('auth-password').minLength=auth.configured?1:12;
    $('auth-password').autocomplete=auth.configured?'current-password':'new-password';
    $('auth-password').placeholder=auth.configured?'输入管理员密码':'至少 12 位';
    $('confirm-label').hidden=auth.configured; $('auth-confirm').required=!auth.configured;
  }
  return auth.authenticated;
}
function localDate() { return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date()); }
async function refresh() {
  const [account,schedule,catalog,logs]=await Promise.all([api('/api/status'),api('/api/schedule'),api('/api/rewards'),api('/api/log')]);
  state={account,schedule,rewards:catalog.rewards,iconBase:catalog.icon_base,logs:logs.logs}; render();
}
function render() {
  const {account:a,schedule:s}=state;
  $('today').textContent=new Intl.DateTimeFormat('zh-CN',{month:'long',day:'numeric',weekday:'short',timeZone:'Asia/Shanghai'}).format(new Date());
  $('balance').textContent=a.exp_voucher==='?'?'—':Number(a.exp_voucher).toLocaleString();
  const today=s.runs.filter(r=>r.day===localDate());
  const done=new Set(today.filter(r=>r.result.satisfied).map(r=>r.reward)).size;
  $('completed').textContent=done;
  $('completion-note').textContent=done===2?'今日奖励已就绪':today.length?'任务已执行，查看记录':'等待任务执行';
  $('daily-time').textContent=s.enabled?(s.time || '09:17'):'已暂停';
  $('timezone').textContent=s.timezone || 'Asia/Shanghai';
  $('credential-state').textContent=a.exchange_ready?'已配置':a.has_cookie?'待补全':'未配置';
  $('credential-state').style.color=a.exchange_ready?'var(--green)':'#9b8472';
  $('account-id').textContent=a.openid || '尚未关联账号';
  $('credential-detail').textContent=a.openid || '未关联账号';
  $('partition').textContent=a.has_cookie?a.area+' / '+a.partition:'—';
  $('credential-check').textContent=a.exchange_ready?'完整':a.has_cookie?'不完整':'未配置';
  $('connection').textContent=s.enabled?'自动任务已开启':'自动任务已暂停';
  $('connection').className='badge '+(s.enabled?'success':'neutral');
  const authFailed=today.some(r=>r.result.kind==='authentication_failed');
  $('credential-banner').hidden=a.exchange_ready&&!s.error&&!authFailed;
  $('banner-title').textContent=authFailed?'登录凭据已失效':a.has_cookie?'账户凭据待补全':'连接你的游戏账号';
  $('banner-detail').textContent=authFailed?'更新后继续执行每日任务':s.error || '账户凭据尚未配置';
  if(currentView!=='settings' || document.activeElement===document.body) {
    $('schedule-enabled').checked=!!s.enabled; $('schedule-time').value=s.time || '09:17';
  }
  $('schedule-error').textContent=s.available===false?'容器未启用调度器':s.error || '';
  $('schedule-form').querySelector('button').disabled=s.available===false;
  renderRewards('daily-rewards',['3','4']);renderRewards('reward-grid',Object.keys(state.rewards));renderHistory();icons();
}
function renderRewards(target, ids) {
  const root=$(target);root.replaceChildren();
  for(const id of ids) {
    const r=state.rewards[id];if(!r)continue;
    const card=document.createElement('article');card.className='reward-card';
    const daily=['3','4'].includes(id);
    card.innerHTML='<div class="reward-art '+(id==='4'?'pink':'')+'"><span class="reward-label"><i data-lucide="'+(daily?'calendar-check':'gift')+'"></i>'+(daily?'每日计划':'活动奖励')+'</span></div><div class="reward-body"><div class="reward-title"><h3></h3><span class="badge neutral">'+(daily?'自动兑换':'手动兑换')+'</span></div><div class="reward-cost"><strong></strong>体验券</div><div class="reward-bottom"><span>'+(daily?'每日 1 次 · 活动规则为准':'活动规则为准')+'</span><button class="secondary"><i data-lucide="arrow-up-right"></i>立即兑换</button></div></div>';
    card.querySelector('h3').textContent=r.name;card.querySelector('strong').textContent=r.cost;
    const img=document.createElement('img');img.src='/static/rewards/'+r.icon;img.alt=r.name;img.width=80;img.height=80;card.querySelector('.reward-art').appendChild(img);
    const button=card.querySelector('button');button.addEventListener('click',()=>exchange(id,button));root.appendChild(card);
  }
}
function entries() {
  return [...state.schedule.runs.map(r=>({date:r.day,name:r.result.reward || state.rewards[r.reward]?.name || r.reward,source:'自动任务',status:labels[r.result.kind]||r.result.kind,ok:r.result.satisfied,message:r.result.message || '—'})),...state.logs.map(l=>({date:l.time,name:l.name,source:'手动兑换',status:l.status==='OK'?'已完成':'未完成',ok:l.status==='OK',message:l.msg}))].sort((a,b)=>b.date.localeCompare(a.date));
}
function table(target, rows, compact=false) {
  const root=$(target);root.replaceChildren();
  if(!rows.length) { root.innerHTML='<div class="empty"><i data-lucide="inbox"></i><span>暂无执行记录</span></div>';return; }
  const table=document.createElement('table');table.innerHTML='<thead><tr><th>奖励</th><th>执行时间</th><th>来源</th><th>结果</th>'+(compact?'':'<th>详情</th>')+'</tr></thead><tbody></tbody>';
  for(const row of rows) {
    const tr=document.createElement('tr');
    for(const value of [row.name,row.date,row.source]) {const td=document.createElement('td');td.textContent=value;tr.appendChild(td);}
    const status=document.createElement('td');const badge=document.createElement('span');badge.className='badge '+(row.ok?'success':'warning');badge.textContent=row.status;status.appendChild(badge);tr.appendChild(status);
    if(!compact) {const td=document.createElement('td');td.textContent=row.message;tr.appendChild(td);}
    table.querySelector('tbody').appendChild(tr);
  }root.appendChild(table);
}
function renderHistory() {const rows=entries();table('recent-runs',rows.slice(0,4),true);const filter=$('history-filter').value;table('history-table',rows.filter(r=>filter==='all'||r.source===(filter==='auto'?'自动任务':'手动兑换')));icons();}
function confirmAction(title,message) {return new Promise(resolve=>{const d=$('confirm-dialog');$('confirm-title').textContent=title;$('confirm-text').textContent=message;let accepted=false;$('confirm-ok').onclick=()=>{accepted=true;d.close();};$('confirm-cancel').onclick=()=>d.close();d.onclose=()=>resolve(accepted);d.showModal();});}
async function exchange(id, btn) {
  if(!state.account.exchange_ready){go('credentials');toast('请先配置完整凭据');return;}
  const r=state.rewards[id];if(!await confirmAction('兑换'+r.name,'本次兑换将消耗 '+r.cost+' 体验券。'))return;
  btn.disabled=true;
  try{const d=await api('/api/exchange',{method:'POST',body:JSON.stringify({reward:id})});toast(d.msg);await refresh();}catch(e){toast(e.message);}finally{btn.disabled=false;}
}
async function init() {
  icons();go(location.hash.slice(1));
  for(const b of document.querySelectorAll('[data-view]')) b.onclick=()=>go(b.dataset.view);
  for(const b of document.querySelectorAll('[data-go]')) b.onclick=()=>go(b.dataset.go);
  window.addEventListener('hashchange',()=>go(location.hash.slice(1)));
  $('auth-form').onsubmit=async e=>{e.preventDefault();$('auth-error').textContent='';const password=$('auth-password').value;if(authMode==='setup'&&password!==$('auth-confirm').value){$('auth-error').textContent='两次密码不一致';return;}$('auth-submit').disabled=true;try{await api('/api/auth/'+authMode,{method:'POST',body:JSON.stringify({password})});$('auth-form').reset();if(await checkAuth())await refresh();}catch(error){$('auth-error').textContent=error.message;}finally{$('auth-submit').disabled=false;}};
  $('logout').onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'});await checkAuth();}catch(e){toast(e.message);}};
  $('logout-top').onclick=$('logout').onclick;
  $('refresh').onclick=()=>refresh().catch(e=>toast(e.message));
  $('history-filter').onchange=renderHistory;
  $('credential-file').onchange=async e=>{const f=e.target.files[0];if(f)$('cookie-input').value=await f.text();};
  $('credential-form').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{const d=await api('/api/cookies',{method:'POST',body:JSON.stringify({raw:$('cookie-input').value})});$('cookie-input').value='';$('credential-file').value='';toast(d.exchange_ready?'凭据已更新':'凭据已保存，兑换字段仍需补全');await refresh();}catch(error){toast(error.message);}finally{button.disabled=false;}};
  $('clear-cookie').onclick=async()=>{if(await confirmAction('清除账户凭据','自动兑换将等待新的账户凭据。'))try{await api('/api/cookies',{method:'DELETE'});await refresh();toast('凭据已清除');}catch(e){toast(e.message);}};
  $('schedule-form').onsubmit=async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{await api('/api/schedule',{method:'PUT',body:JSON.stringify({enabled:$('schedule-enabled').checked,time:$('schedule-time').value})});toast('设置已保存');await refresh();}catch(error){toast(error.message);}finally{b.disabled=state.schedule.available===false;}};
  try{if(await checkAuth())await refresh();}catch(error){toast(error.message);}
  setInterval(()=>{if(!$('app-shell').hidden&&!['settings','credentials'].includes(currentView))refresh().catch(e=>toast(e.message));},30000);
}
document.addEventListener('DOMContentLoaded',init);
