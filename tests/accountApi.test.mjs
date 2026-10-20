import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
const source=readFileSync(new URL('../src/services/accountApi.ts',import.meta.url),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {amount,accountPeriod,accountGet}=await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

test('monetary strings retain exact precision and actual deposit currency',()=>{
  assert.equal(amount('123456789012345678.12345678','GBP'),'123456789012345678.12345678 GBP');
  assert.equal(amount('0','JPY'),'0 JPY');
  assert.equal(amount(null,'EUR'),'Unavailable');
  assert.equal(amount('5',null),'5 (currency unavailable)');
});
test('performance filters normalize timezone and reject invalid ranges',()=>{
  const params=accountPeriod('2026-10-19T12:00:00+02:00','2026-10-20T12:00:00+02:00');
  assert.equal(params.get('since'),'2026-10-19T10:00:00.000Z');
  assert.throws(()=>accountPeriod('invalid','2026-10-20'));
  assert.throws(()=>accountPeriod('2026-10-20','2026-10-19'));
  assert.throws(()=>accountPeriod('2025-01-01','2026-01-01'));
});
test('account calls remain GET-only and preserve abort signal',async()=>{
  const original=globalThis.fetch;const controller=new AbortController();
  globalThis.fetch=async(url,options)=>{
    assert.equal(url,'/api/account/overview');assert.equal(options.method,undefined);
    assert.equal(options.signal,controller.signal);
    return {ok:true,json:async()=>({currency:'CHF',equity:null})};
  };
  try { assert.deepEqual(await accountGet('overview',controller.signal),{currency:'CHF',equity:null}); }
  finally { globalThis.fetch=original; }
});
test('disconnection and rate-limit errors are surfaced, never replaced with zero data',async()=>{
  const original=globalThis.fetch;
  globalThis.fetch=async()=>({ok:false,status:503,json:async()=>({detail:'Broker disconnected'})});
  try { await assert.rejects(accountGet('overview'),/Broker disconnected/); }
  finally { globalThis.fetch=original; }
});
test('non-JSON service failures still produce a useful unavailable state',async()=>{
  const original=globalThis.fetch;
  globalThis.fetch=async()=>({ok:false,status:502,json:async()=>{throw new Error('not JSON');}});
  try { await assert.rejects(accountGet('positions'),/502/); }
  finally { globalThis.fetch=original; }
});
