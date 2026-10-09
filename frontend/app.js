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

async function request(path,options={}){const r=await fetch('/api'+path,options);const d=await r.json().catch(()=>({}));if(!r.ok){const e=new Error(d.detail||`Request failed (${r.status})`);e.status=r.status;throw e}return d}
let progressTimer=null,statusClearTimer=null,progressIndex=0;
const progressStages=['Breaking','Researching','Collecting'];
function busy(on,label='Investigating…',completed=false){
  clearInterval(progressTimer);clearTimeout(statusClearTimer);
  $('#systemStatus').classList.toggle('busy',on);
  $('#systemStatus span').textContent=on?'Research in progress':'System ready';
  const status=$('#status');
  if(on){
    progressIndex=0;
    const paint=()=>{status.innerHTML=`<span class="spinner"></span><span>${progressStages[progressIndex]}</span>`;progressIndex=(progressIndex+1)%progressStages.length};
    paint();progressTimer=setInterval(paint,1800);
  }else if(completed){
    status.innerHTML='<span class="progress-done" aria-hidden="true">✓</span><span>Completed</span>';
    statusClearTimer=setTimeout(()=>{status.innerHTML=''},3000);
  }else status.innerHTML='';
  $('#check').disabled=on;
}
function paintAiStatus(){
  const el=$('#aiStatus');if(!el)return;
  const state=aiStatusSnapshot.state||'unknown';
  const remaining=Math.max(0,Number(aiStatusSnapshot.retry_after_seconds||0)-Math.floor((Date.now()-aiStatusUpdatedAt)/1000));
  el.classList.remove('ai-available','ai-busy','ai-unavailable','ai-unknown');
  el.classList.add(state==='available'?'ai-available':state==='busy'?'ai-busy':state==='unavailable'?'ai-unavailable':'ai-unknown');
  const label=state==='available'?'AI available':state==='busy'?'AI busy':state==='unavailable'?(remaining>0?`AI resetting · ${remaining}s`:'AI unavailable'): 'AI status unknown';
  el.querySelector('span').textContent=label;
  el.title=state==='available'?(aiStatusSnapshot.detail||'Last request succeeded; availability can change'):state==='unavailable'?(remaining>0?`Rate limit cooldown. Retry in ${remaining} seconds.`:aiStatusSnapshot.detail||'AI provider unavailable'):aiStatusSnapshot.detail||label;
}
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
  if(url&&!/^https?:\\/\\//i.test(url)){renderError(Object.assign(new Error('Enter a valid URL starting with https:// or http://.'),{status:400}));return;}
  busy(true);$('#result').innerHTML='';let completed=false;
  try{
    if(url){
      const d=await request('/fact-check/url',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,text:url,...prefs()})});
      activeResultId=d.id;renderArticle(d);
    }else{
      const f=new FormData();f.append('text',text);for(const [k,v] of Object.entries(prefs()))f.append(k,v);selectedFiles.forEach(x=>f.append('files',x));
      const d=await request('/fact-check/media',{method:'POST',body:f});activeResultId=d.id;renderResult(d);
    }
    completed=true;
  }catch(e){renderError(e)}finally{busy(false,'',completed)}
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
