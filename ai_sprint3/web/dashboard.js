import { drawCurves, drawPayments } from './charts.js';

const byId = id => document.getElementById(id);
const put = (id, text) => { byId(id).textContent = text; };
const dealInput = byId('deal');
const paymentInput = byId('payment-type');
const dayCountInput = byId('day-count');
const scenarioInput = byId('scenario');
const paymentChart = byId('payments-chart');
const curveChart = byId('curves-chart');
const payments = { ANN: 'ANN · аннуитет', RGM: 'RGM · равные доли тела', SIMPLE: 'SIMPLE · тело в конце срока' };
const scenarioLabels = {
  base: 'Без стресса', parallel_up: 'Параллельный сдвиг вверх', parallel_down: 'Параллельный сдвиг вниз',
  steepener: 'Увеличение наклона', flattener: 'Уменьшение наклона',
  short_up: 'Рост коротких ставок', short_down: 'Снижение коротких ставок',
};
const dateFormat = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeZone: 'UTC' });
const rates = new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 3 });
const tenors = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 4 });
const points = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 3, signDisplay: 'exceptZero' });
const formatDate = iso => dateFormat.format(new Date(iso));
const signal = value => value < -.005 ? 'negative' : value >= .005 ? 'positive' : '';

let deals;
let request;
let draw = () => {};

async function getJson(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Ошибка сервера расчёта.');
  return result;
}

function make(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function tableRows(id, rows) {
  const body = document.createDocumentFragment();
  rows.forEach(values => {
    const row = make('tr');
    values.forEach((value, index) => {
      const cell = make(index === 0 ? 'th' : 'td', value);
      if (index === 0) cell.scope = 'row';
      row.append(cell);
    });
    body.append(row);
  });
  byId(id).replaceChildren(body);
}

function card(title, ...paragraphs) {
  const article = make('article', undefined, 'experience');
  article.append(make('h3', title), ...paragraphs.map(text => make('p', text)));
  return article;
}

function explain(result, money, delta, scenario, start, end, ratesChanged) {
  const { before, after, changed_parameters: changes, delta_metrics: diff } = result;
  const cards = [];
  for (const change of changes) {
    if (change.parameter === 'payment_type') {
      cards.push(card(`Тип платежа · ${change.before} → ${change.after}`,
        `Выбран способ погашения «${payments[change.after]}». Сумма, договорная ставка и даты платежей сохранены.`,
        `Первый платёж ${money(start.payment)} → ${money(end.payment)}. Изменение суммы процентов ${delta(diff.total_interest)}, базовой PV ${delta(diff.base_pv)}.`));
    } else if (change.parameter === 'day_count') {
      cards.push(card(`База начисления · ${change.before} → ${change.after}`,
        `В первом периоде ${start.days} дн. Проценты = ${money(start.opening_balance)} × ${rates.format(after.deal.annual_rate)} × ${start.days} / ${change.before.split('/')[1]} или / ${change.after.split('/')[1]}.`,
        `Проценты за первый период ${money(start.interest)} → ${money(end.interest)}. Изменение суммы процентов ${delta(diff.total_interest)}, базовой PV ${delta(diff.base_pv)}.`));
    }
  }
  if (cards.length) {
    const comparison = document.createDocumentFragment();
    if (cards.length > 1) comparison.append(make('p', 'В сравнении одновременно изменены тип платежа и база начисления. Указанные суммы отражают совместный эффект.', 'meta'));
    comparison.append(...cards);
    comparison.append(card('Оценка изменённых платежей',
      `Для обоих графиков использованы одни и те же кривые, дата оценки и сроки дисконтирования. Изменение PV по выбранной кривой ${delta(diff.stress_pv)}.`,
      `Изменение сценарного dNPV между графиками ${delta(diff.dnpv)}.`));
    byId('explanations').replaceChildren(comparison);
    return;
  }
  if (after.scenario_id !== 'base') {
    cards.push(card(`Стресс · ${scenario.label}`,
      ratesChanged
        ? 'Платежи по фиксированной сделке сохранены. Доходности выбранной кривой меняют дисконт-факторы будущих платежей по формуле DF = exp(−r × t).'
        : 'Платежи по фиксированной сделке сохранены. Доходности выбранной кривой совпадают с базовыми.',
      `${money(after.metrics.stress_pv)} − ${money(after.metrics.base_pv)} = ${delta(after.metrics.dnpv)} по неокруглённым значениям.`));
  } else {
    cards.push(card('Исходные параметры',
      `Показан график ${after.deal.payment_type} с начислением ${after.deal.day_count}. Обе оценки рассчитаны по базовой кривой.`));
  }
  byId('explanations').replaceChildren(...cards);
}

function showSources(result, scenario) {
  const { after, market } = result;
  const source = market.source;
  const range = `${source.sheet}!${scenario.workbook_range}`;
  const selectedSource = make('span', `${source.file} · ${range}`);
  selectedSource.id = 'scenario-source';
  if (typeof scenario.source_url === 'string' && /^https?:\/\//i.test(scenario.source_url)) {
    const link = make('a', 'Описание сценария');
    link.href = scenario.source_url;
    selectedSource.append(' · ', link);
  }
  const metadata = [
    ['Сделка', after.deal.source],
    ['Дата оценки', formatDate(after.valuation_date)],
    ['Валюта', after.deal.currency],
    ['Кривые', `Команда ${source.team} · ${source.file} · ${source.sheet}!${market.base_curve.workbook_range} (база)`],
    ['Выбранный сценарий', scenario.label],
    ['Источник сценария', selectedSource],
    ['Дата среза кривых', source.as_of ? formatDate(source.as_of) : 'В источнике отсутствует'],
    ['Версия источника', `SHA-256 ${source.sha256}`],
  ];
  if (after.scenario_id !== 'base') {
    const shock = scenario.key_rate_shock_pp;
    if (Number.isFinite(shock)) {
      const link = make('a', `${points.format(shock)} п.п.`);
      link.href = '#scenario-source';
      metadata.push(['Изменение ключевой ставки', link]);
    } else {
      metadata.push(['Изменение ключевой ставки', 'Связь со сценарием в источнике отсутствует']);
    }
  }
  byId('source-grid').replaceChildren(...metadata.flatMap(([label, value]) => {
    const description = make('dd');
    description.append(value);
    return [make('dt', label), description];
  }));
}

function show(result) {
  const { before, after, market, delta_metrics: diff } = result;
  const currency = after.deal.currency;
  const money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency }).format;
  const signedMoney = new Intl.NumberFormat('ru-RU', { style: 'currency', currency, signDisplay: 'exceptZero' }).format;
  const delta = value => signedMoney(Math.abs(value) < .005 ? 0 : value);
  const scenario = after.scenario_id === 'base' ? market.base_curve
    : market.stress_curves.find(entry => entry.scenario_id === after.scenario_id);
  const { zero_rates: baseRates } = market.base_curve;
  const { zero_rates: scenarioRates, zero_rate_shifts_pp: shifts } = scenario;
  const changed = before.schedule.some((row, i) =>
    row.principal !== after.schedule[i].principal || row.interest !== after.schedule[i].interest);
  const hasStress = after.scenario_id !== 'base';
  const minShift = Math.min(...shifts);
  const maxShift = Math.max(...shifts);

  put('base-pv', money(after.metrics.base_pv));
  put('stress-pv', money(after.metrics.stress_pv));
  put('dnpv', delta(after.metrics.dnpv));
  byId('dnpv').className = signal(after.metrics.dnpv);
  put('total-interest', money(after.metrics.total_interest));
  put('base-note', `На ${formatDate(after.valuation_date)} · ${currency}`);
  put('stress-note', hasStress ? (scenarioLabels[after.scenario_id] || scenario.label) : 'Без стресса');
  put('interest-note', `К исходному графику ${delta(diff.total_interest)}`);
  put('payments-caption', `${before.deal.payment_type} / ${before.deal.day_count} → ${after.deal.payment_type} / ${after.deal.day_count} · ${after.schedule.length} платежей`);
  put('curves-caption', hasStress
    ? `Сдвиг доходности ${Math.abs(maxShift - minShift) < 1e-9 ? points.format(maxShift) : `от ${points.format(minShift)} до ${points.format(maxShift)}`} п.п.`
    : 'Для обеих оценок применяется базовая кривая.');
  byId('outline-key').hidden = !changed;
  byId('stress-key').hidden = !hasStress;

  tableRows('payments-table', after.schedule.map((row, i) => {
    const old = before.schedule[i];
    return [formatDate(row.payment_date), ...[old, row].flatMap(value => [value.principal, value.interest, value.payment].map(money))];
  }));
  tableRows('curves-table', market.tenors_years.map((years, i) =>
    [tenors.format(years), rates.format(baseRates[i]), rates.format(scenarioRates[i]), points.format(shifts[i])]));
  const metrics = {
    total_principal: 'Тело долга', total_interest: 'Сумма процентов', total_payments: 'Сумма платежей',
    final_balance: 'Остаток в конце срока', base_pv: 'PV по базовой кривой',
    stress_pv: 'PV по выбранной кривой', dnpv: 'Сценарный dNPV',
  };
  tableRows('metrics-table', Object.entries(metrics).map(([key, label]) =>
    [label, money(before.metrics[key]), money(after.metrics[key]), delta(diff[key])]));
  explain(result, money, delta, scenario, before.schedule[0], after.schedule[0], shifts.some(shift => shift !== 0));
  showSources(result, scenario);
  byId('results').hidden = false;
  byId('sources').hidden = false;
  draw = () => {
    drawPayments(paymentChart, before.schedule, after.schedule, changed, currency);
    drawCurves(curveChart, market.tenors_years, baseRates, hasStress ? scenarioRates : null);
  };
  draw();
}

function updateDeal() {
  const deal = deals.get(dealInput.value);
  const amount = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: deal.currency }).format(deal.principal);
  put('deal-summary', `${deal.source} · ${amount} · ${rates.format(deal.annual_rate)} годовых, фиксированная ставка`);
  paymentInput.value = deal.payment_type;
  dayCountInput.value = deal.day_count;
  scenarioInput.value = 'base';
  return update();
}

function showError(error) {
  put('error', error.message);
  byId('error').hidden = false;
  byId('status').hidden = true;
}

async function update() {
  request?.abort();
  const controller = new AbortController();
  request = controller;
  const params = new URLSearchParams({
    deal: dealInput.value, payment_type: paymentInput.value, day_count: dayCountInput.value, scenario: scenarioInput.value,
  });
  put('status', 'Обновляем расчёт…');
  byId('status').hidden = false;
  byId('error').hidden = true;
  byId('results').hidden = true;
  byId('sources').hidden = true;
  try {
    const result = await getJson(`/api/compare?${params}`, { signal: controller.signal });
    if (controller.signal.aborted) return;
    show(result);
    byId('status').hidden = true;
  } catch (error) {
    if (!controller.signal.aborted) showError(error);
  }
}

async function init() {
  try {
    const options = await getJson('/api/options');
    deals = new Map(options.deals.map(deal => [deal.deal_id, deal]));
    dealInput.replaceChildren(...options.deals.map(deal => new Option(deal.deal_id, deal.deal_id)));
    paymentInput.replaceChildren(...options.payment_types.map(type => new Option(payments[type], type)));
    dayCountInput.replaceChildren(...Object.keys(options.day_counts).map(dayCount => new Option(dayCount, dayCount)));
    scenarioInput.replaceChildren(...options.scenarios.map(({ label, id, available }) =>
      new Option(`${scenarioLabels[id] || label}${available ? '' : ' · нет данных'}`, id)));
    dealInput.addEventListener('change', updateDeal);
    for (const select of [paymentInput, dayCountInput, scenarioInput]) select.addEventListener('change', update);
    byId('reset').addEventListener('click', updateDeal);
    const observer = new ResizeObserver(() => draw());
    observer.observe(paymentChart);
    observer.observe(curveChart);
    byId('inputs').disabled = false;
    byId('reset').disabled = false;
    await updateDeal();
  } catch (error) {
    showError(error);
  }
}

init();
