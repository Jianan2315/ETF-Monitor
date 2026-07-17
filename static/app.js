const RANGE_NAMES = {
  "5": "近一周走势",
  "21": "近一个月走势",
  "63": "近三个月走势",
  "126": "近六个月走势",
  "252": "最近一年走势",
  "all": "成立以来走势"
};

function selectRange(allPoints, days) {
  if (days === "all") return allPoints;
  const count = Number(days);
  return allPoints.slice(-Math.min(count, allPoints.length));
}

function drawChart(canvas, hoveredIndex = null) {
  const source = document.getElementById(canvas.dataset.sourceId);
  if (!source) return;

  if (!canvas._allPoints) {
    canvas._allPoints = JSON.parse(source.textContent);
  }
  if (!canvas._selectedDays) {
    canvas._selectedDays = "126";
  }

  const rawPoints = selectRange(canvas._allPoints, canvas._selectedDays);
  if (!rawPoints.length) return;

  const basePrice = rawPoints[0].price;
  const points = rawPoints.map(point => ({
    ...point,
    change_pct: (point.price / basePrice - 1) * 100
  }));

  const wrap = canvas.parentElement;
  const tooltip = wrap.querySelector('.chart-tooltip');
  const ctx = canvas.getContext('2d');
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

  const w = rect.width;
  const h = rect.height;
  const pad = {left: 56, right: 18, top: 18, bottom: 34};
  const values = points.map(point => point.change_pct);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const extra = Math.max((max - min) * 0.12, 0.5);
  min -= extra;
  max += extra;

  const x = index => pad.left + index * (w - pad.left - pad.right) / Math.max(1, points.length - 1);
  const y = value => pad.top + (max - value) * (h - pad.top - pad.bottom) / (max - min);

  ctx.clearRect(0, 0, w, h);
  ctx.font = '12px sans-serif';
  ctx.fillStyle = '#7b8794';
  ctx.strokeStyle = '#e8edf1';
  ctx.lineWidth = 1;

  for (let index = 0; index <= 4; index += 1) {
    const value = min + (max - min) * index / 4;
    const yy = y(value);
    ctx.beginPath();
    ctx.moveTo(pad.left, yy);
    ctx.lineTo(w - pad.right, yy);
    ctx.stroke();
    ctx.fillText(`${value.toFixed(1)}%`, 6, yy + 4);
  }

  ctx.strokeStyle = '#2676a5';
  ctx.lineWidth = 2.2;
  ctx.beginPath();
  points.forEach((point, index) => {
    const xx = x(index);
    const yy = y(point.change_pct);
    if (index === 0) ctx.moveTo(xx, yy);
    else ctx.lineTo(xx, yy);
  });
  ctx.stroke();

  ctx.fillStyle = '#7b8794';
  const tickCount = canvas._selectedDays === 'all' ? 6 : 5;
  const tickIndexes = [...new Set(
    Array.from({length: tickCount}, (_, i) =>
      Math.round(i * (points.length - 1) / Math.max(1, tickCount - 1))
    )
  )];

  function formatXAxisDate(dateText) {
    const [year, month, day] = dateText.split('-');
    if (canvas._selectedDays === '252' || canvas._selectedDays === 'all') {
      return `${year}-${month}`;
    }
    return `${month}-${day}`;
  }

  tickIndexes.forEach(index => {
    const label = formatXAxisDate(points[index].date);
    const labelWidth = ctx.measureText(label).width;
    const labelX = Math.max(
      pad.left,
      Math.min(w - pad.right - labelWidth, x(index) - labelWidth / 2)
    );
    ctx.fillText(label, labelX, h - 10);
  });

  if (hoveredIndex !== null && points[hoveredIndex]) {
    const point = points[hoveredIndex];
    const xx = x(hoveredIndex);
    const yy = y(point.change_pct);
    ctx.strokeStyle = '#9aabb8';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(xx, pad.top);
    ctx.lineTo(xx, h - pad.bottom);
    ctx.stroke();
    ctx.fillStyle = '#2676a5';
    ctx.beginPath();
    ctx.arc(xx, yy, 4, 0, Math.PI * 2);
    ctx.fill();
  }

  canvas._chart = {points, x, y, pad, w, h};

  canvas.onmousemove = event => {
    const chart = canvas._chart;
    const bounds = canvas.getBoundingClientRect();
    const mouseX = event.clientX - bounds.left;
    const index = Math.max(
      0,
      Math.min(
        chart.points.length - 1,
        Math.round(
          (mouseX - chart.pad.left)
          / (chart.w - chart.pad.left - chart.pad.right)
          * (chart.points.length - 1)
        )
      )
    );
    const point = chart.points[index];
    drawChart(canvas, index);
    const updatedChart = canvas._chart;
    const xx = updatedChart.x(index);
    const yy = updatedChart.y(point.change_pct);
    tooltip.hidden = false;
    tooltip.innerHTML = `${point.date}<br>价格：${point.price.toFixed(4)}<br>区间涨跌：${point.change_pct.toFixed(2)}%`;
    tooltip.style.left = `${xx}px`;
    tooltip.style.top = `${yy}px`;
  };

  canvas.onmouseleave = () => {
    tooltip.hidden = true;
    drawChart(canvas);
  };
}

function initializeChart(canvas) {
  const panel = canvas.closest('.chart-panel');
  const title = panel.querySelector('.chart-title');
  const buttons = panel.querySelectorAll('.range-switcher button');

  buttons.forEach(button => {
    button.addEventListener('click', () => {
      buttons.forEach(item => item.classList.remove('active'));
      button.classList.add('active');
      canvas._selectedDays = button.dataset.days;
      title.textContent = RANGE_NAMES[button.dataset.days] || '走势图';
      panel.querySelector('.chart-tooltip').hidden = true;
      drawChart(canvas);
    });
  });

  drawChart(canvas);
}

function drawAllCharts() {
  document.querySelectorAll('.price-chart').forEach(canvas => {
    if (!canvas._initialized) {
      canvas._initialized = true;
      initializeChart(canvas);
    } else {
      drawChart(canvas);
    }
  });
}

document.addEventListener('DOMContentLoaded', drawAllCharts);
window.addEventListener('resize', () => {
  clearTimeout(window._chartResize);
  window._chartResize = setTimeout(drawAllCharts, 120);
});


function openStrategyModal(modal) {
  if (!modal) return;
  modal.hidden = false;
  modal.setAttribute('aria-hidden', 'false');
  document.body.classList.add('modal-open');
  const closeButton = modal.querySelector('.strategy-modal-close');
  if (closeButton) closeButton.focus();
}

function closeStrategyModal(modal) {
  if (!modal) return;
  modal.hidden = true;
  modal.setAttribute('aria-hidden', 'true');
  document.body.classList.remove('modal-open');
}

document.addEventListener('click', event => {
  const openButton = event.target.closest('[data-strategy-modal]');
  if (openButton) {
    const modal = document.getElementById(openButton.dataset.strategyModal);
    openStrategyModal(modal);
    return;
  }

  const closeControl = event.target.closest('[data-close-strategy-modal]');
  if (closeControl) {
    closeStrategyModal(closeControl.closest('.strategy-modal'));
  }
});

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  const openModal = document.querySelector('.strategy-modal:not([hidden])');
  closeStrategyModal(openModal);
});


function drawIntradayChart(canvas) {
  const source = document.getElementById(canvas.dataset.sourceId);
  if (!source) return;
  const points = JSON.parse(source.textContent || "[]");
  const ctx = canvas.getContext("2d");
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const w = rect.width, h = rect.height;
  const pad = {left:56,right:18,top:18,bottom:34};
  ctx.clearRect(0,0,w,h);
  ctx.font = "12px sans-serif";
  ctx.fillStyle = "#7b8794";
  ctx.strokeStyle = "#e8edf1";

  if (!points.length) {
    ctx.fillStyle="#9aa5af";
    ctx.fillText("暂无今日分时数据", Math.max(pad.left, w/2-55), h/2);
    return;
  }

  const parseTime = value => {
    const normalized = String(value || "").replace(" ", "T");
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? null : date;
  };
  const firstTime = parseTime(points[0].time);
  const lastTime = parseTime(points[points.length - 1].time);
  const tickIndexes = [...new Set(
    Array.from({length: Math.min(6, points.length)}, (_, i) =>
      Math.round(i * (points.length - 1) / Math.max(1, Math.min(6, points.length) - 1))
    )
  )];
  tickIndexes.forEach(index => {
    const pointTime = parseTime(points[index].time);
    const label = pointTime
      ? pointTime.toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit", hour12:false})
      : String(points[index].time || "").slice(-8, -3);
    const xx = pad.left + index * (w-pad.left-pad.right) / Math.max(1, points.length-1);
    const labelWidth = ctx.measureText(label).width;
    ctx.fillText(label, Math.max(pad.left, Math.min(w-pad.right-labelWidth, xx-labelWidth/2)), h-10);
    ctx.beginPath();ctx.moveTo(xx,pad.top);ctx.lineTo(xx,h-pad.bottom);ctx.stroke();
  });

  const values=points.map(p=>p.price);
  let min=Math.min(...values), max=Math.max(...values);
  if(min===max){min-=0.01;max+=0.01;}
  const extra=(max-min)*0.12 || 0.01; min-=extra; max+=extra;
  const x=i=>pad.left+i*(w-pad.left-pad.right)/Math.max(1,points.length-1);
  const y=v=>pad.top+(max-v)*(h-pad.top-pad.bottom)/(max-min);
  ctx.strokeStyle="#2676a5";ctx.lineWidth=2.2;ctx.beginPath();
  points.forEach((p,i)=>{if(i===0)ctx.moveTo(x(i),y(p.price));else ctx.lineTo(x(i),y(p.price));});
  ctx.stroke();

  const tooltip=canvas.parentElement.querySelector(".chart-tooltip");
  canvas.onmousemove=event=>{
    const bounds=canvas.getBoundingClientRect();
    const index=Math.max(0,Math.min(points.length-1,Math.round((event.clientX-bounds.left-pad.left)/(w-pad.left-pad.right)*(points.length-1))));
    const point=points[index];
    tooltip.hidden=false;
    tooltip.innerHTML=`${point.time}<br>价格：${Number(point.price).toFixed(4)}`;
    tooltip.style.left=`${x(index)}px`; tooltip.style.top=`${y(point.price)}px`;
  };
  canvas.onmouseleave=()=>{tooltip.hidden=true;};
}

function drawAllIntradayCharts() {
  document.querySelectorAll(".intraday-chart").forEach(drawIntradayChart);
}
document.addEventListener("DOMContentLoaded", drawAllIntradayCharts);
window.addEventListener("resize", ()=> {
  clearTimeout(window._intradayResize);
  window._intradayResize=setTimeout(drawAllIntradayCharts,120);
});
