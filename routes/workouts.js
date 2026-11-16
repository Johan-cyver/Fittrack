const express = require('express');
const db = require('../db/sql');
const { WorkoutDetail } = require('../db/mongo');
const wrap = require('../middleware/wrap');
const { requireAuth } = require('../middleware/auth');

const router = express.Router();
router.use(requireAuth);

const isDate = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || '');

// Summary row goes to SQL, exercise/set detail goes to MongoDB.
router.post('/', wrap(async (req, res) => {
  const { type, duration_min, calories_burned, workout_date, exercises, notes } = req.body || {};
  const duration = parseInt(duration_min, 10);
  if (!type || !(duration > 0) || !isDate(workout_date)) {
    return res.status(400).json({ error: 'type, duration_min (>0) and workout_date (YYYY-MM-DD) are required' });
  }
  const info = await db.run(
    'INSERT INTO workouts (user_id, type, duration_min, calories_burned, workout_date) VALUES (?, ?, ?, ?, ?)',
    [req.user.id, String(type).trim(), duration, parseInt(calories_burned, 10) || 0, workout_date]
  );
  const id = info.lastInsertRowid;

  try {
    if ((Array.isArray(exercises) && exercises.length) || notes) {
      await WorkoutDetail.create({ userId: req.user.id, workoutId: id, exercises: exercises || [], notes });
    }
  } catch (e) {
    // keep both stores consistent: roll back the SQL row if the Mongo write fails
    await db.run('DELETE FROM workouts WHERE id = ?', [id]);
    throw e;
  }
  res.status(201).json({ id });
}));

router.get('/', wrap(async (req, res) => {
  const rows = await db.all('SELECT * FROM workouts WHERE user_id = ? ORDER BY workout_date DESC, id DESC LIMIT 100', [req.user.id]);
  const details = await WorkoutDetail.find({ workoutId: { $in: rows.map((r) => r.id) }, userId: req.user.id }).lean();
  const byId = new Map(details.map((d) => [d.workoutId, d]));
  res.json(rows.map((r) => ({ ...r, exercises: byId.get(r.id)?.exercises || [], notes: byId.get(r.id)?.notes || '' })));
}));

router.delete('/:id', wrap(async (req, res) => {
  const id = parseInt(req.params.id, 10);
  const info = await db.run('DELETE FROM workouts WHERE id = ? AND user_id = ?', [id, req.user.id]);
  if (!info.changes) return res.status(404).json({ error: 'Workout not found' });
  await WorkoutDetail.deleteOne({ workoutId: id, userId: req.user.id });
  res.json({ ok: true });
}));

module.exports = router;
