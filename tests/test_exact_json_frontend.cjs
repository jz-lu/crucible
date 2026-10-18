// Exact challenge bytes must survive the browser; all requests are fixtures.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createApp, flush} = require('./frontend-dom.cjs');
const exact = '{\n "parameters": {"p": 161436514114974235392863579276153526849336702389222926751199169055975156791391, "q": 9007199254740993}, "literal": "9007199254740993"\n}\n';
async function fixture() {
  const app = await createApp({schemes: [], user: {id:'owner',handle:'owner'}, hash:'#about'});
  vm.runInContext("state.challenge = {id:'exact-fixture'}", app.sandbox);
  let copied = null;
  app.sandbox.navigator.clipboard = {writeText: async value => {copied = value;}};
  return {app, copied: () => copied};
}
(async () => {
  {
    const {app,copied} = await fixture();
    app.sandbox.fetch = async (url,options) => {
      assert.equal(url,'/api/challenges/exact-fixture/download');
      assert.equal(options.method,'GET');assert.equal(options.credentials,'same-origin');
      return {ok:true,status:200,text:async()=>exact,json:async()=>{throw Error('Never parse exact bytes');}};
    };
    await app.sandbox.copyInstance();assert.equal(copied(),exact);
    assert.equal(app.$('#copy-instance').disabled,false);
  }
  for (const change of ["state.challenge={id:'another'}", "state.user={id:'another',handle:'another'}"]) {
    const {app,copied} = await fixture();let resolve;
    const pending = new Promise(r=>{resolve=r;});
    app.sandbox.fetch=async()=>({ok:true,status:200,text:async()=>pending});
    const copy=app.sandbox.copyInstance();await flush();
    vm.runInContext(change,app.sandbox);resolve(exact);await copy;
    assert.equal(copied(),null);assert.equal(app.$('#copy-instance').disabled,false);
  }
  {
    const {app,copied} = await fixture();
    app.sandbox.fetch=async()=>({ok:false,status:403,json:async()=>({detail:'Not authorized'}),text:async()=>{throw Error('Never copy an error response');}});
    await app.sandbox.copyInstance();assert.equal(copied(),null);
    assert.equal(app.$('#copy-instance').disabled,false);
  }
  {
    const profile={id:'exact-1',instance_size:1,shared_secret_bytes:32,
      parameters:JSON.parse(exact).parameters,exact_parameter_json:exact,correctness:{kind:'perfect',failure_bound:'0'}};
    const scheme={id:'fixture',version:'1',name:'Fixture',profiles:[profile],next_profile_id:profile.id,protocol:{}};
    const app=await createApp({schemes:[scheme],hash:'#challenge/fixture/1?profile=exact-1&view=protocol'});
    assert.equal(app.$('#parameter-json').textContent,exact);
    assert.equal(app.$('#parameter-json').querySelector('script'),null);
  }
  console.log('PASS exact copying, late-response guards, error handling and parameter display');
})().catch(error=>{console.error(error);process.exitCode=1;});
