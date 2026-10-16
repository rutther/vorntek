import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../',import.meta.url));
const pack=JSON.parse(fs.readFileSync(path.join(root,'fixtures/guide-figures.json'),'utf8'));
const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
function wrap(value,limit){
 const lines=[]; let line='',width=0;
 for(const char of String(value)){
  const size=char.codePointAt(0)>255?2:1;
  if(width+size>limit){lines.push(line);line='';width=0;}
  line+=char;width+=size;
 }
 if(line)lines.push(line);
 return lines;
}
function textLines(lines,x,y,{size=18,color='#243550',weight=400,gap=27}={}){
 return `<text x="${x}" y="${y}" fill="${color}" font-size="${size}" font-weight="${weight}">${lines.map((line,i)=>`<tspan x="${x}" dy="${i?gap:0}">${escape(line)}</tspan>`).join('')}</text>`;
}
for(const figure of pack.figures){
 if(!/^[a-z0-9-]+$/.test(figure.name)||!['source','help'].includes(figure.bucket))throw new Error('Invalid figure destination');
 const rows=figure.rows.map(([label,value])=>({label,value,lines:wrap(value,49)}));
 const heights=rows.map(row=>Math.max(76,row.lines.length*27+30));
 const rowsHeight=heights.reduce((a,b)=>a+b,0);
 const nextLines=wrap(figure.next,86);
 const height=Math.max(680,rowsHeight+312+nextLines.length*25);
 let y=205;
 const content=rows.map((row,index)=>{
  const active=figure.focus.includes(index+1),rowHeight=heights[index];
  const drawing=`<rect x="234" y="${y}" width="830" height="${rowHeight-8}" rx="9" fill="${active?'#fff5f5':'#fff'}" stroke="${active?'#df5555':'#e0e6ef'}" stroke-width="${active?2:1}"/>`+
   `<circle cx="260" cy="${y+31}" r="12" fill="${active?'#2563eb':'#e8edf5'}"/>`+
   `<text x="260" y="${y+36}" text-anchor="middle" font-size="14" fill="${active?'#fff':'#63738c'}">${index+1}</text>`+
   textLines(wrap(row.label,18),283,y+36,{size:16,color:'#52647e',weight:600,gap:24})+
   textLines(row.lines,485,y+36,{size:18,weight:active?600:400});
  y+=rowHeight;return drawing;
 }).join('');
 const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="${height}" viewBox="0 0 1100 ${height}" role="img" aria-labelledby="title description">
 <title id="title">${escape(figure.title)}</title><desc id="description">${escape(pack.notice)} ${escape(figure.next)}</desc>
 <rect width="1100" height="${height}" rx="14" fill="#f5f7fb"/>
 <g font-family="Microsoft YaHei,PingFang SC,Noto Sans CJK SC,Arial,sans-serif">
 <path d="M0 14Q0 0 14 0H1086Q1100 0 1100 14V81H0Z" fill="#fff"/>
 <text x="28" y="48" font-size="23" font-weight="700" fill="#152846">Vorntek · 操作指引</text>
 <rect x="790" y="22" width="280" height="36" rx="18" fill="#fff2d6"/>
 <text x="930" y="46" text-anchor="middle" font-size="17" font-weight="600" fill="#82520b">示例图 · 非实时账号状态</text>
 <line x1="0" y1="81" x2="1100" y2="81" stroke="#dde4ef"/>
 <rect x="24" y="108" width="180" height="${height-138}" rx="10" fill="#eaf0f9"/>
 <text x="44" y="145" font-size="17" font-weight="700" fill="#244061">核对路径</text>
 ${textLines(figure.context.split(' / '),44,189,{size:16,color:'#405977',gap:36})}
 <rect x="42" y="${height-159}" width="143" height="80" rx="8" fill="#fff"/>
 ${textLines(['使用自己的','数据集与网址'],56,height-127,{size:16,color:'#405977',weight:600,gap:26})}
 ${textLines(wrap(figure.title,48),234,139,{size:26,color:'#162b4a',weight:700,gap:34})}
 <text x="234" y="178" font-size="16" fill="#62758f">按红框逐项核对，完成后返回本站登记实际结果。</text>
 ${content}
 <rect x="234" y="${y+8}" width="830" height="${nextLines.length*25+44}" rx="9" fill="#e8f0ff"/>
 ${textLines(nextLines,253,y+39,{size:17,color:'#234d8b',gap:25})}
 <text x="234" y="${height-22}" font-size="14" fill="#657790">平台界面可能更新；以实际页面为准。示例内容不能作为当前接收、匹配或去重证据。</text>
 </g></svg>\n`;
 const folder=figure.bucket==='source'?path.join(root,'src/assets'):path.join(root,'../console/static/console/meta-help');
 fs.mkdirSync(folder,{recursive:true});
 fs.writeFileSync(path.join(folder,figure.name+'.svg'),svg);
}
console.log(`Generated ${pack.figures.length} original, customer-neutral guide figures.`);
