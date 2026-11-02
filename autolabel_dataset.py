"""Dataset-specific green-cloth proposals, not a trained instrument classifier.

Produces separate, unreviewed suggestions. Never writes human annotations.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import cv2
import numpy as np
from annotation_workspace import AnnotationWorkspace, write_json

METHOD = 'green-cloth-components-v1'
def propose(raw, category):
    if raw is None: raise ValueError("Cannot decode image")
    oh,ow=raw.shape[:2]
    scale=960/ow
    im=cv2.resize(raw,(960,round(oh*scale)),interpolation=cv2.INTER_AREA)
    h,w=im.shape[:2]
    hsv=cv2.cvtColor(im,cv2.COLOR_BGR2HSV)
    green=cv2.inRange(hsv,np.array([36,45,35]),np.array([95,255,255]))
    green=cv2.morphologyEx(green,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
    contours,_=cv2.findContours(green,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    if not contours:return [],{'reason':'no_green'},im
    cloth=max(contours,key=cv2.contourArea)
    if cv2.contourArea(cloth)<w*h*.15:return [],{'reason':'small_green'},im
    roi=np.zeros((h,w),np.uint8)
    cv2.fillConvexPoly(roi,cv2.convexHull(cloth),255)
    roi=cv2.erode(roi,np.ones((13,13),np.uint8))
    # Use the raw green mask here so narrow tool edges aren't closed away.
    sbase=cv2.medianBlur(hsv[:,:,1],51)
    vbase=cv2.medianBlur(hsv[:,:,2],51)
    actualgreen=np.uint8((hsv[:,:,0]>=36)&(hsv[:,:,0]<=95)&(hsv[:,:,1]>=np.minimum(115,sbase*.73))&(hsv[:,:,2]>=np.maximum(20,vbase*.65)))*255
    if category != 4:
        actualgreen=cv2.inRange(hsv,np.array([36,115,25]),np.array([95,255,255]))
    boundary=roi-cv2.erode(roi,np.ones((9,9),np.uint8))
    boundary[:20]=0;boundary[-20:]=0;boundary[:,:20]=0;boundary[:,-20:]=0
    skin=cv2.inRange(hsv,np.array([0,35,35]),np.array([32,255,255]))
    skin=cv2.morphologyEx(skin,cv2.MORPH_OPEN,np.ones((7,7),np.uint8))
    skin=cv2.dilate(skin,np.ones((5,5),np.uint8))
    fg=cv2.bitwise_and(cv2.bitwise_not(actualgreen),roi)
    fg[skin>0]=0
    fg=cv2.morphologyEx(fg,cv2.MORPH_CLOSE,np.ones((11,11),np.uint8))
    n,labels,stats,cents=cv2.connectedComponentsWithStats(fg)
    candidates=[]
    for i in range(1,n):
        x,y,bw,bh,area=stats[i]
        if area<(400 if category==4 else 80) or max(bw,bh)<40 or bw*bh>w*h*.7:continue
        mask=np.uint8(labels==i)*255
        if np.count_nonzero((mask>0)&(boundary>0))>max(8,area*.01):continue
        rim=(cv2.dilate(mask,np.ones((13,13),np.uint8))>0)&(mask==0)
        surrounding=float(np.mean(actualgreen[rim]>0)) if rim.any() else 0
        if surrounding<.55:continue
        central=max(.15,1-np.linalg.norm((cents[i]-np.array([w/2,h/2]))/np.array([w/2,h/2]))*.6)
        score=area*central*surrounding**3
        candidates.append((score,i))
    candidates.sort(reverse=True)
    if not candidates:return [],{'reason':'no_candidate'},im
    _,i=candidates[0];x,y,bw,bh,area=stats[i]
    coords=[x,y,x+bw,y+bh]
    for _,j in candidates[1:]:
        xx,yy,ww,hh,aa=stats[j]
        dx=max(0,coords[0]-(xx+ww),xx-coords[2]);dy=max(0,coords[1]-(yy+hh),yy-coords[3])
        if np.hypot(dx,dy)<35 and aa>area*.08:
            coords=[min(coords[0],xx),min(coords[1],yy),max(coords[2],xx+ww),max(coords[3],yy+hh)]
    x,y,x2,y2=coords;bw=x2-x;bh=y2-y
    box=[int(max(0,x-6)/scale),int(max(0,y-6)/scale),int(min(w,x+bw+6)/scale),int(min(h,y+bh+6)/scale)]
    return [box],{'candidates':len(candidates),'skin':round(float(np.mean((skin>0)&(roi>0))),3),'area':int(area),'edge':bool(x<10 or y<10 or x+bw>w-10 or y+bh>h-10),'multiple':bool(len(candidates)>1 and candidates[1][0]>candidates[0][0]*.25)},im


def proposal_record(entry, raw, run_id):
    image=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
    coords, metrics, _ = propose(image, entry['source_class'])
    flags=[]
    if not coords: flags.append('未找到可靠候選框，需人工框選；不代表沒有器械')
    if metrics.get('skin',0)>.005: flags.append('可能有手部或其他遮擋，請檢查器械邊界')
    if metrics.get('multiple'): flags.append('有多段輪廓或多件物品，請檢查漏框與合併框')
    if metrics.get('edge'): flags.append('器械靠近畫面邊緣，請檢查裁切範圍')
    if entry['source_class']==4: flags.append('長鑷子近拍反光較多，請檢查兩片尖端是否完整')
    return dict(schema=1,id=entry['id'],sha256=entry['sha256'],run_id=run_id,method=METHOD,
        created=time.time(),class_basis='source_folder',priority='high' if flags else 'normal',
        flags=flags,metrics=metrics,
        boxes=[{'class_id':entry['source_class'],'xyxy':b} for b in coords])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=(Path(__file__).resolve().parent / 'data' / 'medical_tools_dataset'))
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'dataset_annotations'/'medical_tools')
    args=parser.parse_args()
    cv2.setNumThreads(2)
    ws=AnnotationWorkspace(args.source,args.output)
    run_id=time.strftime('%Y%m%d-%H%M%S')
    counts={'processed':0,'with_box':0,'needs_manual':0,'priority':0,'preserved_human':0,'errors':0}
    per_class={str(i):dict(with_box=0,needs_manual=0,preserved_human=0) for i in range(5)}
    report={'method':METHOD,'run_id':run_id,'source':str(ws.source),'counts':counts,'classes':per_class,'total':len(ws.entries)}
    for entry in ws.entries.values():
        cls=per_class[str(entry['source_class'])]
        if (ws.root/'annotations'/(entry['id']+'.json')).exists():
            counts['preserved_human']+=1;cls['preserved_human']+=1
            continue
        try:
            raw=ws.image(entry['id'])
            result=proposal_record(entry,raw,run_id)
        except Exception as exc:
            counts['errors']+=1
            result=dict(schema=1,id=entry['id'],sha256=entry['sha256'],run_id=run_id,method=METHOD,
                created=time.time(),class_basis='source_folder',priority='high',boxes=[],
                flags=['自動處理未完成，需人工框選'],error=str(exc))
        # A human may have saved this image while inference was running.
        if (ws.root/'annotations'/(entry['id']+'.json')).exists():
            counts['preserved_human']+=1;cls['preserved_human']+=1
            continue
        destination=ws.root/'proposals'/(entry['id']+'.json')
        if destination.exists():
            old=json.loads(destination.read_text())
            write_json(ws.root/'proposal_history'/entry['id']/(old.get('run_id','previous')+'.json'),old)
        write_json(destination,result)
        counts['processed']+=1
        key='with_box' if result['boxes'] else 'needs_manual'
        counts[key]+=1;cls[key]+=1
        counts['priority']+=int(result['priority']=='high')
        if counts['processed']%50==0:
            report['updated']=time.time();write_json(ws.root/'autolabel-report.json',report)
            print(json.dumps(counts),flush=True)
    report['updated']=time.time();report['complete']=True
    write_json(ws.root/'autolabel-report.json',report)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__': main()
