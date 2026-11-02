const test=require('node:test');
const assert=require('node:assert/strict');
const g=require('./annotation_editor.js');
test('drag directions and boundaries map to original image coordinates',()=>{
  assert.deepEqual(g.rectangle([1900,1000],[100,50],1920,1080),[100,50,1900,1000]);
  assert.deepEqual(g.rectangle([-10,50],[2100,1300],1920,1080),[0,50,1920,1080]);
  assert.equal(g.rectangle([1,1],[1,100],1920,1080),null);
});
test('movement preserves size and stays within the image',()=>{
  assert.deepEqual(g.move([10,20,50,70],-100,999,200,100),[0,50,40,100]);
});
test('resize uses opposite corner and supports crossing it',()=>{
  assert.deepEqual(g.resize([10,20,50,70],'nw',[0,0],200,100),[0,0,50,70]);
  assert.deepEqual(g.resize([10,20,50,70],'se',[5,10],200,100),[5,10,10,20]);
});
