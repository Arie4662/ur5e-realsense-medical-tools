"""Temporary images and annotations only; no robot, camera or real labels."""
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from PIL import Image
from annotation_workspace import AnnotationWorkspace, CLASSES
from annotation_server import make_server


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'source'; self.source.mkdir()
        self.before = {}
        for c in CLASSES:
            folder = self.source/c['name']; folder.mkdir()
            for n in range(2):
                p = folder/('image_%03d.jpg' % n)
                im = Image.new('RGB', (200, 100), (c['id']*40,n*50,10))
                im.save(p, quality=80)
                self.before[p] = hashlib.sha256(p.read_bytes()).hexdigest()
        self.ws = AnnotationWorkspace(self.source, self.root/'output')
        self.items = self.ws.catalog()['items']

    def tearDown(self):
        for path, digest in self.before.items():
            # Except the explicit source mutation test.
            if path.exists() and not getattr(self, 'changed_source', False):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.temp.cleanup()

    def payload(self, item=None, **changes):
        item = item or self.items[0]
        return dict(id=item['id'], revision=0, status='reviewed', split='train',
                    boxes=[{'class_id':2,'xyxy':[20,10,120,60]}],
                    confirmed=True, **changes)

    def test_revision_history_restart_and_source_unchanged(self):
        value = self.ws.save(self.payload())
        self.assertEqual(value['revision'],1)
        with self.assertRaisesRegex(ValueError,'另一個頁面'): self.ws.save(self.payload())
        second = self.ws.save({**self.payload(),'revision':1,'status':'draft'})
        self.assertEqual(second['revision'],2)
        history = list((self.ws.root/'history'/value['id']).glob('*.json'))
        self.assertEqual(len(history),2)
        self.assertTrue(any(json.loads(p.read_text())['revision']==1 for p in history))
        restarted = AnnotationWorkspace(self.source,self.ws.root)
        self.assertEqual(restarted.annotation(value['id']),second)

    def test_invalid_boxes_and_accidental_empty_reviews_rejected(self):
        for coords in ([-1,0,10,10],[0,0,201,20],[5,1,5,10],[0,0,float('nan'),20],[0,0,True,20]):
            with self.assertRaises(ValueError): self.ws.save({**self.payload(),'boxes':[{'class_id':2,'xyxy':coords}]})
        for changes in ({'confirmed':False},{'boxes':[]},{'negative':True},
                        {'boxes':[{'class_id':5,'xyxy':[0,0,20,20]}]}, {'split':'../../escape'}, {'id':'../private'}):
            with self.assertRaises(ValueError): self.ws.save({**self.payload(),**changes})
        saved = self.ws.save({**self.payload(),'boxes':[],'negative':True})
        self.assertEqual(saved['status'],'reviewed')

    def test_export_filters_normalizes_maps_and_keeps_unique_names(self):
        self.ws.save(self.payload(self.items[0]))
        self.ws.save({**self.payload(self.items[1]),'status':'draft'})
        self.ws.save({**self.payload(self.items[2]),'status':'skipped'})
        self.ws.save({**self.payload(self.items[4]),'split':'val',
                      'boxes':[{'class_id':1,'xyxy':[10,20,50,40]}, {'class_id':0,'xyxy':[60,20,100,80]}]})
        self.ws.save({**self.payload(self.items[8]),'boxes':[],'negative':True})
        result = self.ws.export('all')
        target = Path(result['path'])
        self.assertEqual(result['images'],3)
        self.assertEqual(result['counts'],dict(train=2,val=1,test=0))
        self.assertTrue(result['training_ready'])
        labels = list((target/'labels').rglob('*.txt'))
        self.assertEqual(len(labels),3)
        self.assertEqual(len({p.name for p in labels}),3)
        self.assertIn('2 0.35000000 0.35000000 0.50000000 0.50000000\n',[p.read_text() for p in labels])
        hemostat = self.ws.export('hemostat')
        hlabels = list((Path(hemostat['path'])/'labels').rglob('*.txt'))
        self.assertEqual(hemostat['boxes'],2)
        self.assertTrue(all(line.startswith('0 ') for p in hlabels for line in p.read_text().splitlines()))
        self.assertTrue(any(not p.read_text() for p in hlabels))
        self.assertEqual(len(list((self.ws.root/'exports').iterdir())),2)

    def test_empty_export_unknown_images_and_changed_source(self):
        with self.assertRaises(ValueError): self.ws.export('all')
        with self.assertRaises(ValueError): self.ws.image('../../secret')
        with self.assertRaises(ValueError): self.ws.annotation('unknown')
        p=self.source/self.items[0]['file'];p.write_bytes(b'changed');self.changed_source=True
        with self.assertRaisesRegex(ValueError,'原始圖片已變更'): self.ws.save(self.payload())
        self.assertEqual(self.ws.annotations,{})

    def test_loopback_api_auth_image_save_and_export(self):
        server=make_server(self.ws,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin='http://127.0.0.1:%d'%server.server_port
        try:
            html=urlopen(origin+'/').read().decode()
            token=re.search("ANNOTATION_TOKEN='([^']+)'",html).group(1)
            catalog=json.load(urlopen(origin+'/api/catalog'))
            self.assertEqual(len(catalog['items']),10)
            self.assertEqual(urlopen(origin+'/api/image?id='+self.items[0]['id']).read(),self.ws.image(self.items[0]['id']))
            def post(path,data,**headers):
                req=Request(origin+path,data=json.dumps(data).encode(),headers={
                    'Origin':origin,'X-Annotation-Token':token,'Content-Type':'application/json',**headers})
                try:
                    with urlopen(req) as r:return r.status,json.loads(r.read())
                except HTTPError as e:
                    e.close();return e.code,None
            self.assertEqual(post('/api/save',self.payload(),Origin='http://foreign.invalid')[0],403)
            self.assertEqual(post('/api/save',self.payload(),**{'X-Annotation-Token':'wrong'})[0],403)
            self.assertEqual(post('/api/save',self.payload())[0],200)
            self.assertEqual(post('/api/save',self.payload())[0],409)
            code,result=post('/api/export',{'mode':'hemostat'})
            self.assertEqual(code,200);self.assertEqual(result['images'],1)
            self.assertFalse(result['training_ready'])
            self.assertEqual(post('/robot/arm',{})[0],404)
        finally:server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main()
