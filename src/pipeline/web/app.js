const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names = {QUEUED:'已入队',PENDING:'等待依赖',READY:'就绪',RUNNING:'执行中',WAITING_RESOURCE:'等待 GPU',RETRY_WAIT:'等待重试',SUCCEEDED:'已完成',CACHED:'已复用',FAILED:'失败',BLOCKED:'上游阻塞',INTERRUPTED:'进程中断',TIMED_OUT:'执行超时',CANCELLING:'取消中',CANCELLED:'已取消'};
const eventNames = {'task.cancel_requested':'请求取消','task.cancelled':'任务已取消','step.cancelled':'步骤已取消','attempt.cancelled':'执行已停止','attempt.cancel_unconfirmed':'执行停止未确认','task.archived':'任务已归档','task.unarchived':'恢复到任务列表','task.created':'任务已创建','task.succeeded':'任务已完成','task.failed':'任务失败','attempt.started':'开始执行','attempt.succeeded':'执行完成','attempt.failed':'本次失败','attempt.interrupted':'进程中断','attempt.timed_out':'执行超时','checkpoint.saved':'保存检查点','checkpoint.invalidated':'检查点失效','step.cached':'复用结果','step.ready':'依赖已就绪','step.blocked':'等待上游修复','step.manual_retry':'手动重试','step.waiting_resource':'等待 GPU 槽','step.unschedulable':'资源不足','cache.invalidated':'缓存失效','attempt.stale_commit_rejected':'拒绝过期提交','step.deadline_exceeded':'超过任务时限'};
let selected = localStorage.getItem('frameflow-task'), selectedStep = 'stylize', currentTask = null, items = [], busy = false, lastEvents = [], lastResources = null;
let pageCount=1, nextCursor=null, filterVersion=0, refreshQueued=false, parametersOpen=false, cancelTarget=null;
const terminal = status => ['SUCCEEDED','FAILED','CANCELLED'].includes(status);
const dateTime = value => value ? new Date(value).toLocaleString('zh-CN',{hour12:false}) : '—';
const badge = s => `<span class="badge ${escapeHTML(s)}">${escapeHTML(names[s] || s)}</span>`;
const time = value => value ? new Date(value).toLocaleTimeString('zh-CN',{hour12:false}) : '—';
const seconds = value => Number(value || 0).toFixed(1) + 's';
function toast(message){ $('#toast').textContent=message; clearTimeout(window.toastTimer); window.toastTimer=setTimeout(()=>$('#toast').textContent='',5000); }
async function api(path, options={}){ const r=await fetch(path,{signal:AbortSignal.timeout(10000),...options}); if(!r.ok){ let b;try{b=await r.json()}catch{b={detail:r.statusText}} const error=Error(typeof b.detail==='string'?b.detail:JSON.stringify(b.detail));error.status=r.status;throw error; } return r.json(); }
document.getElementById('app').innerHTML=`
<div class="app-shell"><aside class="sidebar"><div class="brand"><span class="brand-mark">▧</span><div>Frameflow<small>动画任务观察台</small></div></div>
<div class="side-title">工作空间 / 本地开发</div><div class="side-heading">任务中心 <span id="task-count">0</span></div>
<form id="history-filters" class="history-filters" role="search">
<label for="task-search">查找任务</label><input id="task-search" type="search" placeholder="名称或任务 ID" maxlength="100">
<div class="filter-row"><select id="status-filter" aria-label="任务状态"><option value="">全部状态</option><option value="QUEUED">已入队</option><option value="RUNNING">执行中</option><option value="CANCELLING">取消中</option><option value="CANCELLED">已取消</option><option value="SUCCEEDED">已完成</option><option value="FAILED">失败</option></select>
<select id="archive-filter" aria-label="归档范围"><option value="active">未归档</option><option value="archived">已归档</option><option value="all">全部任务</option></select></div></form><nav class="task-list" id="task-list" aria-label="任务列表"></nav><button id="load-more" class="load-more" hidden>加载更早任务</button>
<div class="side-footer"><b>● PostgreSQL 持久化</b><br>步骤重试 · 批次恢复 · 结果复用<br><a href="/docs" target="_blank" rel="noopener">查看 API 文档</a></div></aside>
<main class="workspace"><header class="topbar"><span>工作空间 <span aria-hidden="true"> / </span> <strong>动画处理管线</strong></span><span class="connection" id="connection"><i class="dot"></i>正在连接</span></header>
<div class="content"><div class="page-heading"><div><h1>每一步，都有迹可循</h1><p class="description">观察任务、定位失败，从已完成的工作继续。</p></div><div class="actions"><button id="interrupt-help">中断恢复演示</button><button id="open-create" class="primary">＋ 新建任务</button></div></div>
<div id="task-content"></div><div class="footer-note"><span>计算使用 MOCK · 状态、检查点与文件真实持久化</span><span>每 1.2 秒更新 · <a href="/metrics" target="_blank" rel="noopener">查看指标</a></span></div></div></main></div>
<dialog id="create-dialog"><form id="create-form"><div class="dialog-title"><h2>新建动画处理任务</h2><button type="button" id="close-create" aria-label="关闭">×</button></div>
<div class="form-body"><label class="field full">任务名称<input name="title" value="Orbit / 动画素材" maxlength="80" required></label>
<label class="field">演示素材<select name="asset"><option value="orbit">轨道运动 / Orbit</option><option value="bounce">弹跳运动 / Bounce</option><option value="invalid">无效素材（永久失败）</option></select></label>
<label class="field">帧数<select name="frames"><option>16</option><option>32</option><option>64</option></select></label>
<label class="field">模型版本<input name="model_version" value="mock-v1" maxlength="32" required></label><label class="field">随机种子<input name="seed" type="number" value="42" min="0" max="65535" required></label>
<label class="field">每批模拟耗时<select name="step_delay"><option value="1.2">1.2 秒 / 标准演示</option><option value="4">4 秒 / 中断恢复</option><option value="0.1">0.1 秒 / 快速验收</option></select></label>
<label class="field">故障预设<select name="fault_mode"><option value="none">无故障</option><option value="transient_once">首次失败，自动重试</option><option value="exhaust_once">本轮全部失败，手动恢复</option><option value="permanent">永久失败</option><option value="timeout_once">首次卡住，超时恢复</option></select></label>
<label class="check-field full"><input type="checkbox" name="cache_enabled" checked>允许复用相同输入与版本的结果</label>
<div class="form-note full">风格化按每 4 帧保存检查点。故障预设会关闭缓存，便于观察真实执行过程。GPU 为单槽模拟资源。</div></div>
<div class="dialog-actions"><button type="button" id="cancel-create">取消</button><button class="primary" type="submit" id="submit-create">创建并执行</button></div></form></dialog>
<dialog id="help-dialog"><div class="dialog-title"><h2>验证真正的进程中断</h2><button id="close-help" aria-label="关闭">×</button></div><div class="form-body"><div class="form-note full">创建一个 32 帧、每批 4 秒且关闭缓存的任务。待风格化步骤保存检查点后，在项目目录运行：<p><code>.venv/bin/python scripts/dev.py interrupt gpu</code></p>本地启动器会对自己的两个 GPU Worker 发送 SIGKILL 并重启它们。约 10–15 秒后，新的执行尝试从检查点继续。右侧资源面板可看到新 Worker。<p>适用于 <code>scripts/dev.py start</code> 启动方式。界面不会提供任意进程终止接口。</p></div></div></dialog>
<dialog id="cancel-dialog"><div class="dialog-title"><h2>取消这个任务？</h2><button id="close-cancel" aria-label="关闭">×</button></div><div class="form-body"><p class="form-note full" id="cancel-description"></p><p class="form-note full">后续步骤将停止领取，运行中的步骤会在安全边界停止。已完成的产物和执行历史会保留。取消后如需再运行，可以用相同参数新建任务。</p></div><div class="dialog-actions"><button id="keep-task">继续执行任务</button><button id="confirm-cancel" class="danger">确认取消</button></div></dialog>
<div class="toast" id="toast" role="status" aria-live="polite"></div>`;
$('#open-create').onclick=()=>$('#create-dialog').showModal();
$('#close-create').onclick=$('#cancel-create').onclick=()=>$('#create-dialog').close();
$('#interrupt-help').onclick=()=>$('#help-dialog').showModal();$('#close-help').onclick=()=>$('#help-dialog').close();
$('#create-form').elements.fault_mode.onchange=e=>{if(e.target.value!=='none')$('#create-form').elements.cache_enabled.checked=false;};
$('#create-form').onsubmit=async e=>{e.preventDefault();const f=e.target;const p=Object.fromEntries(new FormData(f));p.frames=Number(p.frames);p.seed=Number(p.seed);p.step_delay=Number(p.step_delay);p.cache_enabled=f.elements.cache_enabled.checked&&p.fault_mode==='none';if(p.fault_mode==='timeout_once')p.attempt_timeout=15;
$('#submit-create').disabled=true;try{const created=await api('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},body:JSON.stringify(p)});selectTask(created.task_id);$('#create-dialog').close();toast('任务已创建，等待 Worker 领取');await refresh(true);}catch(e){toast(e.message)}finally{$('#submit-create').disabled=false}};
function selectTask(id){selected=id;currentTask=null;lastEvents=[];localStorage.setItem('frameflow-task',id);}
$('#task-list').onclick=e=>{const button=e.target.closest('[data-task]');if(button){selectTask(button.dataset.task);refresh(true)}};
$('#task-content').onclick=async e=>{const cancel=e.target.closest('[data-cancel]');if(cancel){cancelTarget=cancel.dataset.cancel;$('#cancel-description').textContent=currentTask.params.title+' · '+cancelTarget;$('#cancel-dialog').showModal();return}const archive=e.target.closest('[data-archive]');if(archive){archive.disabled=true;try{const archived=archive.dataset.archived!=='true';await api('/api/tasks/'+archive.dataset.archive+'/archive?archived='+archived,{method:'POST'});toast(archived?'已归档，历史和文件仍保留':'已恢复到任务列表');await refresh(true)}catch(error){toast(error.message)}return}const node=e.target.closest('[data-step]');if(node){selectedStep=node.dataset.step;renderTask(currentTask);renderEvents(lastEvents);if(lastResources)renderResources(lastResources);return}const retry=e.target.closest('[data-retry]');if(retry){retry.disabled=true;try{await api('/api/steps/'+retry.dataset.retry+'/retry',{method:'POST'});toast('已安排新一轮尝试，历史记录保留');currentTask=null;await refresh(true)}catch(error){toast(error.message);retry.disabled=false}}const clone=e.target.closest('[data-clone]');if(clone&&currentTask){const p={...currentTask.params,title:currentTask.params.title+' / 复用',cache_enabled:true,fault_mode:'none'};try{const r=await api('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},body:JSON.stringify(p)});selectTask(r.task_id);await refresh(true)}catch(error){toast(error.message)}}};
function renderList(){ $('#task-count').textContent=items.length;$('#task-list').innerHTML=items.map(t=>`<button class="task-item ${t.id===selected?'selected':''}" data-task="${escapeHTML(t.id)}"><strong>${t.archived_at?'▣ ':''}${escapeHTML(t.title)}</strong><small><span>${escapeHTML(names[t.status])}</span><span>${dateTime(t.created_at)}</span></small></button>`).join('') || '<div class="no-data">没有符合条件的任务<br>可调整搜索或筛选条件</div>'; }
const parameterNames={title:'任务名称',asset:'演示素材',frames:'总帧数',seed:'随机种子',model_version:'模型版本',step_delay:'步骤模拟耗时（秒）',batch_delay:'每批模拟耗时（秒）',cache_enabled:'允许结果复用',fault_step:'故障步骤',fault_mode:'故障模式',max_attempts:'每轮最多尝试',attempt_timeout:'单次执行时限（秒）',task_timeout:'任务总体时限（秒）',gpu_memory_mb:'模拟显存需求（MiB）'};
function renderTask(t){if(!t){$('#task-content').innerHTML='<section class="panel empty-state"><div class="empty-icon">▧</div><h1>让任务开始运行</h1><p class="description">从一个动画素材开始，观察六个处理步骤。你可以注入失败、终止 Worker，再从保存的进度继续。</p><button class="primary" onclick="document.getElementById(\'create-dialog\').showModal()">创建第一个任务</button></section>';return;}
const done=t.steps.filter(s=>['SUCCEEDED','CACHED'].includes(s.status)).length,cached=t.steps.filter(s=>s.status==='CACHED').length,attempts=t.steps.flatMap(s=>s.attempts),failed=attempts.filter(a=>['FAILED','INTERRUPTED','TIMED_OUT'].includes(a.status)).length;
const selectedNode=t.steps.find(s=>s.key===selectedStep)||t.steps[0];const active=selectedNode.attempts.at(-1);const gif=t.steps.find(s=>s.key==='compose')?.artifact;const bundle=t.steps.find(s=>s.key==='package')?.artifact;
$('#task-content').innerHTML=`<section class="task-toolbar"><div><strong>${escapeHTML(t.params.title)}</strong><small>${escapeHTML(t.id)} · ${dateTime(t.created_at)}${t.archived_at?' · 已归档':''}</small></div><div class="actions">${!terminal(t.status)?`<button data-cancel="${t.id}" class="danger" ${t.status==='CANCELLING'?'disabled':''}>${t.status==='CANCELLING'?'正在取消…':'取消任务'}</button>`:`<button data-archive="${t.id}" data-archived="${!!t.archived_at}">${t.archived_at?'恢复到列表':'归档任务'}</button>`}<button data-clone="true">用相同参数新建</button></div></section>
${t.status==='CANCELLING'?'<div class="state-notice">取消请求已保存，正在等待运行步骤停止。停止确认前保留运行资源。</div>':''}
${t.cancel_unconfirmed?'<div class="error-box">已停止调度并拒绝后续结果提交。取消期间 Worker 失联，无法确认原计算是否已经停止；本项目只释放模拟 GPU 槽。</div>':''}
<details class="parameter-panel" id="task-parameters" ${parametersOpen?'open':''}><summary>任务参数与执行时限</summary><dl>${Object.entries(t.params).map(([key,value])=>`<div><dt>${escapeHTML(parameterNames[key]||key)}</dt><dd>${escapeHTML(value===null?'沿用步骤耗时':value===true?'是':value===false?'否':value)}</dd></div>`).join('')}<div><dt>总体截止时间</dt><dd>${dateTime(t.deadline)}</dd></div>${t.cancel_requested_at?`<div><dt>取消请求时间</dt><dd>${dateTime(t.cancel_requested_at)}</dd></div>`:''}${t.cancelled_at?`<div><dt>取消结束时间</dt><dd>${dateTime(t.cancelled_at)}</dd></div>`:''}</dl></details>
<section class="overview" aria-label="任务概览"><div class="metric"><label>当前任务状态</label>${badge(t.status)}<small>${escapeHTML(t.params.title)}</small></div><div class="metric"><label>完成步骤</label><strong>${done}<span style="color:#a6b1c0;font-size:16px"> / 6</span></strong><small>批次与步骤综合进度 ${Math.round(t.progress*100)}%</small></div><div class="metric"><label>结果复用</label><strong>${cached}<span style="font-size:12px;font-weight:400;margin-left:6px">个步骤</span></strong><small>输入与版本一致时命中</small></div><div class="metric"><label>执行尝试</label><strong>${attempts.length}<span style="font-size:12px;font-weight:400;margin-left:6px">次</span></strong><small>${failed} 次失败 / 中断 / 超时</small></div></section>
<section class="panel"><div class="panel-header"><h2>处理管线 <span style="font-size:10px;color:#8d9bb0;font-weight:400;margin-left:9px">animation-v1</span></h2><small>点击步骤，查看执行明细</small></div><div class="pipeline-scroll"><div class="pipeline"><svg class="edges" viewBox="0 0 1000 218" preserveAspectRatio="none" aria-hidden="true"><defs><marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" style="fill:#b4c5db;stroke:none"/></marker></defs><path d="M185 108 H245 M375 108 H408 V60 H450 M408 108 V154 H450 M580 60 H650 M775 60 H804 V108 H850 M580 154 H804 V108" marker-end="url(#arrow)"/></svg>${t.steps.map(s=>`<button class="node node-${s.key} ${s.key===selectedNode.key?'selected':''}" data-step="${s.key}" aria-pressed="${s.key===selectedNode.key}"><strong>${escapeHTML(s.label)}<em>${s.resource==='gpu'?'GPU MOCK':'CPU'}</em></strong>${badge(s.status)}<span class="progress-track"><i style="width:${s.progress*100}%"></i></span></button>`).join('')}</div></div><div class="pipeline-footer"><span class="legend"><span><i></i>执行中</span><span><i class="success"></i>完成 / 复用</span><span><i class="waiting"></i>重试 / 等待</span></span><span>持久化状态 · 依赖就绪后领取</span></div></section>
<div class="lower-grid"><section class="panel"><div class="panel-header"><h2>${escapeHTML(selectedNode.label)} · 执行明细</h2>${badge(selectedNode.status)}</div><div class="step-body">${selectedNode.error?`<div class="error-box">${escapeHTML(selectedNode.error)}</div>`:''}<div class="step-summary"><div><small>处理进度</small><strong>${Math.round(selectedNode.progress*100)}%${selectedNode.checkpoint.frames?' / '+selectedNode.checkpoint.frames.length+' 帧已保存':''}</strong></div><div><small>最近心跳</small><strong>${time(active?.heartbeat_at)}</strong></div><div><small>执行资源</small><strong>${selectedNode.resource==='gpu'?'GPU mock / 单槽':'CPU Worker'}</strong></div></div>
${selectedNode.attempts.length?`<div class="table-scroll"><table><thead><tr><th>轮次 / 尝试</th><th>状态</th><th>耗时</th><th>恢复起点</th><th>Worker</th></tr></thead><tbody>${selectedNode.attempts.map(a=>`<tr><td>${a.retry_round+1} / ${a.attempt_no}</td><td>${badge(a.status)}</td><td>${seconds(((a.finished_at?new Date(a.finished_at):new Date())-new Date(a.started_at))/1000)}</td><td>${a.resume_from} 帧</td><td><code title="${escapeHTML(a.error||a.worker_id)}">${escapeHTML(a.worker_id.slice(0,18))}</code></td></tr>`).join('')}</tbody></table></div>`:`<div class="no-data">${selectedNode.status==='CACHED'?'已复用校验通过的产物，无需创建计算尝试。':'领取步骤后，这里会记录每次尝试。'}</div>`}<div class="detail-footer">${selectedNode.status==='FAILED'&&!t.archived_at&&!['CANCELLING','CANCELLED'].includes(t.status)?`<button data-retry="${selectedNode.id}">重试这个步骤</button>`:''}${selectedNode.artifact?`<a href="/api/artifacts/${selectedNode.artifact.id}?download=true">下载步骤产物</a>`:''}<span class="details-id">${escapeHTML(selectedNode.id.slice(0,12))}</span></div></div></section>
<section class="panel"><div class="panel-header"><h2>计算资源</h2><span class="mock-tag">模拟 GPU · 非真实遥测</span></div><div class="resource-body" id="resource-body"></div></section></div>
${gif?`<section class="panel" style="margin-top:20px;padding:0 21px 18px"><div class="preview"><img src="/api/artifacts/${gif.id}" alt="已生成的演示动画"><div><h3 style="margin-bottom:6px">动画产物已就绪</h3><p>包含真实 GIF、缩略图与摘要清单，可下载核对。</p>${bundle?`<a href="/api/artifacts/${bundle.id}?download=true">下载交付包</a>　`:''}<button data-clone="true" style="padding:5px 9px;font-size:10px">相同输入再次运行</button></div></div></section>`:''}
<section class="panel timeline-panel"><div class="panel-header"><h2>任务事件</h2><small>事务内记录 · 最近 30 条</small></div><div class="timeline" id="timeline"></div></section>`;
}
function renderResources(r){const el=$('#resource-body');if(!el)return;const slot=r.slots[0],busy=!!slot?.attempt_id;const online=r.workers.filter(w=>w.online);el.innerHTML=`<div><div class="resource-title"><strong>gpu-mock-0</strong><span class="badge ${busy?'RUNNING':'CACHED'}">${busy?'已占用':'空闲'}</span></div><div class="resource-bar"><i style="width:${busy?100:0}%"></i></div><div class="resource-copy"><span>${busy?'1':'0'} / 1 执行槽</span><span>容量 ${(slot?.capacity_mb||8192)/1024} GiB（模拟）</span></div><p class="description" style="margin-top:12px">${r.waiting.length?'排队 '+r.waiting.length+' 个任务，最长等待 '+seconds(Math.max(...r.waiting.map(w=>w.waiting_seconds))):'没有任务在等待 GPU 资源'}</p></div><div class="workers"><h3>在线 Worker <span style="color:#8393a8;font-weight:400">${online.length}</span></h3>${r.workers.slice(0,4).map(w=>`<div class="worker"><code>${escapeHTML(w.id)}</code><span><i class="dot ${w.online?'':'offline'}"></i> ${w.online?'在线':'心跳过期'}</span></div>`).join('')||'<div class="description">尚无 Worker，请启动本地服务</div>'}</div>`;}
function renderEvents(events){const el=$('#timeline');if(!el)return;el.innerHTML=events.slice(-30).reverse().map(e=>`<div class="event"><time>${time(e.created_at)}</time><span class="event-name">${escapeHTML(eventNames[e.type]||e.type)}</span><span class="event-meta">${escapeHTML(e.step_key||'task')}${e.payload.completed_frames!==undefined?' / 已保存 '+e.payload.completed_frames+' 帧':''}${e.payload.worker_id?' / '+escapeHTML(e.payload.worker_id):''}${e.payload.error?' / '+escapeHTML(e.payload.error):''}${e.payload.will_retry?' / 将自动重试':''}</span></div>`).join('');}

$('#task-content').addEventListener('toggle', e=>{if(e.target.id==='task-parameters')parametersOpen=e.target.open;},true);
$('#close-cancel').onclick=$('#keep-task').onclick=()=>$('#cancel-dialog').close();
$('#confirm-cancel').onclick=async()=>{
  const button=$('#confirm-cancel');button.disabled=true;
  try{const result=await api('/api/tasks/'+cancelTarget+'/cancel',{method:'POST'});$('#cancel-dialog').close();toast(result.status==='CANCELLED'?'任务已取消':'已请求取消，等待执行停止');await refresh(true)}catch(e){toast(e.message)}finally{button.disabled=false}
};
let searchTimer;
function resetFilters(){pageCount=1;filterVersion++;refresh(true)}
$('#history-filters').onsubmit=e=>{e.preventDefault();clearTimeout(searchTimer);resetFilters()};
$('#task-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(resetFilters,250)};
$('#status-filter').onchange=$('#archive-filter').onchange=resetFilters;
$('#load-more').onclick=()=>{if(nextCursor){pageCount++;refresh(true)}};

async function loadList(){
  const params=new URLSearchParams({limit:'30',q:$('#task-search').value,view:$('#archive-filter').value});
  if($('#status-filter').value)params.set('status',$('#status-filter').value);
  let rows=[],cursor=null;
  for(let page=0;page<pageCount;page++){
    if(cursor)params.set('before',cursor);
    const result=await api('/api/tasks?'+params);
    rows.push(...result.items);cursor=result.next_cursor;
    if(!cursor)break;
  }
  return {items:[...new Map(rows.map(t=>[t.id,t])).values()],next_cursor:cursor};
}
async function refresh(force=false){
  if(busy){if(force)refreshQueued=true;return}
  busy=true;
  const version=filterVersion;
  try{
    const [list,resources]=await Promise.all([loadList(),api('/api/resources')]);
    if(version!==filterVersion){refreshQueued=true;return}
    items=list.items;nextCursor=list.next_cursor;lastResources=resources;
    if(!selected&&items.length)selectTask(items[0].id);
    renderList();$('#load-more').hidden=!nextCursor;
    if(selected){
      const requested=selected;
      const listed=items.find(t=>t.id===requested);
      if(force||!currentTask||!terminal(currentTask.status)||!listed||listed.updated_at!==currentTask.updated_at){
        let task;
        try{task=await api('/api/tasks/'+requested)}catch(e){
          if(e.status===404){if(selected===requested){selected=null;currentTask=null;localStorage.removeItem('frameflow-task');refreshQueued=true}return}throw e
        }
        let after=lastEvents.at(-1)?.seq||0,batch;
        const newEvents=[];
        do{
          batch=await api('/api/tasks/'+requested+'/events?limit=500&after_seq='+after);
          newEvents.push(...batch.items);after=batch.next_seq;
        }while(batch.items.length===500);
        if(selected!==requested){refreshQueued=true;return}
        currentTask=task;lastEvents=[...lastEvents,...newEvents].slice(-500);
        renderTask(task);renderEvents(lastEvents);
      }
      renderResources(resources);
    }else{renderTask(null)}
    $('#connection').innerHTML='<i class="dot"></i>本地连接正常';
  }catch(e){$('#connection').innerHTML='<i class="dot offline"></i>连接中断，正在重连';if(force)toast(e.message)}
  finally{busy=false;if(refreshQueued){refreshQueued=false;queueMicrotask(()=>refresh(true))}}
}
renderTask(null);refresh(true);setInterval(()=>refresh(),1200);
