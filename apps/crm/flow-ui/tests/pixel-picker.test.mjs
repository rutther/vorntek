import test from 'node:test';
import assert from 'node:assert/strict';
import {filterPages,mergePaths,validatePixelValues} from '../src/pixel-model.mjs';

const pages=[{path:'/',title:'首页',locale:'en',formCodes:[]},{path:'/contact/',title:'联系我们',locale:'fr',formCodes:['project-inquiry']}];
test('search matches title path and language without mutating selection',()=>{
 const selected=['/','/contact/'];
 assert.equal(filterPages(pages,'联系')[0].path,'/contact/');
 assert.equal(filterPages(pages,'FR').length,1);
 assert.deepEqual(selected,['/','/contact/']);
});
test('manual input normalizes trailing slash and deduplicates',()=>{
 assert.deepEqual(mergePaths(['/contact/'],'/contact\n/'),['/contact/','/']);
 assert.throws(()=>mergePaths([],'https://evil.com'));
});
test('required and actual form placement are enforced without clearing invalid old paths',()=>{
 const catalog={available:true,pages};
 assert.ok(validatePixelValues({landingPaths:[],leadPaths:[],formId:''},catalog,[]) .landingPaths);
 const scope={landingPaths:['/gone/'],leadPaths:['/'],formId:'1'};
 const errors=validatePixelValues(scope,catalog,[{id:1,code:'project-inquiry'}]);
 assert.ok(errors.landingPaths); assert.ok(errors.leadPaths);
 assert.deepEqual(scope.landingPaths,['/gone/']);
});
