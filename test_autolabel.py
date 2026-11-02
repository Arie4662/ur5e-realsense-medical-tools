"""Proposals stay unreviewed and cannot replace human work or enter exports."""
import json
import unittest
import cv2
import numpy as np
import test_annotation_workspace as fixtures
from annotation_workspace import AnnotationWorkspace, write_json
from autolabel_dataset import propose


class AutoLabelTests(unittest.TestCase):
    setUp=fixtures.AnnotationTests.setUp
    tearDown=fixtures.AnnotationTests.tearDown
    payload=fixtures.AnnotationTests.payload

    def suggestion(self, boxes=None, **changes):
        e=self.items[0]
        value=dict(schema=1,id=e['id'],sha256=e['sha256'],method='test',run_id='test',
                   priority='high',flags=['review'],class_basis='source_folder',
                   boxes=boxes if boxes is not None else self.payload()['boxes'])
        value.update(changes)
        write_json(self.ws.root/'proposals'/(e['id']+'.json'),value)
        return e['id']

    def test_proposals_require_human_review_and_preserve_origin(self):
        image_id=self.suggestion(status='reviewed')
        a=self.ws.annotation(image_id)
        self.assertEqual(a['status'],'auto_pending')
        self.assertEqual(a['revision'],0)
        self.assertFalse(a['negative'])
        with self.assertRaises(ValueError):self.ws.export('all')
        saved=self.ws.save({**self.payload(),'status':'draft'})
        self.assertEqual(saved['suggestion']['method'],'test')
        self.suggestion(boxes=[{'class_id':0,'xyxy':[0,0,10,10]}])
        self.assertEqual(self.ws.annotation(image_id)['boxes'],saved['boxes'])
        with self.assertRaises(ValueError):self.ws.export('all')
        self.ws.save({**self.payload(),'revision':1})
        self.assertEqual(self.ws.export('all')['images'],1)

    def test_failed_detection_is_not_a_negative_and_wrong_image_is_ignored(self):
        image_id=self.suggestion(boxes=[])
        a=self.ws.annotation(image_id)
        self.assertEqual(a['status'],'needs_manual');self.assertFalse(a['negative'])
        with self.assertRaises(ValueError):self.ws.save({**self.payload(),'boxes':[]})
        self.suggestion(sha256='wrong')
        self.assertEqual(self.ws.annotation(image_id)['status'],'unreviewed')

    def test_existing_human_work_and_cross_instance_revision_are_preserved(self):
        second=AnnotationWorkspace(self.source,self.ws.root)
        self.ws.save({**self.payload(),'status':'draft'})
        self.suggestion()
        self.assertEqual(second.annotation(self.items[0]['id'])['provenance'],'human')
        with self.assertRaisesRegex(ValueError,'另一個頁面'):second.save(self.payload())

    def test_green_cloth_candidate_geometry_and_no_target_cases(self):
        image=np.full((540,960,3),(80,170,50),np.uint8)
        cv2.rectangle(image,(300,150),(430,400),(180,180,180),-1)
        boxes,metrics,_=propose(image,2)
        self.assertEqual(len(boxes),1)
        x1,y1,x2,y2=boxes[0]
        self.assertTrue(285<=x1<=305 and 135<=y1<=155)
        self.assertTrue(425<=x2<=445 and 395<=y2<=415)
        self.assertEqual(propose(np.full_like(image,128),2)[0],[])
        hand=np.full_like(image,(80,170,50))
        cv2.rectangle(hand,(300,150),(430,400),(80,140,200),-1)
        self.assertEqual(propose(hand,2)[0],[])


if __name__=='__main__':unittest.main()
