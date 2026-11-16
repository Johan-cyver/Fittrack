const express = require('express');
const db = require('../db/sql');
const wrap = require('../middleware/wrap');
const { requireAuth } = require('../middleware/auth');

const router = express.Router();
router.use(requireAuth);

router.post('/', wrap(async (req, res) => {
  const kg = parseFloat(req.body.weight_kg);
  const date = req.body.logged_on;
  if (!(kg > 0) || !/^\d{4}-\d{2}-\d{2}$/.test(date || '')) {
    return res.status(400).json({ error: 'weight_kg (>0) and logged_on (YYYY-MM-DD) are required' });
  }
  // one entry per day: logging again the same day updates it
  await db.run(
    `INSERT INTO weight_logs (user_id, weight_kg, logged_on) VALUES (?, ?, ?)
     ON CONFLICT(user_id, logged_on) DO UPDATE SET weight_kg = excluded.weight_kg`,
    [req.user.id, kg, date]
  );
  res.status(201).json({ ok: true });
}));

router.get('/', wrap(async (req, res) => {
  res.json(await db.all('SELECT id, weight_kg, logged_on FROM weight_logs WHERE user_id = ? ORDER BY logged_on DESC LIMIT 90', [req.user.id]));
}));

router.delete('/:id', wrap(async (req, res) => {
  const info = await db.run('DELETE FROM weight_logs WHERE id = ? AND user_id = ?', [parseInt(req.params.id, 10), req.user.id]);
  if (!info.changes) return res.status(404).json({ error: 'Entry not found' });
  res.json({ ok: true });
}));

module.exports = router;
