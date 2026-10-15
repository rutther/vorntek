import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../../console/static/console/marketing-legacy-entry.js',import.meta.url),'utf8');
test('legacy hash tabs have fixed same-origin destinations, no arbitrary return URL',()=>{
 for(const [hash,target] of [['#integrations','/admin/marketing/integrations/'],['#rules','/admin/marketing/?tab=rules#rules'],['#matching','/admin/marketing/attribution/?tab=matching'],['#https://example.invalid',null],['#constructor',null],['',null]]){
  let actual=null;vm.runInNewContext(source,{window:{location:{hash,replace:value=>{actual=value;}}}});assert.equal(actual,target);
 }
});
