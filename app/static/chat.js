const $ = id => document.getElementById(id);
let catalog = {}, previousTurn = null, controller = null;
const labels = {answered:'已回答',evidence_only:'仅检索片段',needs_clarification:'需要补充信息',insufficient_evidence:'证据不足',error:'服务异常'};
const stages = {started:'开始处理',retrieval:'正在检索',reranking:'正在重排',generation:'正在生成',verification:'正在核验草稿'};
function option(parent,value,label) {
  const el = document.createElement('option'); el.value=value; el.textContent=label; parent.append(el);
}
function versions() {
  const box=$('version'); box.replaceChildren(); option(box,'','未指定（必要时澄清）');
  for (const version of catalog[$('product').value]||[]) option(box,version,version);
}
async function initialize() {
  try {
    const [health,res] = await Promise.all([fetch('/health').then(r=>r.json()),fetch('/api/v1/catalog').then(r=>r.json())]);
    catalog=res.products;
    for (const product of Object.keys(catalog)) option($('product'),product,product);
    versions();
    $('mode').textContent=(health.llm_provider==='extractive'?'零密钥演示：只展示检索片段，不代表真实模型回答效果。':'已选择真实模型模式；实际可用性以请求结果为准。')+' 已导入 '+health.document_count+' 份文档。';
  } catch { $('mode').textContent='无法连接服务，请确认本地服务已启动。'; }
}
function render(data) {
  $('status').textContent=labels[data.status]||data.status;
  $('answer').textContent=data.answer;
  $('verification').textContent=data.verification==='model_checked_not_human_verified'?'引用原文已匹配，并经模型检查支持关系；未经人工核验。':data.verification==='extractive_only'?'当前片段由检索直接返回，未进行语义答案核验。':'';
  $('citations').replaceChildren();
  if (!data.citations.length) $('citations').textContent='本次没有可引用的答案证据。';
  for (const hit of data.citations) {
    const item=document.createElement('div'); item.className='evidence';
    const title=document.createElement('strong'); title.textContent='['+hit.citation_id+'] '+(hit.metadata.title||hit.source);
    const meta=document.createElement('div'); meta.className='hint';
    meta.textContent=[hit.metadata.version,hit.metadata.section,hit.metadata.page_number?'第 '+hit.metadata.page_number+' 页':null].filter(Boolean).join(' · ');
    const quote=document.createElement('p'); quote.textContent=hit.text; item.append(title,meta,quote);
    const url=hit.metadata.upstream_url;
    if (url) { try {
      const target=new URL(url);
      if (target.protocol==='https:'&&target.hostname==='github.com') {
        const link=document.createElement('a'); link.href=url; link.target='_blank'; link.rel='noopener noreferrer';
        link.textContent='查看固定版本的上游文档'; item.append(link);
      }
    } catch {} }
    $('citations').append(item);
  }
  $('timing').replaceChildren();
  for (const [key,label] of [['retrieval_ms','检索'],['generation_ms','回答与核验'],['total_ms','总计']]) {
    const span=document.createElement('span'); span.className='metric'; span.textContent=label+' '+data.trace[key]+' ms'; $('timing').append(span);
  }
  $('traceBox').hidden=false;
  $('trace').textContent=JSON.stringify({trace:data.trace,reason:data.reason,retrieval:data.retrieval,context:data.context},null,2);
  previousTurn=data.status==='error'?previousTurn:data.conversation;
  $('conversation').textContent=previousTurn?'下一条可补充版本或追问；仅带入上一轮。':'';
}
async function consumeSSE(response, onEvent) {
  if (!response.ok) throw Error('请求失败（HTTP '+response.status+'）');
  const reader=response.body.getReader(), decoder=new TextDecoder();
  let buffer='', doneEvent=false;
  try {
    while (true) {
      const {value,done}=await reader.read();
      buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});
      let at;
      while ((at=buffer.indexOf('\n\n'))>=0) {
        const frame=buffer.slice(0,at); buffer=buffer.slice(at+2);
        const lines=frame.split('\n');
        const event=lines.find(l=>l.startsWith('event: '))?.slice(7);
        const raw=lines.filter(l=>l.startsWith('data: ')).map(l=>l.slice(6)).join('\n');
        if (event&&raw) { onEvent(event,JSON.parse(raw)); if(event==='done') doneEvent=true; }
      }
      if (done) break;
    }
    if (!doneEvent) throw Error('连接中断，草稿尚未成为最终答案。');
  } finally { reader.releaseLock(); }
}
$('product').addEventListener('change',()=>{versions();previousTurn=null;});
$('cancel').addEventListener('click',()=>controller?.abort());
$('reset').addEventListener('click',()=>{previousTurn=null;$('conversation').textContent='已开始新对话。';});
$('form').addEventListener('submit',async event=>{
  event.preventDefault(); controller=new AbortController();
  $('submit').disabled=true; $('reset').disabled=true; $('cancel').hidden=false;
  $('submit').textContent='正在处理…'; $('result').hidden=false; $('status').textContent='处理中';
  $('answer').textContent=''; $('citations').replaceChildren(); $('verification').textContent='';
  $('timing').replaceChildren(); $('traceBox').hidden=true;
  let finalSeen=false;
  try {
    const payload={question:$('question').value,top_k:Number($('top_k').value)};
    if ($('product').value) payload.product=$('product').value;
    if ($('version').value) payload.version=$('version').value;
    if (previousTurn) payload.previous_turn=previousTurn;
    const res=await fetch('/api/v1/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal});
    await consumeSSE(res,(name,data)=>{
      if (name==='stage') $('status').textContent=stages[data.stage]||data.stage;
      if (name==='draft') { $('answer').textContent=data.text; $('verification').textContent='生成中的草稿，尚未通过引用与支持关系核验。'; }
      if (name==='final') { render(data); finalSeen=true; }
      if (name==='error') throw Error(data.message);
    });
    if (!finalSeen) throw Error('未收到最终结果。');
  } catch (error) {
    $('status').textContent=error.name==='AbortError'?'已取消':'服务异常';
    $('answer').textContent=error.name==='AbortError'?'本次请求已取消。':error.message;
    $('verification').textContent=''; $('citations').replaceChildren();
  } finally {
    controller=null; $('submit').disabled=false; $('reset').disabled=false; $('cancel').hidden=true;
    $('submit').textContent='查找证据并回答';
  }
});
initialize();
