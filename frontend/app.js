const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
let contextChatHistory=[],contextChatContext={},selectedFiles=[],activeResultId=null;
let aiStatusSnapshot={state:"unknown",retry_after_seconds:0,detail:"Waiting for status"};
let aiStatusUpdatedAt=Date.now();
const esc=x=>String(x??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const prefs=()=>({content_mode:$("#contentMode").value,detail:$("#detail").value,audience:$("#audience").value,source_preference:$("#sourcePref").value,region:$("#region").value,language:$("#language").value});

function setView(name){$$('.nav-btn').forEach(b=>b.classList.toggle('active',b.dataset.v===name));$$('.view').forEach(v=>v.classList.toggle('active',v.id===name));if(name==='history')loadHistory()}
$$('.nav-btn').forEach(b=>b.onclick=()=>{history.replaceState(null,'','#'+b.dataset.v);setView(b.dataset.v)});
window.addEventListener('hashchange',()=>setView(location.hash.slice(1)||'fact'));setView(location.hash.slice(1)||'fact');

function syncSettings(){for(const id of ['detail','audience','sourcePref','region','language']){const k='fc_'+id,v=localStorage.getItem(k);if(v&&$('#'+id))$('#'+id).value=v; if($('#'+id))$('#'+id).onchange=e=>{localStorage.setItem(k,e.target.value);const x=$('#'+id+'2');if(x)x.value=e.target.value}}
['detail','audience','sourcePref','region','language'].forEach(id=>{const x=$('#'+id+'2'),base=$('#'+id);if(!x)return;const k='fc_'+id,v=localStorage.getItem(k);if(v)x.value=v;x.onchange=e=>{localStorage.setItem(k,e.target.value);base.value=e.target.value}})}
syncSettings();

$('#advancedToggle').onclick=()=>{const x=$('#advanced'),open=x.classList.toggle('hidden')===false;$('#advancedToggle').setAttribute('aria-expanded',open)};
$('#input').oninput=e=>$('#charCount').textContent=e.target.value.length;
function renderFiles(){
  $('#mediaList').innerHTML=selectedFiles.length?selectedFiles.map((f,i)=>`<span class="file-chip">📎 ${esc(f.name)} · ${Math.max(1,Math.round(f.size/1024))} KB <button type="button" aria-label="Remove ${esc(f.name)}" onclick="removeFile(${i})">×</button></span>`).join(' '):'No media added';
}
function addFiles(files){selectedFiles=[...selectedFiles,...[...files]].filter((f,i,a)=>i===a.findIndex(x=>x.name===f.name&&x.size===f.size&&x.lastModified===f.lastModified)).slice(0,5);renderFiles()}
window.removeFile=i=>{selectedFiles.splice(i,1);renderFiles()};
$('#mediaInput').onchange=()=>addFiles($('#mediaInput').files);
['dragenter','dragover'].forEach(e=>$('#mediaBox').addEventListener(e,ev=>{ev.preventDefault();$('#mediaBox').classList.add('dragging')}));
['dragleave','drop'].forEach(e=>$('#mediaBox').addEventListener(e,ev=>{ev.preventDefault();$('#mediaBox').classList.remove('dragging')}));
$('#mediaBox').addEventListener('drop',ev=>addFiles(ev.dataTransfer.files));

async function request(path,options={}){const r=await fetch('/api'+path,options);const d=await r.json().catch(()=>({}));if(!r.ok){const e=new Error(d.detail||`Request failed (${r.status})`);e.status=r.status;e.retryAfter=Number(r.headers.get('Retry-After')||0);throw e}return d}
let waitingForAiAvailability=false;
function renderRetryNotice(){
  if(!waitingForAiAvailability||!activeProgressId)return;
  const status=$('#status');
  if(status){
    renderResearchProgress();
    status.insertAdjacentHTML('beforeend','<p class="mini-meta" role="status">AI rate limit reached — waiting for AI availability. This investigation will retry automatically when the provider can accept requests.</p>');
  }
}
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitUntilAiCanRetry(){
  waitingForAiAvailability=true;
  while(waitingForAiAvailability){
    renderRetryNotice();
    try{
      const r=await fetch('/api/ai-status',{cache:'no-store'});
      if(r.ok){
        const d=await r.json();
        aiStatusSnapshot={...d};aiStatusUpdatedAt=Date.now();paintAiStatus();
        if(d.token_capacity_sufficient===true||['retry_ready','verified','stale','ready'].includes(d.state))return;
      }
    }catch{}
    await wait(3000);
  }
}
async function requestWithRateLimitRetry(makeRequest,maxRetries=8){
  for(let attempt=0;;attempt++){
    try{waitingForAiAvailability=false;return await makeRequest()}
    catch(e){
      const isAiLimit=e.status===429&&/AI rate limit|AI analysis is temporarily rate-limited/i.test(e.message);
      if(!isAiLimit||attempt>=maxRetries)throw e;
      await waitUntilAiCanRetry();
      waitingForAiAvailability=false;
      // The real investigation request verifies availability. If the provider
      // still rejects it, the loop returns here and waits for availability again.
    }
  }
}
function renderResearchProgress(completed=false,failed=false){
  const status=$('#status');if(!status)return;
  status.innerHTML='<div class="research-progress" role="status" aria-live="polite">'+progressStages.map((stage,i)=>{
    const current=progressStages.indexOf(serverResearchStage),done=completed||i<current,active=!completed&&i===current;
    const marker=done?'<span class="stage-check">✓</span>':active?'<span class="stage-spinner"></span>':'<span class="stage-empty">◻</span>';
    return '<span class="progress-stage '+(done?'done':active?'active':'pending')+'">'+marker+'<span>'+stage+'</span></span>';
  }).join('')+(serverAiWaiting?'<span class="progress-stage active ai-waiting"><span class="stage-spinner"></span><span>waiting for AI tokens</span></span>':'')+(failed?'<span class="progress-stage failed"><span class="stage-check">!</span><span>failed</span></span>':'')+'</div>';
}
function startProgressPolling(){
  clearTimeout(progressPollTimer);
  const poll=async()=>{
    if(!activeProgressId)return;
    try{
      const r=await fetch('/api/research-progress/'+encodeURIComponent(activeProgressId),{cache:'no-store'});
      if(r.ok){
        const d=await r.json();
        if(d.stage==='waiting_ai'){
          serverAiWaiting=true;serverResearchStage='analyzing';renderResearchProgress();
        }else if(progressStages.includes(d.stage)){
          serverAiWaiting=false;serverResearchStage=d.stage;renderResearchProgress();
        }
      }
    }catch{}
    // Schedule only after the previous poll finishes to prevent overlapping requests.
    if(activeProgressId)progressPollTimer=setTimeout(poll,900);
  };
  progressPollTimer=setTimeout(poll,0);
}
function busy(on,label='Investigating…',completed=false,failed=false){
  clearTimeout(statusClearTimer);
  $('#systemStatus').classList.toggle('busy',on);
  $('#systemStatus span').textContent=on?'Research in progress':'System ready';
  if(on){
    activeProgressId='rp-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,10);
    serverResearchStage='breaking';serverAiWaiting=false;renderResearchProgress();startProgressPolling();
  }else{
    clearTimeout(progressPollTimer);progressPollTimer=null;
    if(completed){serverResearchStage='analyzing';renderResearchProgress(true);statusClearTimer=setTimeout(()=>{$('#status').innerHTML=''},5000)}
    else if(failed){serverResearchStage='error';renderResearchProgress(false,true);statusClearTimer=setTimeout(()=>{$('#status').innerHTML=''},5000);activeProgressId=null}
    else{$('#status').innerHTML='';activeProgressId=null}
  }
  $('#check').disabled=on;
}
function paintAiStatus(){
  const el=$('#aiStatus');if(!el)return;
  const state=aiStatusSnapshot.state||'unknown';
  const elapsed=Math.floor((Date.now()-aiStatusUpdatedAt)/1000);
  const remaining=Math.max(0,Number(aiStatusSnapshot.retry_after_seconds||0)-elapsed);
  const refill=Math.max(0,Number(aiStatusSnapshot.token_refill_in_seconds||0)-elapsed);
  const age=aiStatusSnapshot.last_success_ago_seconds;
  el.classList.remove('ai-available','ai-busy','ai-unavailable','ai-unknown');
  const tone=state==='verified'?'ai-available':state==='busy'?'ai-busy':['rate_limited','unavailable'].includes(state)?'ai-unavailable':'ai-unknown';
  el.classList.add(tone);
  const label=state==='verified'?`AI verified · 1 request (${age??0}s ago)`:state==='retry_ready'?'AI retry ready · 1 try':state==='rate_limited'?`AI blocked · ${remaining}s`:state==='stale'?'AI capacity unverified':state==='ready'?'AI untested':state==='busy'?'AI testing request':state==='unavailable'?'AI unavailable':'AI status unknown';
  el.querySelector('span').textContent=label;
  el.title=aiStatusSnapshot.detail||label;
  const panel=$('#aiUsagePanel');
  if(panel){
    const fmt=n=>n==null?'Not reported':Number(n).toLocaleString();
    const available=aiStatusSnapshot.tokens_remaining,limit=aiStatusSnapshot.token_limit,usage=aiStatusSnapshot.last_request_usage;
    const refillText=refill>0?formatDuration(refill):aiStatusSnapshot.token_refill_in_seconds!=null&&available!=null?'Reset window elapsed; awaiting fresh provider data':'Not reported yet';
    const usageText=usage?.total_tokens!=null?`<strong>${fmt(usage.total_tokens)} tokens</strong><div class="mini-meta">Input ${fmt(usage.prompt_tokens)} + output ${fmt(usage.completion_tokens)} · last successful AI call</div>`:'<span class="mini-meta">No usage data returned yet</span>';
    panel.innerHTML=`<div class="ai-usage-title">AI token usage</div><div class="ai-usage-row"><span>Token availability</span><strong>${available==null?'Not reported':fmt(available)+' remaining'}${limit==null?'':' / '+fmt(limit)}</strong></div><div class="ai-usage-row"><span>Token-window reset</span><strong>${refillText}</strong></div><div class="ai-usage-row usage-total"><span>Last AI call used</span><div>${usageText}</div></div><div class="mini-meta">Provider-reported limits; last AI call is not the total for the full investigation.</div>`;
  }
}
function formatDuration(seconds){const n=Math.max(0,Math.ceil(Number(seconds)||0));return n<60?n+'s':Math.floor(n/60)+'m '+(n%60)+'s'}
function toggleAiUsage(){const panel=$('#aiUsagePanel'),button=$('#aiStatus');if(!panel||!button)return;const open=panel.classList.toggle('hidden')===false;button.setAttribute('aria-expanded',String(open))}
$('#aiStatus').addEventListener('click',toggleAiUsage);
$('#aiStatus').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();toggleAiUsage()}});
async function refreshAiStatus(){
  try{
    const r=await fetch('/api/ai-status',{cache:'no-store'});
    if(!r.ok)throw Error('AI status endpoint unavailable');
    const d=await r.json();
    aiStatusSnapshot={...d};
    aiStatusUpdatedAt=Date.now();
    paintAiStatus();
  }catch{
    aiStatusSnapshot={state:'unknown',retry_after_seconds:0,detail:'Could not refresh AI availability'};
    aiStatusUpdatedAt=Date.now();
    paintAiStatus();
  }
}
setInterval(paintAiStatus,1000);

setInterval(refreshAiStatus,3000);
refreshAiStatus();

function renderError(e){const title=e.status===429?'AI rate limit':e.status===503?'Capability temporarily unavailable':'Investigation could not be completed';$('#result').innerHTML=`<div class="warning"><b>${title}</b><p>${esc(e.message)}</p>${e.status===503?'<p class="mini-meta">This does not mean the uploaded file is invalid. The research service is missing a currently available analysis capability.</p>':''}</div>`}

async function submitMedia(){
  const text=$('#input').value.trim(),url=$('#articleUrl').value.trim();
  if(!text&&!url){renderError(Object.assign(new Error('Enter text to research or add an article URL.'),{status:400}));return;}
  if(url&&!/^https?:\/\//i.test(url)){renderError(Object.assign(new Error('Enter a valid URL starting with https:// or http://.'),{status:400}));return;}
  busy(true);$('#result').innerHTML='';let completed=false,failed=false;
  try{
    if(url){
      const d=await requestWithRateLimitRetry(()=>request('/fact-check/url',{method:'POST',headers:{'Content-Type':'application/json','X-Research-ID':activeProgressId},body:JSON.stringify({url,text:url,...prefs()})}));
      activeResultId=d.id;renderArticle(d);
    }else{
      const f=new FormData();f.append('text',text);for(const [k,v] of Object.entries(prefs()))f.append(k,v);selectedFiles.forEach(x=>f.append('files',x));
      const d=await requestWithRateLimitRetry(()=>request('/fact-check/media',{method:'POST',headers:{'X-Research-ID':activeProgressId},body:f}));activeResultId=d.id;renderResult(d);
    }
    completed=true;
  }catch(e){failed=true;renderError(e)}finally{busy(false,'',completed,failed)}
}
$('#check').onclick=submitMedia;
function verdictClass(v){if(['TRUE','MOSTLY TRUE'].includes(v))return'good';if(['FALSE','MOSTLY FALSE'].includes(v))return'bad';return'warn'}
function evidenceCards(a,contra=false){return(a||[]).map(x=>`<article class="evidence-card ${contra?'contra':''}"><b>${esc(x.title||x.publisher||'Evidence')}</b><div class="mini-meta">${esc(x.publisher)} · ${esc(x.source_tier||x.source_type||'Source')} · quality ${x.source_quality??0}/100${x.relevance_reason?` · ${esc(x.relevance_reason)}`:''}</div><p>${esc(x.excerpt||'No excerpt supplied.')}</p><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a></article>`).join('')||'<div class="empty">No relevant evidence mapped.</div>'}
function sourceCards(a){return(a||[]).map((x,i)=>`<article class="source-card"><div><b>${i+1}. ${esc(x.title||'Untitled source')}</b><div class="mini-meta">${esc(x.publisher)} · ${esc(x.source_tier||x.source_type||'Other')} · ${x.corroboration_count??0} source(s)${x.relevance_reason?` · ${esc(x.relevance_reason)}`:''}</div></div><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">Open ↗</a></article>`).join('')||'<div class="empty">No sources were mapped to the final assessment.</div>'}
function claims(a){return(a||[]).map((c,i)=>`<article class="claim-card"><b>${i+1}. ${esc(c.claim)}</b><div class="mini-verdict ${verdictClass(c.verdict)}">${esc(c.verdict)} · ${c.confidence}%</div><p>${esc(c.summary)}</p><div class="mini-meta">${esc(c.content_type)} · source quality ${c.source_quality}/100 · ${c.corroboration_count} independent source(s)</div>${c.reasoning?`<p><b>Reasoning:</b> ${esc(c.reasoning)}</p>`:''}${c.what_would_change_conclusion?`<p class="mini-meta"><b>Could change:</b> ${esc(c.what_would_change_conclusion)}</p>`:''}</article>`).join('')||'<div class="empty">No separate claim assessments were returned.</div>'}
function mediaCards(a){return(a||[]).map(x=>`<article class="media-card"><b>📎 ${esc(x.filename)}</b><div class="mini-meta">${esc(x.kind)} · ${Math.round((x.size_bytes||0)/1024)} KB</div>${x.extracted_text?`<p><b>Extracted text:</b> ${esc(x.extracted_text)}</p>`:''}${x.visual_summary?`<p><b>Visual context:</b> ${esc(x.visual_summary)}</p>`:''}</article>`).join('')||'<div class="empty">No media attached.</div>'}
function reportShell(d,article=false){const isQuestion=!article&&String(d.content_type||'').toUpperCase()==='QUESTION';const verdict=article?d.overall_verdict:d.verdict,confidence=article?d.overall_confidence:d.confidence;const meta=isQuestion?'Live evidence':`Confidence <strong>${confidence}%</strong> · ${esc(d.last_checked||'')}`;const kind=article?'ARTICLE':(isQuestion?'QUESTION':'CLAIM');const typeLabel=article?'LIVE SOURCES':(isQuestion?'QUESTION':d.content_type);return `<div class="resultcard"><div class="result-head"><div><div class="result-kicker">${kind}${isQuestion?'':` · ${esc(typeLabel)}`}</div><h2>${esc(article?d.article_title:(d.claim||d.report_title||'Fact Check'))}</h2>${isQuestion?'':`<div class="verdict ${verdictClass(verdict)}">${esc(verdict)}</div>`}<div class="confidence">${meta}</div></div></div>`}
function resourcePlanCard(d){
  const p=d.resource_plan||{};
  if(!p.context&&!p.resource_types?.length)return '';
  const types=(p.resource_types||[]).map(x=>'<span class="tag">'+esc(x)+'</span>').join(' ');
  const domains=(p.preferred_domains||[]).map(x=>esc(x)).join(', ');
  return '<section class="result-section resource-plan"><h3>Resource plan</h3>'+
    '<div class="mini-meta"><b>Context:</b> '+esc(p.context||'general')+'</div>'+
    '<p>'+esc(p.rationale||'Resources selected for this investigation context.')+'</p>'+
    '<div>'+types+'</div>'+
    (domains?'<div class="mini-meta"><b>Preferred:</b> '+domains+'</div>':'')+
    '</section>';
}
function researchCards(d){
  const qs=d.research_questions||[];
  const rd=d.research_data||[];
  if(!qs.length&&!rd.length)return '';
  const byQ=new Map(rd.map(x=>[x.question,x]));
  return `<section class="result-section research-trace"><h3>Research process</h3>
    <div class="process-steps">
      <div><b>1. Input</b><p>Original user input</p></div>
      <div><b>2. Breakdown</b><p>${qs.length} research question(s)</p></div>
      <div><b>3. Web search</b><p>Live sources searched</p></div>
      <div><b>4. Raw research</b><p>Question-level findings</p></div>
      <div><b>5. Synthesis</b><p>Answer + verdict</p></div>
    </div>
    ${qs.map((q,i)=>{
      const x=byQ.get(q)||{};
      return `<article class="research-item">
        <b>Q${i+1}: ${esc(q)}</b>
        <p>${esc(x.answer||'No sufficient retrieved answer.')}</p>
        ${x.evidence_ids?.length?`<div class="mini-meta">Evidence: ${esc(x.evidence_ids.join(', '))}</div>`:''}
      </article>`;
    }).join('')}
  </section>`;
}
function contextChatPanel(d,article=false){
  contextChatHistory=[];
  contextChatContext={type:article?'article':(String(d.content_type||'').toUpperCase()==='QUESTION'?'question':'claim'),input:article?(d.article_title||d.article_url):(d.claim||''),answer:d.summary||'',verdict:article?d.overall_verdict:d.verdict,confidence:article?d.overall_confidence:d.confidence,research_questions:d.research_questions||[],question_coverage:d.question_coverage||[],research_data:d.research_data||[],uncertainties:d.uncertainties||[],sources:(d.sources||[]).slice(0,10).map(x=>({title:x.title,publisher:x.publisher,url:x.url,excerpt:x.excerpt,relevance_reason:x.relevance_reason,source_tier:x.source_tier}))};
  const suggestions=['Which sources support this answer?','What remains uncertain?','Is there contradictory evidence?'];
  return `<details class="context-chat"><summary><span><b>Discuss this result</b><small>Ask follow-ups about this answer and its sources</small></span><span class="chat-chevron">＋</span></summary><div class="context-chat-body"><div class="chat-suggestions">${suggestions.map(q=>`<button type="button" class="chat-suggestion" data-chat-prompt="${esc(q)}">${esc(q)}</button>`).join('')}</div><div class="context-chat-log" id="contextChatLog"><p class="context-chat-empty">Ask about the answer, evidence, or uncertainty.</p></div><form class="context-chat-compose" id="contextChatForm"><input id="contextChatInput" maxlength="4000" placeholder="Ask about this result…" aria-label="Ask about this result"><button class="primary" type="submit">Ask ↗</button></form><div class="mini-meta">Uses this report's evidence. It won't pretend to have done new research.</div></div></details>`;
}
function renderChatLog(){
  const log=$('#contextChatLog');if(!log)return;
  log.innerHTML=contextChatHistory.length?contextChatHistory.map(x=>`<div class="context-chat-message ${x.role==='user'?'user':'assistant'}">${esc(x.content)}</div>`).join(''):'<p class="context-chat-empty">Ask about the answer, evidence, or uncertainty.</p>';
  log.scrollTop=log.scrollHeight;
}
async function sendContextChat(value){
  const message=String(value||'').trim();if(!message)return;
  const input=$('#contextChatInput'),send=$('#contextChatForm button[type="submit"]');
  if(input)input.value='';
  contextChatHistory.push({role:'user',content:message});renderChatLog();
  if(send)send.disabled=true;if(input)input.disabled=true;
  const log=$('#contextChatLog');if(log){const wait=document.createElement('div');wait.className='context-chat-message assistant';wait.textContent='Thinking…';wait.id='contextChatWaiting';log.appendChild(wait);log.scrollTop=log.scrollHeight}
  try{const d=await request('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message,history:contextChatHistory.slice(0,-1).slice(-10),context:contextChatContext})});contextChatHistory.push({role:'assistant',content:d.reply})}
  catch(e){contextChatHistory.push({role:'assistant',content:e.message||'Could not answer right now. Please try again.'})}
  if(send)send.disabled=false;if(input)input.disabled=false;renderChatLog();if(input)input.focus();
}
$('#result').addEventListener('submit',e=>{if(e.target.id==='contextChatForm'){e.preventDefault();sendContextChat($('#contextChatInput')?.value)}});
$('#result').addEventListener('click',e=>{const b=e.target.closest('[data-chat-prompt]');if(b)sendContextChat(b.dataset.chatPrompt)});
function renderResult(d){
  const uncertainty=d.uncertainties?.length?'<div class="warning"><b>Uncertainty:</b> '+esc(d.uncertainties[0])+'</div>':'';
  $('#result').innerHTML=reportShell(d)+'<div class="result-grid"><div><section class="result-section final-answer"><h3>'+(String(d.content_type||'').toUpperCase()==='QUESTION'?'ANSWER':'Conclusion')+'</h3><p class="summary">'+esc(d.summary||d.reasoning||'No grounded answer was available.')+'</p></section><section class="result-section"><h3>Sources</h3>'+sourceCards(d.sources)+'</section>'+uncertainty+'</div></div>'+contextChatPanel(d)+shareAction(d.id)+'</div>';
}
function renderArticle(d){
  $('#result').innerHTML=reportShell(d,true)+`<div class="result-grid"><div><section class="result-section"><h3>Bottom line</h3><p class="summary">${esc(d.summary)}</p></section><section class="result-section"><h3>Claims</h3>${claims((d.claims_checked||[]).map(c=>({...c,content_type:'ARTICLE CLAIM',source_quality:0,corroboration_count:0})))}</section></div><aside><section class="result-section"><h3>Sources</h3>${sourceCards(d.sources)}</section>${d.uncertainties?.length?`<div class="warning"><b>Uncertainty</b><ul>${d.uncertainties.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`:''}</aside></div>`+contextChatPanel(d,true)+shareAction(d.id)+`</div>`}
function shareAction(id){return `<div class="result-actions"><button class="link-btn share-link" onclick="shareResult(${id})">Copy share link ↗</button></div>`}
window.shareResult=async id=>{const u=location.origin+'/share/'+id;try{await navigator.clipboard.writeText(u);$('#status').textContent='Share link copied.';setTimeout(()=>$('#status').textContent='',1800)}catch{prompt('Copy share link',u)}};

async function loadHistory(){const q=encodeURIComponent($('#hq').value||'');try{const d=await request('/history?q='+q);$('#historyList').innerHTML=d.items?.length?d.items.map(x=>`<div class="history-row"><div><div class="history-title">${esc(x.title||'Untitled investigation')}</div><div class="history-meta">${esc(x.kind)} · ${esc(x.created_at)}</div></div><b class="mini-verdict ${verdictClass(x.verdict)}">${esc(x.verdict)} · ${x.confidence}%</b><div class="row-actions"><button class="secondary" onclick="openHistory(${x.id})">Open</button> <button class="secondary" onclick="deleteHistory(${x.id})">Delete</button></div></div>`).join(''):'<div class="empty">No investigations in this browser session.</div>'}catch(e){$('#historyList').innerHTML='<div class="empty">Unable to load history.</div>'}}
window.openHistory=async id=>{try{const d=await request('/history/'+id);activeResultId=id;setView('fact');renderResult(d)}catch(e){renderError(e)}};
window.deleteHistory=async id=>{if(!confirm('Delete this investigation?'))return;await request('/history/'+id,{method:'DELETE'});loadHistory()};
$('#hq').oninput=()=>loadHistory();$('#clear').onclick=async()=>{if(confirm('Clear all browser history?')){await request('/history',{method:'DELETE'});loadHistory()}};

(async()=>{try{const h=await fetch('/api/health').then(r=>r.json());$('#version').textContent=`v${esc(h.version||'')}`;if(h.status!=='ok')throw Error()}catch{ $('#systemStatus span').textContent='Service degraded';$('#systemStatus i').style.background='var(--warn)'}})();
