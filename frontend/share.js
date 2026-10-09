const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const cls=v=>['TRUE','MOSTLY TRUE'].includes(v)?'good':['FALSE','MOSTLY FALSE'].includes(v)?'bad':'warn';
const source=x=>`<article class="source-card"><div><b>${esc(x.title||'Source')}</b><div class="mini-meta">${esc(x.publisher)} · ${esc(x.source_tier||x.source_type||'Other')} · quality ${x.source_quality??0}/100</div></div><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">Open ↗</a></article>`;
const evidence=x=>`<article class="evidence-card"><b>${esc(x.title||x.publisher||'Evidence')}</b><div class="mini-meta">${esc(x.publisher)} · ${esc(x.source_tier||'Source')}</div><p>${esc(x.excerpt||'')}</p><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a></article>`;
(async()=>{
 const token=location.pathname.split('/').pop();
 const root=document.querySelector('#shareResult');
 try{
  const r=await fetch('/api/share/'+encodeURIComponent(token),{cache:'no-store'});
  const d=await r.json();
  if(!r.ok)throw Error(d.detail||'Unavailable');
  const article=Boolean(d.overall_verdict);
  const verdict=article?d.overall_verdict:d.verdict;
  const confidence=article?d.overall_confidence:d.confidence;
  const isQuestion=!article&&String(d.content_type||'').toUpperCase()==='QUESTION';
  const title=article?(d.article_title||d.article_url):(d.claim||d.report_title||'Fact Check');
  const meta=isQuestion?'Live evidence':`Assessment confidence <strong>${confidence??0}%</strong> · checked ${esc(d.last_checked||'')}`;
  const claims=d.claims_checked||[];
  root.innerHTML=`<div class="resultcard"><div class="result-head"><div><div class="result-kicker">SHARED INVESTIGATION · ${article?'ARTICLE':isQuestion?'QUESTION':esc(d.content_type||'FACT CHECK')}</div><h2>${esc(title)}</h2>${isQuestion?'':`<div class="verdict ${cls(verdict)}">${esc(verdict||'UNVERIFIED')}</div>`}<div class="confidence">${meta}</div></div></div><div class="result-grid"><div><section class="result-section"><h3>${isQuestion?'Answer':'Assessment'}</h3><p class="summary">${esc(d.summary||'No summary was supplied.')}</p></section><section class="result-section"><h3>Claims checked</h3>${claims.map((c,i)=>`<article class="claim-card"><b>${i+1}. ${esc(c.claim)}</b><div class="mini-verdict ${cls(c.verdict)}">${esc(c.verdict)} · ${c.confidence??0}%</div><p>${esc(c.summary)}</p></article>`).join('')||'<div class="empty">No separate claim breakdown.</div>'}</section><section class="result-section"><h3>Supporting evidence</h3>${(d.supporting_evidence||[]).map(evidence).join('')||'<div class="empty">None mapped.</div>'}</section><section class="result-section"><h3>Contradicting evidence</h3>${(d.contradicting_evidence||[]).map(evidence).join('')||'<div class="empty">None mapped.</div>'}</section></div><aside><section class="result-section"><h3>Sources</h3>${(d.sources||[]).map(source).join('')||'<div class="empty">No sources.</div>'}</section>${d.uncertainties?.length?`<div class="warning"><b>Uncertainty</b><ul>${d.uncertainties.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`:''}</aside></div></div>`;
 }catch(e){root.innerHTML=`<div class="card warning"><b>Shared result unavailable</b><p>${esc(e.message)}</p></div>`}
})();