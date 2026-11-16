/* FitTrack frontend – plain JS, no build step */
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const todayStr = () => new Date().toISOString().slice(0, 10);

let token = localStorage.getItem('ft_token');
let user = JSON.parse(localStorage.getItem('ft_user') || 'null');
let registerMode = false;

async function api(path, opts = {}) {
  const res = await fetch('/api' + path, {
    method: opts.method || 'GET',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && token) { logout(); throw new Error('Session expired, please log in again'); }
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}

/* ---------- auth ---------- */
function setMode(reg) {
  registerMode = reg;
  $('#tab-login').classList.toggle('active', !reg);
  $('#tab-register').classList.toggle('active', reg);
  $('#auth-name').hidden = !reg;
  $('#auth-name').required = reg;
  $('#auth-form .primary').textContent = reg ? 'Create account' : 'Log in';
  $('#auth-error').textContent = '';
}
$('#tab-login').onclick = () => setMode(false);
$('#tab-register').onclick = () => setMode(true);

$('#auth-form').onsubmit = async (e) => {
  e.preventDefault();
  try {
    const body = { email: $('#auth-email').value, password: $('#auth-password').value };
    if (registerMode) body.name = $('#auth-name').value;
    const r = await api(registerMode ? '/auth/register' : '/auth/login', { method: 'POST', body });
    token = r.token; user = r.user;
    localStorage.setItem('ft_token', token);
    localStorage.setItem('ft_user', JSON.stringify(user));
    showApp();
  } catch (err) { $('#auth-error').textContent = err.message; }
};

function logout() {
  token = null; user = null;
  localStorage.removeItem('ft_token'); localStorage.removeItem('ft_user');
  $('#app-view').hidden = true; $('#auth-view').hidden = false;
}
$('#logout').onclick = logout;

/* ---------- navigation ---------- */
function showPage(name) {
  $$('#nav button').forEach((b) => b.classList.toggle('active', b.dataset.page === name));
  $$('.page').forEach((p) => (p.hidden = p.id !== 'page-' + name));
  ({ dashboard: loadDashboard, workouts: loadWorkouts, meals: loadMeals, weight: loadWeight })[name]();
}
$$('#nav button').forEach((b) => (b.onclick = () => showPage(b.dataset.page)));

function showApp() {
  $('#auth-view').hidden = true; $('#app-view').hidden = false;
  $('#user-name').textContent = user?.name || '';
  ['#w-date', '#m-date', '#wt-date'].forEach((s) => ($(s).value = todayStr()));
  if (!$('#exercise-list').children.length) addExercise();
  if (!$('#food-list').children.length) addFood();
  showPage('dashboard');
}

/* ---------- charts (tiny canvas line/bar, no libraries) ---------- */
function drawChart(canvas, labels, values, kind) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = 180;
  canvas.width = w * dpr; canvas.height = h * dpr; canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr); ctx.clearRect(0, 0, w, h);
  const pad = { l: 40, r: 10, t: 10, b: 24 };
  let min = Math.min(...values), max = Math.max(...values);
  if (kind === 'bar') min = 0;
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const x = (i) => pad.l + (values.length === 1 ? (w - pad.l - pad.r) / 2 : (i * (w - pad.l - pad.r)) / (values.length - 1));
  const y = (v) => pad.t + (1 - (v - min) / span) * (h - pad.t - pad.b);
  ctx.font = '11px system-ui'; ctx.fillStyle = '#6b7684'; ctx.strokeStyle = '#e3e8ee';
  for (let i = 0; i <= 3; i++) {
    const v = min + (span * i) / 3, yy = y(v);
    ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r, yy); ctx.stroke();
    ctx.fillText(Math.round(v * 10) / 10, 4, yy + 4);
  }
  ctx.fillStyle = '#0f9d6b'; ctx.strokeStyle = '#0f9d6b'; ctx.lineWidth = 2;
  if (kind === 'bar') {
    const bw = Math.min(36, ((w - pad.l - pad.r) / values.length) * 0.6);
    values.forEach((v, i) => {
      const cx = pad.l + ((i + 0.5) * (w - pad.l - pad.r)) / values.length;
      ctx.fillRect(cx - bw / 2, y(v), bw, y(0) - y(v));
      ctx.fillStyle = '#6b7684'; ctx.fillText(labels[i], cx - 12, h - 6); ctx.fillStyle = '#0f9d6b';
    });
  } else {
    ctx.beginPath();
    values.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
    ctx.stroke();
    values.forEach((v, i) => { ctx.beginPath(); ctx.arc(x(i), y(v), 3, 0, 7); ctx.fill(); });
    ctx.fillStyle = '#6b7684';
    ctx.fillText(labels[0], pad.l, h - 6);
    if (labels.length > 1) ctx.fillText(labels[labels.length - 1], w - pad.r - 52, h - 6);
  }
}

/* ---------- dashboard ---------- */
async function loadDashboard() {
  try {
    const s = await api('/stats');
    const { goals, week } = s;
    $('#s-workouts').textContent = week.workouts;
    $('#s-workouts-goal').textContent = `Goal: ${goals.weekly_workout_goal} per week`;
    $('#b-workouts').style.width = Math.min(100, (week.workouts / goals.weekly_workout_goal) * 100) + '%';
    $('#s-minutes').textContent = week.minutes;
    $('#s-burned').textContent = `${week.burned} kcal burned`;
    $('#s-cal').textContent = s.caloriesToday;
    $('#s-cal-goal').textContent = `Goal: ${goals.daily_calorie_goal} kcal`;
    const pct = (s.caloriesToday / goals.daily_calorie_goal) * 100;
    $('#b-cal').style.width = Math.min(100, pct) + '%';
    $('#b-cal').classList.toggle('over', pct > 100);
    $('#goal-cal').value = goals.daily_calorie_goal;
    $('#goal-wk').value = goals.weekly_workout_goal;

    const w = s.weights;
    if (w.length) {
      const last = w[w.length - 1].weight_kg;
      $('#s-weight').textContent = last + ' kg';
      $('#s-weight-change').textContent = w.length > 1 ? `${(last - w[0].weight_kg > 0 ? '+' : '')}${(last - w[0].weight_kg).toFixed(1)} kg since ${w[0].logged_on}` : '';
    } else { $('#s-weight').textContent = '–'; $('#s-weight-change').textContent = ''; }
    $('#weight-empty').hidden = w.length > 0;
    $('#weight-chart').hidden = !w.length;
    if (w.length) drawChart($('#weight-chart'), w.map((r) => r.logged_on.slice(5)), w.map((r) => r.weight_kg), 'line');

    const c = s.caloriesByDay;
    $('#cal-empty').hidden = c.length > 0;
    $('#cal-chart').hidden = !c.length;
    if (c.length) drawChart($('#cal-chart'), c.map((r) => r.date.slice(5)), c.map((r) => r.calories), 'bar');

    $('#type-list').innerHTML = s.byType.length
      ? s.byType.map((t) => `<li><span>${esc(t.type)}</span><span class="muted">${t.count} sessions · ${t.minutes} min</span></li>`).join('')
      : '<li class="muted">No workouts yet.</li>';
  } catch (e) { console.error(e); }
}

$('#goal-form').onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api('/auth/goals', { method: 'PUT', body: { daily_calorie_goal: $('#goal-cal').value, weekly_workout_goal: $('#goal-wk').value } });
    loadDashboard();
  } catch (err) { alert(err.message); }
};

/* ---------- workouts ---------- */
function addExercise() {
  const d = document.createElement('div');
  d.className = 'ex-block';
  d.innerHTML = `
    <div class="ex-head"><input class="ex-name" placeholder="Exercise (e.g. Bench press)" /><button type="button" class="del rm-ex">✕</button></div>
    <div class="sets"></div>
    <button type="button" class="secondary add-set">+ Set</button>`;
  d.querySelector('.rm-ex').onclick = () => d.remove();
  const addSet = () => {
    const r = document.createElement('div');
    r.className = 'set-row';
    r.innerHTML = `<input class="set-reps" type="number" min="0" placeholder="Reps" /><input class="set-kg" type="number" min="0" step="0.5" placeholder="Weight kg" /><button type="button" class="del">✕</button>`;
    r.querySelector('button').onclick = () => r.remove();
    d.querySelector('.sets').appendChild(r);
  };
  d.querySelector('.add-set').onclick = addSet;
  addSet();
  $('#exercise-list').appendChild(d);
}
$('#add-exercise').onclick = addExercise;

$('#workout-form').onsubmit = async (e) => {
  e.preventDefault();
  $('#w-error').textContent = '';
  const exercises = $$('#exercise-list .ex-block').map((b) => ({
    name: b.querySelector('.ex-name').value.trim(),
    sets: [...b.querySelectorAll('.set-row')]
      .map((r) => ({ reps: +r.querySelector('.set-reps').value || undefined, weightKg: +r.querySelector('.set-kg').value || undefined }))
      .filter((s) => s.reps || s.weightKg),
  })).filter((x) => x.name);
  try {
    await api('/workouts', { method: 'POST', body: {
      type: $('#w-type').value, workout_date: $('#w-date').value, duration_min: $('#w-dur').value,
      calories_burned: $('#w-cal').value, notes: $('#w-notes').value.trim() || undefined, exercises } });
    e.target.reset(); $('#w-date').value = todayStr();
    $('#exercise-list').innerHTML = ''; addExercise();
    loadWorkouts();
  } catch (err) { $('#w-error').textContent = err.message; }
};

async function loadWorkouts() {
  try {
    const list = await api('/workouts');
    $('#workout-list').innerHTML = list.length ? list.map((w) => `
      <div class="entry">
        <div>
          <b>${esc(w.type)} <span class="muted">· ${esc(w.workout_date)}</span></b>
          <span class="muted">${w.duration_min} min${w.calories_burned ? ` · ${w.calories_burned} kcal` : ''}</span>
          ${w.exercises.map((x) => `<div class="muted"><span class="tag">${esc(x.name)}</span>${x.sets.map((s) => `${s.reps ?? '-'}×${s.weightKg ?? 'bw'}`).map(esc).join(', ')}</div>`).join('')}
          ${w.notes ? `<div class="muted">“${esc(w.notes)}”</div>` : ''}
        </div>
        <button class="del" data-id="${w.id}">Delete</button>
      </div>`).join('') : '<p class="muted">No workouts logged yet.</p>';
    $$('#workout-list .del').forEach((b) => (b.onclick = async () => {
      if (confirm('Delete this workout?')) { await api('/workouts/' + b.dataset.id, { method: 'DELETE' }); loadWorkouts(); }
    }));
  } catch (e) { console.error(e); }
}

/* ---------- meals ---------- */
function addFood() {
  const r = document.createElement('div');
  r.className = 'item-row';
  r.innerHTML = `
    <input class="f-name" placeholder="Food" />
    <input class="f-qty" placeholder="Qty" />
    <input class="f-cal" type="number" min="0" placeholder="kcal" />
    <input class="f-p" type="number" min="0" placeholder="Protein g" />
    <input class="f-c" type="number" min="0" placeholder="Carbs g" />
    <input class="f-f" type="number" min="0" placeholder="Fat g" />
    <button type="button" class="del">✕</button>`;
  r.querySelector('.del').onclick = () => r.remove();
  $('#food-list').appendChild(r);
}
$('#add-food').onclick = addFood;

$('#meal-form').onsubmit = async (e) => {
  e.preventDefault();
  $('#m-error').textContent = '';
  const items = $$('#food-list .item-row').map((r) => ({
    name: r.querySelector('.f-name').value.trim(), quantity: r.querySelector('.f-qty').value.trim(),
    calories: +r.querySelector('.f-cal').value || 0, proteinG: +r.querySelector('.f-p').value || 0,
    carbsG: +r.querySelector('.f-c').value || 0, fatG: +r.querySelector('.f-f').value || 0,
  })).filter((i) => i.name);
  try {
    await api('/meals', { method: 'POST', body: { date: $('#m-date').value, mealType: $('#m-type').value, items } });
    $('#food-list').innerHTML = ''; addFood();
    loadMeals();
  } catch (err) { $('#m-error').textContent = err.message; }
};

async function loadMeals() {
  try {
    const list = await api('/meals');
    $('#meal-list').innerHTML = list.length ? list.map((m) => `
      <div class="entry">
        <div>
          <b>${esc(m.mealType)} <span class="muted">· ${esc(m.date)} · ${m.totalCalories} kcal</span></b>
          ${m.items.map((i) => `<div class="muted">${esc(i.name)}${i.quantity ? ' (' + esc(i.quantity) + ')' : ''} – ${i.calories} kcal, P${i.proteinG} C${i.carbsG} F${i.fatG}</div>`).join('')}
        </div>
        <button class="del" data-id="${esc(m._id)}">Delete</button>
      </div>`).join('') : '<p class="muted">No meals logged yet.</p>';
    $$('#meal-list .del').forEach((b) => (b.onclick = async () => {
      if (confirm('Delete this meal?')) { await api('/meals/' + b.dataset.id, { method: 'DELETE' }); loadMeals(); }
    }));
  } catch (e) { console.error(e); }
}

/* ---------- weight ---------- */
$('#weight-form').onsubmit = async (e) => {
  e.preventDefault();
  $('#wt-error').textContent = '';
  try {
    await api('/weight', { method: 'POST', body: { weight_kg: $('#wt-kg').value, logged_on: $('#wt-date').value } });
    $('#wt-kg').value = '';
    loadWeight();
  } catch (err) { $('#wt-error').textContent = err.message; }
};

async function loadWeight() {
  try {
    const list = await api('/weight');
    $('#weight-list').innerHTML = list.length ? list.map((w) => `
      <div class="entry"><div><b>${w.weight_kg} kg</b><span class="muted">${esc(w.logged_on)}</span></div><button class="del" data-id="${w.id}">Delete</button></div>`).join('')
      : '<p class="muted">No weight entries yet.</p>';
    $$('#weight-list .del').forEach((b) => (b.onclick = async () => { await api('/weight/' + b.dataset.id, { method: 'DELETE' }); loadWeight(); }));
  } catch (e) { console.error(e); }
}

/* ---------- boot ---------- */
window.addEventListener('resize', () => { if (!$('#page-dashboard').hidden && token) loadDashboard(); });
if (token && user) showApp();
