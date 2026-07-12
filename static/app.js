function drawChart(canvas) {
  const source = document.getElementById(canvas.dataset.sourceId);
  if (!source) return;
  const points = JSON.parse(source.textContent);
  if (!points.length) return;
  const wrap = canvas.parentElement;
  const tooltip = wrap.querySelector('.chart-tooltip');
  const ctx = canvas.getContext('2d');
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

  const w = rect.width, h = rect.height;
  const pad = {left: 52, right: 18, top: 18, bottom: 34};
  const values = points.map(p => p.change_pct);
  let min = Math.min(...values), max = Math.max(...values);
  if (min === max) { min -= 1; max += 1; }
  const extra = (max - min) * 0.12;
  min -= extra; max += extra;
  const x = i => pad.left + i * (w - pad.left - pad.right) / Math.max(1, points.length - 1);
  const y = v => pad.top + (max - v) * (h - pad.top - pad.bottom) / (max - min);

  ctx.clearRect(0,0,w,h);
  ctx.font = '12px sans-serif';
  ctx.fillStyle = '#7b8794';
  ctx.strokeStyle = '#e8edf1';
  ctx.lineWidth = 1;
  for (let i=0;i<=4;i++) {
    const val = min + (max-min)*i/4;
    const yy = y(val);
    ctx.beginPath();ctx.moveTo(pad.left,yy);ctx.lineTo(w-pad.right,yy);ctx.stroke();
    ctx.fillText(`${val.toFixed(1)}%`, 6, yy+4);
  }
  // const zeroY = y(0);
  // if (zeroY >= pad.top && zeroY <= h-pad.bottom) {
  //   ctx.strokeStyle='#cfd7de';ctx.beginPath();ctx.moveTo(pad.left,zeroY);ctx.lineTo(w-pad.right,zeroY);ctx.stroke();
  // }
  ctx.strokeStyle='#2676a5';ctx.lineWidth=2.2;ctx.beginPath();
  points.forEach((p,i)=>{ const xx=x(i), yy=y(p.change_pct); if(i===0)ctx.moveTo(xx,yy);else ctx.lineTo(xx,yy); });ctx.stroke();
  ctx.fillStyle='#7b8794';
  [0, Math.floor((points.length-1)/2), points.length-1].forEach(i=>ctx.fillText(points[i].date.slice(5), x(i)-18, h-10));

  canvas._chart = {points, x, y, pad, w, h};

  canvas.onmousemove = e => {
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX-r.left;
    const index = Math.max(0,Math.min(points.length-1,Math.round((mx-pad.left)/(w-pad.left-pad.right)*(points.length-1))));
    const p = points[index], xx=x(index), yy=y(p.change_pct);
    drawChart(canvas);
    ctx.setTransform(ratio,0,0,ratio,0,0);
    ctx.strokeStyle='#9aabb8';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(xx,pad.top);ctx.lineTo(xx,h-pad.bottom);ctx.stroke();
    ctx.fillStyle='#2676a5';ctx.beginPath();ctx.arc(xx,yy,4,0,Math.PI*2);ctx.fill();
    tooltip.hidden=false;
    tooltip.innerHTML=`${p.date}<br>价格：${p.price.toFixed(4)}<br>区间涨跌：${p.change_pct.toFixed(2)}%`;
    tooltip.style.left=`${xx}px`;tooltip.style.top=`${yy}px`;
  };
  canvas.onmouseleave=()=>{tooltip.hidden=true;drawChart(canvas);};
}

function drawAllCharts(){document.querySelectorAll('.price-chart').forEach(drawChart)}
document.addEventListener('DOMContentLoaded',drawAllCharts);
window.addEventListener('resize',()=>{clearTimeout(window._chartResize);window._chartResize=setTimeout(drawAllCharts,120)});
