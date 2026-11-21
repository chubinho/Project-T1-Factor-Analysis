const LABEL = '#4e6078';
const GRID = '#d9e2ed';
const PRINCIPAL = '#315fbe';
const INTEREST = '#4bbcaf';
const STRESS = '#008176';
const yearLabel = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const monthLabel = new Intl.DateTimeFormat('ru-RU', { month: 'short', year: '2-digit', timeZone: 'UTC' });

function canvasArea(canvas) {
  const { width, height } = canvas.getBoundingClientRect();
  if (!width || !height) return null;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);
  ctx.font = '12px system-ui, sans-serif';
  return { ctx, left: 54, top: 24, right: width - 12, bottom: height - 27 };
}

function grid(area, min, max, unit) {
  const { ctx, left, top, right, bottom } = area;
  const scale = value => bottom - (value - min) / (max - min) * (bottom - top);
  ctx.fillStyle = LABEL;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillText(unit, 0, 3);
  for (let tick = 0; tick <= 4; tick++) {
    const value = min + ((max - min) / 4) * tick;
    const y = scale(value);
    ctx.strokeStyle = GRID;
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.fillText(yearLabel.format(value), left - 8, y);
  }
  return scale;
}

function axisLabel(area, x, label, align = 'center') {
  const { ctx, bottom } = area;
  ctx.fillStyle = LABEL;
  ctx.textBaseline = 'top';
  ctx.textAlign = align;
  ctx.fillText(label, x, bottom + 7);
}

export function drawPayments(canvas, before, after, changed, currency) {
  const area = canvasArea(canvas);
  if (!area) return;
  const { ctx, left, right, bottom } = area;
  const peak = after.reduce((max, row, index) => Math.max(max, row.payment, before[index].payment), 0);
  const million = peak >= 1e6;
  const divisor = million ? 1e6 : 1e3;
  const unit = `${million ? 'млн' : 'тыс.'} ${currency === 'RUB' ? '₽' : currency}`;
  const rounded = 10 ** Math.floor(Math.log10(peak / divisor));
  const max = Math.ceil(peak / divisor / rounded) * rounded || 1;
  const scale = grid(area, 0, max, unit);
  const slot = (right - left) / after.length;
  const barWidth = Math.min(24, slot * (changed ? .37 : .72));
  const xAt = index => left + (index + .5) * slot;
  function bar(row, x) {
    const yPrincipal = scale(row.principal / divisor);
    const yPayment = scale(row.payment / divisor);
    ctx.fillStyle = PRINCIPAL;
    ctx.fillRect(x, yPrincipal, barWidth, bottom - yPrincipal);
    ctx.fillStyle = INTEREST;
    ctx.fillRect(x, yPayment, barWidth, yPrincipal - yPayment);
  }
  after.forEach((row, index) => {
    const x = xAt(index);
    if (changed) {
      ctx.globalAlpha = .48;
      bar(before[index], x - barWidth * 1.08);
      ctx.globalAlpha = 1;
    }
    bar(row, changed ? x + barWidth * .08 : x - barWidth / 2);
  });
  const slots = Math.min(after.length, Math.max(2, Math.floor((right - left) / 90) + 1));
  for (let tick = 0; tick < slots; tick++) {
    const index = slots === 1 ? 0 : Math.round(tick * (after.length - 1) / (slots - 1));
    const [x, align] = slots === 1 ? [xAt(index), 'center']
      : tick === 0 ? [left, 'left'] : tick === slots - 1 ? [right, 'right'] : [xAt(index), 'center'];
    axisLabel(area, x, monthLabel.format(new Date(after[index].payment_date)), align);
  }
}

export function drawCurves(canvas, tenors, base, stress) {
  const area = canvasArea(canvas);
  if (!area) return;
  const { ctx, left, right } = area;
  const series = stress ? [base, stress] : [base];
  const allRates = series.flat().map(rate => rate * 100);
  const min = Math.floor(Math.min(...allRates));
  const max = Math.max(min + 1, Math.ceil(Math.max(...allRates)));
  const scale = grid(area, min, max, 'Доходность, %');
  const end = tenors.at(-1);
  const xAt = tenor => left + (right - left) * tenor / end;
  series.forEach((rates, index) => {
    const color = index === 0 ? PRINCIPAL : STRESS;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.setLineDash(index === 0 ? [] : [5, 4]);
    ctx.beginPath();
    rates.forEach((rate, i) => ctx.lineTo(xAt(tenors[i]), scale(rate * 100)));
    ctx.stroke();
    if (rates.length === 1) {
      ctx.fillStyle = color;
      ctx.fillRect(xAt(tenors[0]) - 3, scale(rates[0] * 100) - 2, 6, 4);
    }
  });
  ctx.setLineDash([]);
  for (let tick = 0; tick <= 5; tick++) {
    const tenor = end * tick / 5;
    axisLabel(area, xAt(tenor), yearLabel.format(tenor) + (tick === 5 ? ' лет' : ''), tick === 5 ? 'right' : 'center');
  }
}

export function drawReadyPayments(canvas, rows, currency) {
  const area = canvasArea(canvas);
  if (!area) return;
  const { ctx, left, right } = area;
  const amounts = rows.map(row => row.payment);
  const limit = Math.max(...amounts.map(Math.abs));
  const divisor = limit >= 1e6 ? 1e6 : limit >= 1e3 ? 1e3 : 1;
  const unit = (divisor === 1e6 ? 'млн ' : divisor === 1e3 ? 'тыс. ' : '') + (currency === 'RUB' ? '₽' : currency);
  const min = Math.floor(Math.min(0, ...amounts) / divisor);
  const max = Math.max(min + 1, Math.ceil(Math.max(0, ...amounts) / divisor));
  const scale = grid(area, min, max, unit);
  const zero = scale(0);
  ctx.strokeStyle = LABEL;
  ctx.beginPath();
  ctx.moveTo(left, zero);
  ctx.lineTo(right, zero);
  ctx.stroke();

  const dates = rows.map(row => Date.parse(row.payment_date));
  const width = right - left;
  const span = dates.at(-1) - dates[0];
  const barWidth = Math.max(1, Math.min(20, width / dates.length * .64));
  const xAt = time => span ? left + barWidth / 2 + (time - dates[0]) / span * (width - barWidth) : (left + right) / 2;
  rows.forEach((row, index) => {
    const y = scale(row.payment / divisor);
    ctx.fillStyle = row.payment < 0 ? '#9b3039' : PRINCIPAL;
    ctx.fillRect(xAt(dates[index]) - barWidth / 2, Math.min(y, zero), barWidth, Math.abs(zero - y));
  });
  const tickCount = span ? Math.max(2, Math.floor(width / 100)) : 1;
  for (let index = 0; index < tickCount; index++) {
    const time = dates[0] + (tickCount === 1 ? 0 : index * span / (tickCount - 1));
    const align = tickCount === 1 ? 'center' : index === 0 ? 'left' : index === tickCount - 1 ? 'right' : 'center';
    axisLabel(area, xAt(time), monthLabel.format(new Date(time)), align);
  }
}
