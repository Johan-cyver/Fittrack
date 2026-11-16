const express = require('express');
const bcrypt = require('bcryptjs');
const db = require('../db/sql');
const wrap = require('../middleware/wrap');
const { sign, requireAuth } = require('../middleware/auth');

const router = express.Router();

router.post('/register', wrap(async (req, res) => {
  const { name, email, password } = req.body || {};
  if (!name || !email || !password) return res.status(400).json({ error: 'Name, email and password are required' });
  if (password.length < 6) return res.status(400).json({ error: 'Password must be at least 6 characters' });

  const cleanEmail = String(email).trim().toLowerCase();
  if (await db.get('SELECT id FROM users WHERE email = ?', [cleanEmail])) {
    return res.status(409).json({ error: 'Email already registered' });
  }
  const hash = bcrypt.hashSync(password, 10);
  const cleanName = String(name).trim();
  const info = await db.run('INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)', [cleanName, cleanEmail, hash]);
  const user = { id: info.lastInsertRowid, email: cleanEmail, name: cleanName };
  res.status(201).json({ token: sign(user), user });
}));

router.post('/login', wrap(async (req, res) => {
  const { email, password } = req.body || {};
  const row = await db.get('SELECT * FROM users WHERE email = ?', [String(email || '').trim().toLowerCase()]);
  if (!row || !bcrypt.compareSync(password || '', row.password_hash)) {
    return res.status(401).json({ error: 'Invalid email or password' });
  }
  res.json({ token: sign(row), user: { id: row.id, email: row.email, name: row.name } });
}));

router.get('/me', requireAuth, wrap(async (req, res) => {
  const u = await db.get('SELECT id, name, email, daily_calorie_goal, weekly_workout_goal FROM users WHERE id = ?', [req.user.id]);
  if (!u) return res.status(404).json({ error: 'User not found' });
  res.json(u);
}));

router.put('/goals', requireAuth, wrap(async (req, res) => {
  const cal = parseInt(req.body.daily_calorie_goal, 10);
  const wk = parseInt(req.body.weekly_workout_goal, 10);
  if (!(cal > 0) || !(wk > 0)) return res.status(400).json({ error: 'Goals must be positive numbers' });
  await db.run('UPDATE users SET daily_calorie_goal = ?, weekly_workout_goal = ? WHERE id = ?', [cal, wk, req.user.id]);
  res.json({ daily_calorie_goal: cal, weekly_workout_goal: wk });
}));

module.exports = router;
