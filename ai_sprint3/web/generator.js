const money = new Intl.NumberFormat('ru-RU', {
  style: 'currency', currency: 'RUB', maximumFractionDigits: 0,
});
const percent = new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 4 });
const spread = new Intl.NumberFormat('ru-RU', { signDisplay: 'exceptZero', maximumFractionDigits: 4 });

function weightInput(value, label) {
  const input = document.createElement('input');
  input.type = 'number';
  input.min = '0';
  input.step = 'any';
  input.required = true;
  input.value = value;
  input.setAttribute('aria-label', label);
  return input;
}

function fillWeights(container, labels, weights) {
  for (const [key, text] of Object.entries(labels)) {
    const label = document.createElement('label');
    label.textContent = text;
    const input = weightInput(weights[key] ?? 0, `Вес: ${text}`);
    input.dataset.key = key;
    label.append(input);
    container.append(label);
  }
}

function appendTerm(container, months = '', weight = 1) {
  const row = document.createElement('div');
  row.className = 'generator-term';

  const term = document.createElement('input');
  term.type = 'number';
  term.min = '1';
  term.step = '1';
  term.required = true;
  term.value = months;
  term.setAttribute('aria-label', 'Срок, месяцев');
  term.placeholder = 'Месяцы';

  const termWeight = weightInput(weight, 'Вес срока');
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.textContent = '×';
  remove.setAttribute('aria-label', 'Удалить срок');
  remove.addEventListener('click', () => row.remove());
  row.append(term, termWeight, remove);
  container.append(row);
  return term;
}

function readWeights(container) {
  return Object.fromEntries(Array.from(container.querySelectorAll('input'),
    input => [input.dataset.key, input.valueAsNumber]));
}

function readTerms(container) {
  const terms = {};
  for (const row of container.children) {
    const [term, weight] = row.querySelectorAll('input');
    if (Object.hasOwn(terms, term.valueAsNumber)) throw new Error('Укажите каждый срок один раз.');
    terms[term.valueAsNumber] = weight.valueAsNumber;
  }
  return terms;
}

function rateDescription(deal) {
  if (deal.rate_type === 'FIXED') return 'Фиксированная';
  return `Плавающая · ${deal.rate_index} ${spread.format(deal.rate_spread * 100)} п.п. · каждые ${deal.reset_interval_months} мес.`;
}

function renderDeals(table, deals) {
  const body = document.createDocumentFragment();
  for (const deal of deals) {
    const row = document.createElement('tr');
    const values = [deal.deal_id, money.format(deal.principal), deal.term_months,
      deal.payment_type, deal.day_count, percent.format(deal.annual_rate), rateDescription(deal)];
    for (const [index, value] of values.entries()) {
      const cell = document.createElement(index === 0 ? 'th' : 'td');
      if (index === 0) cell.scope = 'row';
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
  table.replaceChildren(body);
}

function percentInput(rate) {
  return Number((rate * 100).toPrecision(15));
}

export function initGenerator(defaults, maxDeals, labels, generate, useDeals) {
  const byId = id => document.getElementById(id);
  const dialog = byId('generator-dialog');
  const form = byId('generator-form');
  const terms = byId('generator-terms');
  const paymentWeights = byId('generator-payment-weights');
  const rateWeights = byId('generator-rate-weights');
  const dayWeights = byId('generator-day-weights');
  const status = byId('generator-status');
  const error = byId('generator-error');
  const submit = byId('generate');
  const compare = byId('use-generated');
  const open = byId('open-generator');
  const fields = form.elements;

  const scalarDefaults = {
    count: defaults.count, seed: defaults.seed, start_date: defaults.start_date,
    payment_interval_months: defaults.payment_interval_months,
    principal_min: defaults.principal_rub[0], principal_max: defaults.principal_rub[1],
    rate_min: percentInput(defaults.annual_rate[0]), rate_max: percentInput(defaults.annual_rate[1]),
    float_index: defaults.float_rate.index, float_spread: percentInput(defaults.float_rate.spread),
    float_reset: defaults.float_rate.reset_interval_months,
  };
  for (const [key, value] of Object.entries(scalarDefaults)) fields.namedItem(key).value = value;
  fields.namedItem('count').max = maxDeals;
  for (const [months, weight] of Object.entries(defaults.term_months_weights)) appendTerm(terms, months, weight);
  fillWeights(paymentWeights, labels.payments, defaults.payment_type_weights);
  fillWeights(rateWeights, { FIXED: 'Фиксированная', FLOAT: 'Плавающая' }, defaults.rate_type_weights);
  fillWeights(dayWeights, labels.dayCounts, defaults.day_count_weights);

  byId('add-generator-term').addEventListener('click', () => appendTerm(terms).focus());
  byId('close-generator').addEventListener('click', () => dialog.close());
  compare.addEventListener('click', () => dialog.close());
  open.addEventListener('click', () => dialog.showModal());
  open.disabled = false;
  form.addEventListener('invalid', event => {
    const details = event.target.closest('details');
    if (details) details.open = true;
  }, true);

  const number = key => fields.namedItem(key).valueAsNumber;
  const value = key => fields.namedItem(key).value;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    submit.disabled = true;
    error.hidden = true;
    status.textContent = 'Генерируем сделки…';
    status.hidden = false;
    try {
      const config = {
        count: number('count'), seed: number('seed'),
        principal_rub: [number('principal_min'), number('principal_max')],
        annual_rate: [number('rate_min') / 100, number('rate_max') / 100],
        start_date: value('start_date'), payment_interval_months: number('payment_interval_months'),
        term_months_weights: readTerms(terms), payment_type_weights: readWeights(paymentWeights),
        day_count_weights: readWeights(dayWeights), rate_type_weights: readWeights(rateWeights),
        float_rate: { index: value('float_index'), spread: number('float_spread') / 100, reset_interval_months: number('float_reset') },
      };
      const deals = await generate(config);
      const fixed = deals.filter(deal => deal.rate_type === 'FIXED');
      renderDeals(byId('generator-table'), deals);
      useDeals(fixed);
      byId('generator-results').hidden = false;
      compare.hidden = fixed.length === 0;
      status.textContent = `Сгенерировано ${deals.length}. Для расчёта добавлено фиксированных: ${fixed.length}.`;
      if (fixed.length < deals.length) status.textContent += ' Плавающие сделки доступны в таблице.';
    } catch (failure) {
      status.hidden = true;
      error.textContent = failure.message;
      error.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });
}
