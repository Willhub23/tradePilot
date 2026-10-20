import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const source=readFileSync(new URL('../src/services/journalApi.ts',import.meta.url),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const {journalQuery,journalGet}=await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

test('filters encode UTC instants and exclude unavailable values',()=>{
  const params=journalQuery({since:'2026-10-20T10:00:00+02:00',until:'',outcome:'rejected',setup:'range_support_bounce',direction:'short'});
  assert.equal(params.get('since'),'2026-10-20T08:00:00.000Z');
  assert.equal(params.has('until'),false);
  assert.equal(params.get('direction'),'short');
  assert.equal(params.get('setup'),'range_support_bounce');
});
test('invalid dates cannot silently become unfiltered requests',()=>{
  assert.throws(()=>journalQuery({since:'not-a-date',until:'',outcome:'',setup:'',direction:''}));
});
test('fetch is GET-only and propagates cancellation',async()=>{
  const original=globalThis.fetch;
  const controller=new AbortController();
  globalThis.fetch=async(url,options)=>{
    assert.equal(url,'/api/journal/evaluations?page=2');
    assert.equal(options.signal,controller.signal);
    assert.equal(options.method,undefined);
    return {ok:true,json:async()=>({items:[],total:0})};
  };
  try { assert.deepEqual(await journalGet('evaluations?page=2',controller.signal),{items:[],total:0}); }
  finally { globalThis.fetch=original; }
});
test('API failures are surfaced rather than replaced with demo records',async()=>{
  const original=globalThis.fetch;
  globalThis.fetch=async()=>({ok:false,status:503});
  try { await assert.rejects(journalGet('summary'),/503/); }
  finally { globalThis.fetch=original; }
});
