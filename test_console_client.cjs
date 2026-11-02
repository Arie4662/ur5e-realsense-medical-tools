// No browser, network or robot access: exercise actual UI code in a mock DOM.
module.exports = async function testClient(html) {
  const vm = require('node:vm');
  const assert = require('node:assert/strict');
  function setup() {
    const elements = new Map(), requests = [];
    function element(id) {
      if (!elements.has(id)) elements.set(id, {checked:false, disabled:false,
        classList:{add(){},remove(){},toggle(){}},setAttribute(){},append(){}});
      return elements.get(id);
    }
    const context = vm.createContext({
      document:{getElementById:element,createElement:()=>element(Math.random()),
        querySelectorAll:()=>[],hasFocus:()=>true,hidden:false,addEventListener(){}},
      window:{addEventListener(){}},crypto:{randomUUID:()=>String(Math.random())},
      AbortSignal:{timeout:()=>null},setTimeout(){},
      fetch:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve})),
    });
    let script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
    script = script.replace(/update\(\);\s*$/, '');
    vm.runInContext(script,context);
    vm.runInContext(`cameraFresh=true;displayedAt=Date.now()/1000;
      robot={connected:true,armed:false,control_revision:0,
      state:{'is in remote control':'true',safetystatus:'Safetystatus: NORMAL',robotmode:'Robotmode: RUNNING'},
      tcp_offset_m_rad:[0,0,0,0,0,0],tcp_pose_m_rad:[0,0,1,0,0,0]};$('clear').checked=true;`,context);
    async function tick(){for(let i=0;i<12;i++)await Promise.resolve();}
    function respond(req,revision,armed){req.resolve({ok:true,json:async()=>({ok:true,
      robot:{...vm.runInContext('robot',context),armed,control_revision:revision,control_mode:'joint'}})});}
    return {context,elements,requests,tick,respond};
  }
  {
    const h=setup();
    const arming=h.elements.get('arm').onclick();await h.tick();
    assert.equal(h.requests[0].url,'/robot/arm');
    h.respond(h.requests[0],1,true);await arming;
    assert.equal(vm.runInContext('armed',h.context),true);
    const accepted=vm.runInContext('adoptRobot({...robot,armed:false,control_revision:0})',h.context);
    assert.equal(accepted,false);
    assert.equal(vm.runInContext('robot.armed',h.context),true);
  }
  {
    const h=setup();
    const arming=h.elements.get('arm').onclick();await h.tick();
    vm.runInContext("stop('focus lost')",h.context);await h.tick();
    h.respond(h.requests.find(r=>r.url==='/robot/arm'),1,true);
    await h.tick();
    for (let i=0;i<3;i++) {
      const stop=h.requests.find(r=>r.url==='/robot/stop'&&!r.handled);
      if(stop){stop.handled=true;h.respond(stop,i+2,false);await h.tick();}
    }
    await arming;
    assert.equal(vm.runInContext('armed',h.context),false);
    assert.equal(h.requests.some(r=>r.url==='/robot/start'),false);
  }
  {
    const h=setup();
    vm.runInContext("stop('released')",h.context);await h.tick();
    const arming=h.elements.get('arm').onclick();await h.tick();
    assert.equal(h.requests.some(r=>r.url==='/robot/arm'),false);
    h.respond(h.requests[0],1,false);await h.tick();
    const arm=h.requests.find(r=>r.url==='/robot/arm');assert.ok(arm);
    h.respond(arm,2,true);await arming;
    assert.equal(vm.runInContext('armed',h.context),true);
  }
  return '3 UI concurrency checks passed: stale status, release during arm, stop before rearm';
};
