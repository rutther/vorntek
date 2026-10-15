export function filterPages(pages,query){
 const term=query.trim().toLocaleLowerCase();
 return pages.filter(p=>`${p.title} ${pageLabel(p)} ${p.path} ${p.locale} ${localeLabel(p.locale)}`.toLocaleLowerCase().includes(term));
}
export function pageLabel(page){
 if(page.path==='/')return '首页';
 if(page.path==='/contact/')return '联系页';
 return (page.title||page.path).split(/\s+[|｜]\s+/)[0];
}
export function localeLabel(locale){return ({en:'英语',zh:'中文','zh-CN':'简体中文',ar:'阿拉伯语',fr:'法语',es:'西班牙语',pt:'葡萄牙语',ru:'俄语'})[locale]||locale;}
export function normalizePath(value){
 const path=value.trim();
 if(!path.startsWith('/')||path.startsWith('//')||/[\\?#*\s\u0000-\u0020]/.test(path)||path.length>500||path.includes('%')||path.includes('//')||path.split('/').some(s=>s==='.'||s==='..'))throw Error('请输入本站路径，例如 /contact/；不要填写域名、参数或通配符。');
 return path==='/'?'/':path.replace(/\/+$/,'')+'/';
}
export function mergePaths(paths,text){
 const result=[...new Set([...paths,...text.split('\n').map(x=>x.trim()).filter(Boolean)].map(normalizePath))];
 if(result.length>30)throw Error('每组最多选择30个页面。');
 return result;
}
export function validatePixelValues(scope,catalog,forms){
 const errors={};
 const pages=new Map((catalog?.pages||[]).map(p=>[p.path,p]));
 for(const key of ['landingPaths','leadPaths']){
  if(!scope[key]?.length)errors[key]='必填，请至少选择1个页面。';
  else if(scope[key].length>30)errors[key]='最多选择30个页面。';
  else if(catalog?.available&&scope[key].some(p=>!pages.has(p)))errors[key]='包含未发布或已下线的路径，请核对并修改。';
 }
 const form=forms.find(f=>String(f.id)===String(scope.formId));
 if(!form)errors.formId='必填，请选择本站可用的网站询盘表单。';
 if(form&&catalog?.available&&scope.leadPaths?.some(p=>!pages.get(p)?.formCodes.includes(form.code)))errors.leadPaths='所选页面没有对应询盘表单，请选择实际提交询盘的页面。';
 if(!catalog?.available)errors.catalog='网站页面目录暂不可用，可暂存草稿，稍后刷新目录再保存。';
 return errors;
}
