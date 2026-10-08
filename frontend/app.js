const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
let chatHistory=[],selectedFiles=[],activeResultId=null;
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
$('#claimTab').onclick=()=>switchMode(false);$('#urlTab').onclick=()=>switchMode(true);
function switchMode(url){$('#claimTab').classList.toggle('active',!url);$('#urlTab').classList.toggle('active',url);$('#check').classList.toggle('hide',url);$('#analyze').classList.toggle('hide',!url);$('#mediaBox').classList.toggle('hide',url);$('#input').placeholder=url?'https://example.com/news/article':'Paste a claim, social post, quote, statistic, or question…'}
function renderFiles(){
  $('#mediaList').innerHTML=selectedFiles.length?selectedFiles.map((f,i)=>`<span class="file-chip">📎 ${esc(f.name)} · ${Math.max(1,Math.round(f.size/1024))} KB <button type="button" aria-label="Remove ${esc(f.name)}" onclick="removeFile(${i})">×</button></span>`).join(' '):'Add image, audio, or video · up to 5';
}
function addFiles(files){selectedFiles=[...selectedFiles,...[...files]].filter((f,i,a)=>i===a.findIndex(x=>x.name===f.name&&x.size===f.size&&x.lastModified===f.lastModified)).slice(0,5);renderFiles()}
window.removeFile=i=>{selectedFiles.splice(i,1);renderFiles()};
$('#mediaInput').onchange=()=>addFiles($('#mediaInput').files);
['dragenter','dragover'].forEach(e=>$('#mediaBox').addEventListener(e,ev=>{ev.preventDefault();$('#mediaBox').classList.add('dragging')}));
['dragleave','drop'].forEach(e=>$('#mediaBox').addEventListener(e,ev=>{ev.preventDefault();$('#mediaBox').classList.remove('dragging')}));
$('#mediaBox').addEventListener('drop',ev=>addFiles(ev.dataTransfer.files));

async function request(path,options={}){const r=await fetch('/api'+path,options);const d=await r.json().catch(()=>({}));if(!r.ok){const e=new Error(d.detail||`Request failed (${r.status})`);e.status=r.status;throw e}return d}
function busy(on,label='Investigating…'){$('#systemStatus').classList.toggle('busy',on);$('#systemStatus span').textContent=on?'Research in progress':'System ready';$('#status').innerHTML=on?`<span class="spinner"></span>${esc(label)}`:'';$('#check').disabled=on;$('#analyze').disabled=on}
function renderError(e){const title=e.status===429?'AI rate limit':e.status===503?'Capability temporarily unavailable':'Investigation could not be completed';$('#result').innerHTML=`<div class="warning"><b>${title}</b><p>${esc(e.message)}</p>${e.status===503?'<p class="mini-meta">This does not mean the uploaded file is invalid. The research service is missing a currently available analysis capability.</p>':''}</div>`}

async function submitMedia(){const text=$('#input').value.trim();if(!text){renderError(Object.assign(new Error('Input is required. Attachments are optional.'),{status:400}));return;}busy(true,'Breaking input → researching questions → collecting evidence…');$('#result').innerHTML='';try{const f=new FormData();f.append('text',text);for(const [k,v] of Object.entries(prefs()))f.append(k,v);selectedFiles.forEach(x=>f.append('files',x));const d=await request('/fact-check/media',{method:'POST',body:f});activeResultId=d.id;renderResult(d)}catch(e){renderError(e)}finally{busy(false)}}
$('#check').onclick=submitMedia;
$('#analyze').onclick=async()=>{const url=$('#input').value.trim();if(!url)return;busy(true,'Fetching article → extracting claims → checking evidence…');$('#result').innerHTML='';try{const d=await request('/fact-check/url',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,text:url,...prefs()})});activeResultId=d.id;renderArticle(d)}catch(e){renderError(e)}finally{busy(false)}};

function verdictClass(v){if(['TRUE','MOSTLY TRUE'].includes(v))return'good';if(['FALSE','MOSTLY FALSE'].includes(v))return'bad';return'warn'}
function evidenceCards(a,contra=false){return(a||[]).map(x=>`<article class="evidence-card ${contra?'contra':''}"><b>${esc(x.title||x.publisher||'Evidence')}</b><div class="mini-meta">${esc(x.publisher)} · ${esc(x.source_tier||x.source_type||'Source')} · quality ${x.source_quality??0}/100${x.relevance_reason?` · ${esc(x.relevance_reason)}`:''}</div><p>${esc(x.excerpt||'No excerpt supplied.')}</p><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a></article>`).join('')||'<div class="empty">No relevant evidence mapped.</div>'}
function sourceCards(a){return(a||[]).map((x,i)=>`<article class="source-card"><div><b>${i+1}. ${esc(x.title||'Untitled source')}</b><div class="mini-meta">${esc(x.publisher)} · ${esc(x.source_tier||x.source_type||'Other')} · ${x.corroboration_count??0} source(s)${x.relevance_reason?` · ${esc(x.relevance_reason)}`:''}</div></div><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">Open ↗</a></article>`).join('')||'<div class="empty">No sources were mapped to the final assessment.</div>'}
function claims(a){return(a||[]).map((c,i)=>`<article class="claim-card"><b>${i+1}. ${esc(c.claim)}</b><div class="mini-verdict ${verdictClass(c.verdict)}">${esc(c.verdict)} · ${c.confidence}%</div><p>${esc(c.summary)}</p><div class="mini-meta">${esc(c.content_type)} · source quality ${c.source_quality}/100 · ${c.corroboration_count} independent source(s)</div>${c.reasoning?`<p><b>Reasoning:</b> ${esc(c.reasoning)}</p>`:''}${c.what_would_change_conclusion?`<p class="mini-meta"><b>Could change:</b> ${esc(c.what_would_change_conclusion)}</p>`:''}</article>`).join('')||'<div class="empty">No separate claim assessments were returned.</div>'}
function mediaCards(a){return(a||[]).map(x=>`<article class="media-card"><b>📎 ${esc(x.filename)}</b><div class="mini-meta">${esc(x.kind)} · ${Math.round((x.size_bytes||0)/1024)} KB</div>${x.extracted_text?`<p><b>Extracted text:</b> ${esc(x.extracted_text)}</p>`:''}${x.visual_summary?`<p><b>Visual context:</b> ${esc(x.visual_summary)}</p>`:''}</article>`).join('')||'<div class="empty">No media attached.</div>'}
function reportShell(d,article=false){const isQuestion=!article&&String(d.content_type||'').toUpperCase()==='QUESTION';const verdict=article?d.overall_verdict:(isQuestion?'':d.verdict),confidence=article?d.overall_confidence:d.confidence;const meta=isQuestion?'Live evidence':`Confidence <strong>${confidence}%</strong> · ${esc(d.last_checked||'')}`;return `<div class="resultcard"><div class="result-head"><div><div class="result-kicker">${article?'ARTICLE':(isQuestion?'QUESTION':'CLAIM')} · ${esc(article?'LIVE SOURCES':d.content_type)}</div><h2>${esc(article?d.article_title:(d.claim||d.report_title||'Fact Check'))}</h2><div class="verdict ${verdictClass(verdict)}">${isQuestion?'ANSWER':esc(verdict)}</div><div class="confidence">${meta}</div></div><button class="secondary share-btn" onclick="shareResult(${d.id})">Share</button></div>`}
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
function renderResult(d){
  const verdict=d.verdict||'UNVERIFIED';
  const uncertainty=d.uncertainties?.length
    ? '<div class="warning"><b>Uncertainty:</b> '+esc(d.uncertainties[0])+'</div>' : '';
  $('#result').innerHTML=reportShell(d)+
    '<div class="result-grid"><div>'+
      '<section class="result-section final-answer"><h3>Answer</h3><p class="summary">'+esc(d.summary||d.reasoning||'No grounded answer was available.')+'</p></section>'+
      '<section class="result-section"><h3>Sources</h3>'+sourceCards(d.sources)+'</section>'+
      uncertainty+
    '</div></div></div>';
}
function renderArticle(d){$('#result').innerHTML=reportShell(d,true)+`<div class="result-grid"><div><section class="result-section"><h3>Bottom line</h3><p class="summary">${esc(d.summary)}</p></section><section class="result-section"><h3>Claims</h3>${claims((d.claims_checked||[]).map(c=>({...c,content_type:'ARTICLE CLAIM',source_quality:0,corroboration_count:0})) )}</section></div><aside><section class="result-section"><h3>Sources</h3>${sourceCards(d.sources)}</section>${d.uncertainties?.length?`<div class="warning"><b>Uncertainty</b><ul>${d.uncertainties.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`:''}</aside></div></div>`}
window.shareResult=async id=>{const u=location.origin+'/share/'+id;try{await navigator.clipboard.writeText(u);$('#status').textContent='Share link copied.';setTimeout(()=>$('#status').textContent='',1800)}catch{prompt('Copy share link',u)}};

async function loadHistory(){const q=encodeURIComponent($('#hq').value||'');try{const d=await request('/history?q='+q);$('#historyList').innerHTML=d.items?.length?d.items.map(x=>`<div class="history-row"><div><div class="history-title">${esc(x.title||'Untitled investigation')}</div><div class="history-meta">${esc(x.kind)} · ${esc(x.created_at)}</div></div><b class="mini-verdict ${verdictClass(x.verdict)}">${esc(x.verdict)} · ${x.confidence}%</b><div class="row-actions"><button class="secondary" onclick="openHistory(${x.id})">Open</button> <button class="secondary" onclick="deleteHistory(${x.id})">Delete</button></div></div>`).join(''):'<div class="empty">No investigations in this browser session.</div>'}catch(e){$('#historyList').innerHTML='<div class="empty">Unable to load history.</div>'}}
window.openHistory=async id=>{try{const d=await request('/history/'+id);activeResultId=id;setView('fact');renderResult(d)}catch(e){renderError(e)}};
window.deleteHistory=async id=>{if(!confirm('Delete this investigation?'))return;await request('/history/'+id,{method:'DELETE'});loadHistory()};
$('#hq').oninput=()=>loadHistory();$('#clear').onclick=async()=>{if(confirm('Clear all browser history?')){await request('/history',{method:'DELETE'});loadHistory()}};

function addBubble(role,text){const empty=$('.empty-chat');if(empty)empty.remove();const d=document.createElement('div');d.className='bubble '+role;d.textContent=text;$('#chatLog').appendChild(d);$('#chatLog').scrollTop=$('#chatLog').scrollHeight}
$('#send').onclick=sendChat;$('#chatInput').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendChat()}};
async function sendChat(){const m=$('#chatInput').value.trim();if(!m)return;addBubble('user',m);$('#chatInput').value='';try{const d=await request('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:m,history:chatHistory})});chatHistory.push({role:'user',content:m},{role:'assistant',content:d.reply});addBubble('ai',d.reply)}catch(e){addBubble('ai',e.message)}}

(async()=>{try{const h=await fetch('/api/health').then(r=>r.json());$('#version').textContent=`v${esc(h.version||'')}`;if(h.status!=='ok')throw Error()}catch{ $('#systemStatus span').textContent='Service degraded';$('#systemStatus i').style.background='var(--warn)'}})();
