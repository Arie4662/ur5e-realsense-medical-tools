'use strict';
// Coordinates always refer to the original JPEG, regardless of CSS zoom.
const Geometry = {
  rectangle(a,b,w,h) {
    const clamp=(v,limit)=>Math.min(limit,Math.max(0,v));
    const x1=clamp(Math.min(a[0],b[0]),w), y1=clamp(Math.min(a[1],b[1]),h);
    const x2=clamp(Math.max(a[0],b[0]),w), y2=clamp(Math.max(a[1],b[1]),h);
    return x2-x1>=2 && y2-y1>=2 ? [x1,y1,x2,y2] : null;
  },
  move(box,dx,dy,w,h) {
    dx=Math.min(w-box[2],Math.max(-box[0],dx));dy=Math.min(h-box[3],Math.max(-box[1],dy));
    return [box[0]+dx,box[1]+dy,box[2]+dx,box[3]+dy];
  },
  resize(box,corner,point,w,h) {
    const fixed=[corner.includes('w')?box[2]:box[0],corner.includes('n')?box[3]:box[1]];
    return this.rectangle(fixed,point,w,h);
  }
};
if(typeof module!=='undefined') module.exports=Geometry;
if(typeof document!=='undefined') {
const $=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg';
const statusNames={unreviewed:'尚未標註',draft:'人工草稿',reviewed:'已確認完成',skipped:'暫略',auto_pending:'自動框選 · 待確認',needs_manual:'需人工框選'};
const colors=['#ffd783','#ff9f9f','#70e6b3','#9ecaff','#dbb3ff'];
let catalog=null, visible=[], current=null, annotation=null, boxes=[], selected=-1;
let mode='draw', dirty=false, busy=false, loaded=false, gesture=null, history=[];
let oldCategory='2', oldState='pending', epoch=0;
function message(text,error=false){$('status').textContent=text;$('status').className=error?'error':'';}
async function api(url,data){const r=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(30000),
  ...(data?{method:'POST',headers:{'Content-Type':'application/json','X-Annotation-Token':ANNOTATION_TOKEN},body:JSON.stringify(data)}:{})});
  const type=r.headers.get('content-type')||'';if(!type.includes('json'))throw Error('服務已更新或無法連線，請儲存備份後重新載入頁面。');
  const result=await r.json();if(!r.ok)throw Error(result.error||'請求失敗');return result;}
const clone=v=>JSON.parse(JSON.stringify(v));
function remember(){history.push({boxes:clone(boxes),negative:$('negative').checked});if(history.length>60)history.shift();}
function changed(){dirty=true;$('confirmed').checked=false;$('saveState').textContent='有尚未儲存的修改';controls();}
function controls(){const disabled=busy||!current||!loaded;
  for(const id of ['drawMode','selectMode','zoom','classId','split','note','negative','confirmed','draft','skip'])$(id).disabled=disabled;
  $('negative').disabled=disabled||boxes.length>0;
  $('complete').disabled=disabled||!$('confirmed').checked||(!boxes.length&&!$('negative').checked);
  $('discard').disabled=disabled||!dirty;$('undo').disabled=disabled||!history.length;
  $('editorFields').disabled=disabled||selected<0;
  for(const id of ['categoryFilter','stateFilter','imageSelect','prev','next','refresh'])$(id).disabled=busy;
  const at=visible.findIndex(x=>x.id===current?.id);$('prev').disabled=busy||at<=0;$('next').disabled=busy||at<0||at>=visible.length-1;
  $('export').disabled=busy||dirty||!catalog?.items.some(x=>x.status==='reviewed');
}
function updateCatalog(){const items=catalog.items, done=items.filter(x=>x.status==='reviewed').length;
  $('progress').textContent=`${done} / ${items.length}`;$('progressBar').value=done;$('progressBar').max=items.length;
  $('counts').textContent=`自動待確認 ${items.filter(x=>x.status==='auto_pending').length} · 人工草稿 ${items.filter(x=>x.status==='draft').length} · 需人工框選 ${items.filter(x=>x.status==='needs_manual').length}`;
  $('classificationSummary').textContent=`照片分類已依資料夾設定：${items.length} 張 · ${catalog.classes.length} 類。下方進度為框選覆核進度。`;
}
function filter(){const state=$('stateFilter').value;visible=catalog.items.filter(x=>(!$('categoryFilter').value||x.source_class===Number($('categoryFilter').value))&&(!state||x.status===state||(state==='pending'&&['draft','auto_pending','needs_manual','unreviewed'].includes(x.status))||(state==='priority'&&['auto_pending','needs_manual'].includes(x.status)&&x.priority==='high')));
  $('imageSelect').replaceChildren(...visible.map(x=>{const o=document.createElement('option');o.value=x.id;o.textContent=`${x.file.split('/').pop()} · ${statusNames[x.status]}${x.priority==='high'&&['auto_pending','needs_manual'].includes(x.status)?' ⚑':''}`;return o;}));
  if(current)$('imageSelect').value=current.id;
  $('position').textContent=`${Math.max(0,visible.findIndex(x=>x.id===current?.id)+1)} / ${visible.length} 張`;controls();
}
function canLeave(){if(dirty){message('這張尚未儲存。請先儲存草稿、確認完成，或放棄修改後再切換。',true);return false;}return !busy;}
function setMode(value){mode=value;$('drawMode').setAttribute('aria-pressed',String(mode==='draw'));$('selectMode').setAttribute('aria-pressed',String(mode==='select'));$('stage').dataset.mode=mode;}
function svgElement(tag,attrs){const el=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))el.setAttribute(k,v);return el;}
function render(){if(!current)return;$('overlay').replaceChildren();
  const scale=current.width/Math.max(1,$('stage').getBoundingClientRect().width), radius=5*scale;
  boxes.forEach((b,i)=>{const [x1,y1,x2,y2]=b.xyxy,color=colors[b.class_id];
    const rect=svgElement('rect',{x:x1,y:y1,width:x2-x1,height:y2-y1,fill:i===selected?'#ffffff18':'#00000005',stroke:color,'stroke-width':i===selected?3:2,'vector-effect':'non-scaling-stroke','data-index':i});$('overlay').append(rect);
    const text=svgElement('text',{x:x1+3*scale,y:Math.max(15*scale,y1-6*scale),fill:color,'font-size':13*scale,'font-weight':600,'paint-order':'stroke',stroke:'#10161e','stroke-width':3*scale,'pointer-events':'none'});text.textContent=`${i+1} ${catalog.classes[b.class_id].name}`;$('overlay').append(text);
    if(i===selected&&mode==='select')for(const [corner,x,y] of [['nw',x1,y1],['ne',x2,y1],['sw',x1,y2],['se',x2,y2]])$('overlay').append(svgElement('rect',{x:x-radius,y:y-radius,width:radius*2,height:radius*2,fill:color,stroke:'#10161e','stroke-width':scale,'data-index':i,'data-corner':corner}));
  });
  $('boxes').replaceChildren(...boxes.map((b,i)=>{const button=document.createElement('button');button.className='box-item';button.setAttribute('aria-pressed',String(i===selected));button.textContent=`${i+1}. ${catalog.classes[b.class_id].name} · ${Math.round(b.xyxy[2]-b.xyxy[0])} × ${Math.round(b.xyxy[3]-b.xyxy[1])}`;button.onclick=()=>{if(busy)return;selected=i;setMode('select');render();};return button;}));
  if(!boxes.length){const p=document.createElement('p');p.className='muted';p.textContent='尚無框選。在圖片拖曳新增。';$('boxes').append(p);}
  if(selected>=0&&boxes[selected]){$('selectedClass').value=String(boxes[selected].class_id);['x1','y1','x2','y2'].forEach((id,i)=>$(id).value=String(Math.round(boxes[selected].xyxy[i]*1000)/1000));}
  controls();
}
async function openImage(item,force=false){if(!force&&!canLeave())return;if(!item){current=null;loaded=false;$('stage').style.display='none';$('empty').hidden=false;$('fileTitle').textContent='沒有符合條件的圖片';$('boxes').replaceChildren();message('可切換資料夾或標註狀態。');controls();return;}
  busy=true;loaded=false;controls();const requestEpoch=++epoch;
  try{const saved=await api('/api/annotation?id='+item.id);const image=new Image();image.src='/api/image?id='+item.id;
    await image.decode();if(requestEpoch!==epoch)return;
    current=item;annotation=saved;boxes=clone(saved.boxes);selected=-1;dirty=false;gesture=null;history=[];
    $('photo').setAttribute('href',image.src);$('photo').setAttribute('width',item.width);$('photo').setAttribute('height',item.height);$('stage').setAttribute('viewBox',`0 0 ${item.width} ${item.height}`);
    $('stage').style.display='block';$('empty').hidden=true;$('zoom').value='1';$('stage').style.width='100%';$('viewport').scrollTop=0;$('viewport').scrollLeft=0;
    $('fileTitle').textContent=item.file;$('itemStatus').textContent=statusNames[saved.status];$('dimensions').textContent=`原圖 ${item.width} × ${item.height} px · ${boxes.length} 個框`;
    $('imageClass').textContent=`照片分類：${catalog.classes[item.source_class].name}（依資料夾指定）`;
    $('classId').value=String(item.source_class);$('split').value=saved.split;$('note').value=saved.note;$('negative').checked=saved.negative;$('confirmed').checked=false;$('saveState').textContent=saved.revision?`已儲存 · 第 ${saved.revision} 版`:saved.provenance==='automatic'?'自動候選已載入，待確認':'尚未標註';
    const hint=saved.suggestion;
    $('suggestionInfo').hidden=!hint;
    if(hint)$('suggestionInfo').textContent=`${saved.provenance==='automatic'?'自動候選框的位置待確認。':'此框選起始於自動候選。'}照片分類已依資料夾指定，候選框沿用此類別。請調整框的位置與範圍；若照片還有其他器械，請分別補框並指定類別。`+(hint.flags.length?'\n優先檢查：'+hint.flags.join('；'):'');
    setMode(boxes.length?'select':'draw');loaded=true;filter();render();message(saved.status==='auto_pending'?'可直接選取並調整候選框；確認全部器械正確後，勾選核對並按「確認完成並下一張」。':saved.status==='needs_manual'?'這張未找到可靠候選，請人工框選。沒有候選框不代表負樣本。':'選擇類別後，在圖片上拖曳框出器械。');
  }catch(e){loaded=false;message('無法載入圖片：'+e.message,true);}finally{busy=false;controls();}}
function point(e){const matrix=$('stage').getScreenCTM();if(!matrix)return [0,0];const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(matrix.inverse());return [Math.max(0,Math.min(current.width,p.x)),Math.max(0,Math.min(current.height,p.y))];}
function cancelGesture(){if(!gesture)return;boxes=gesture.before;selected=-1;gesture=null;render();}
$('stage').onpointerdown=e=>{if(e.button!==0||busy||!loaded||gesture)return;e.preventDefault();const p=point(e),before=clone(boxes);
  if(mode==='select'){const index=Number(e.target.getAttribute('data-index'));if(!e.target.hasAttribute('data-index')){selected=-1;render();return;}selected=index;gesture={kind:e.target.getAttribute('data-corner')||'move',start:p,before,index,base:[...boxes[index].xyxy]};}
  else gesture={kind:'draw',start:p,before,index:boxes.length};
  $('stage').setPointerCapture(e.pointerId);render();};
$('stage').onpointermove=e=>{if(!gesture)return;const p=point(e),g=gesture;let coords;
  if(g.kind==='draw')coords=Geometry.rectangle(g.start,p,current.width,current.height);
  else if(g.kind==='move')coords=Geometry.move(g.base,p[0]-g.start[0],p[1]-g.start[1],current.width,current.height);
  else coords=Geometry.resize(g.base,g.kind,p,current.width,current.height);
  if(coords){if(g.kind==='draw'){boxes[g.index]={class_id:Number($('classId').value),xyxy:coords};selected=g.index;}else boxes[g.index].xyxy=coords;render();}};
$('stage').onpointerup=e=>{if(!gesture)return;$('stage').onpointermove(e);const before=gesture.before;gesture=null;if(JSON.stringify(before)!==JSON.stringify(boxes)){history.push({boxes:before,negative:$('negative').checked});if(history.length>60)history.shift();$('negative').checked=false;changed();message(`已框選 ${boxes.length} 件器械。核對類別與範圍後再儲存。`);}$('dimensions').textContent=`原圖 ${current.width} × ${current.height} px · ${boxes.length} 個框`;render();};
$('stage').onpointercancel=cancelGesture;window.addEventListener('blur',cancelGesture);
$('drawMode').onclick=()=>{setMode('draw');render();};$('selectMode').onclick=()=>{setMode('select');render();};$('zoom').onchange=()=>{$('stage').style.width=(Number($('zoom').value)*100)+'%';render();};
$('undo').onclick=()=>{if(!history.length||busy)return;const last=history.pop();boxes=last.boxes;$('negative').checked=last.negative;selected=-1;changed();render();};
$('deleteBox').onclick=()=>{if(selected<0||busy)return;remember();boxes.splice(selected,1);selected=-1;changed();render();};
$('selectedClass').onchange=()=>{if(selected<0)return;remember();boxes[selected].class_id=Number($('selectedClass').value);changed();render();};
for(const id of ['x1','y1','x2','y2'])$(id).onchange=()=>{if(selected<0)return;const coords=['x1','y1','x2','y2'].map(k=>Number($(k).value));const [a,b,c,d]=coords;
  if(coords.some(v=>!Number.isFinite(v))||a<0||b<0||c>current.width||d>current.height||c-a<2||d-b<2){message('座標超出圖片，或框的寬高小於 2 像素。',true);render();return;}
  remember();boxes[selected].xyxy=coords;changed();render();};
for(const id of ['split','negative'])$(id).onchange=()=>changed();$('note').oninput=()=>changed();$('confirmed').onchange=controls;
async function save(status,next=false){if(busy||!current||!loaded)return;const id=current.id;const index=visible.findIndex(x=>x.id===id),nextId=visible[index+1]?.id;
  busy=true;controls();try{const saved=await api('/api/save',{id,revision:annotation.revision,status,boxes,split:$('split').value,note:$('note').value,negative:$('negative').checked,confirmed:$('confirmed').checked});
    annotation=saved;dirty=false;history=[];Object.assign(catalog.items.find(x=>x.id===id),{status:saved.status,split:saved.split,revision:saved.revision,box_count:saved.boxes.length});
    $('itemStatus').textContent=statusNames[saved.status];$('saveState').textContent=`已儲存 · 第 ${saved.revision} 版`;$('confirmed').checked=false;updateCatalog();filter();message(`已儲存${statusNames[status]}，共 ${saved.boxes.length} 個框。`);
    if(next){busy=false;const target=visible.find(x=>x.id===nextId)||visible.find(x=>x.id!==id&&x.status!=='reviewed');if(target)await openImage(target);else message('已儲存。目前篩選範圍沒有下一張待標註圖片。');}
  }catch(e){message('儲存失敗：'+e.message,true);}finally{busy=false;controls();}}
$('draft').onclick=()=>save('draft');$('complete').onclick=()=>save('reviewed',true);$('skip').onclick=()=>save('skipped',true);
$('discard').onclick=()=>{if(!busy)openImage(current,true);};
$('refresh').onclick=async()=>{if(!canLeave())return;busy=true;controls();try{catalog=await api('/api/catalog');updateCatalog();filter();busy=false;await openImage(visible.find(x=>x.id===current?.id)||visible.find(x=>x.status==='auto_pending')||visible[0]);}catch(e){message(e.message,true);}finally{busy=false;controls();}};
for(const id of ['categoryFilter','stateFilter'])$(id).onchange=()=>{if(!canLeave()){$('categoryFilter').value=oldCategory;$('stateFilter').value=oldState;return;}oldCategory=$('categoryFilter').value;oldState=$('stateFilter').value;filter();openImage(visible[0]);};
$('imageSelect').onchange=()=>{const target=visible.find(x=>x.id===$('imageSelect').value);if(!canLeave()){$('imageSelect').value=current?.id||'';return;}openImage(target);};
$('prev').onclick=()=>openImage(visible[visible.findIndex(x=>x.id===current?.id)-1]);$('next').onclick=()=>openImage(visible[visible.findIndex(x=>x.id===current?.id)+1]);
$('export').onclick=async()=>{if(busy)return;busy=true;controls();$('exportResult').textContent='正在匯出已確認的標註…';try{const r=await api('/api/export',{mode:$('exportMode').value});$('exportResult').textContent=`已匯出 ${r.images} 張圖片、${r.boxes} 個框。\n訓練 ${r.counts.train} · 驗證 ${r.counts.val} · 測試 ${r.counts.test}\n${r.path}\n${r.training_ready?'已包含訓練與驗證資料；仍需檢查標註品質與拍攝批次是否獨立。':'目前匯出尚缺訓練集、驗證集或有效框選，補齊後再訓練。'}`;}catch(e){$('exportResult').textContent='匯出失敗：'+e.message;}finally{busy=false;controls();}};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
window.addEventListener('keydown',e=>{const typing=['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName);if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='s'){e.preventDefault();save('draft');return;}if(typing||busy)return;
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='z'){e.preventDefault();$('undo').click();}else if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();$('deleteBox').click();}else if(e.key.toLowerCase()==='n')$('drawMode').click();else if(e.key.toLowerCase()==='v')$('selectMode').click();else if(e.key==='Escape')cancelGesture();});
new ResizeObserver(()=>{if(loaded)render();}).observe($('viewport'));
(async()=>{try{catalog=await api('/api/catalog');for(const id of ['classId','selectedClass','categoryFilter'])for(const c of catalog.classes){const o=document.createElement('option');o.value=String(c.id);o.textContent=c.name;$(id).append(o);}$('categoryFilter').value=oldCategory;$('stateFilter').value=oldState;updateCatalog();filter();await openImage(visible.find(x=>x.status==='auto_pending')||visible.find(x=>x.status!=='reviewed')||visible[0]);}catch(e){message('無法載入資料集：'+e.message,true);}})();
}
