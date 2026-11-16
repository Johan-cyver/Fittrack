const express = require('express');
const { Meal } = require('../db/mongo');
const { requireAuth } = require('../middleware/auth');

const router = express.Router();
router.use(requireAuth);

const isDate = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || '');

router.post('/', async (req, res, next) => {
  try {
    const { date, mealType, items } = req.body || {};
    if (!isDate(date) || !Array.isArray(items) || !items.length) {
      return res.status(400).json({ error: 'date (YYYY-MM-DD) and at least one item are required' });
    }
    const meal = await Meal.create({ userId: req.user.id, date, mealType, items });
    res.status(201).json(meal);
  } catch (e) {
    if (e.name === 'ValidationError') return res.status(400).json({ error: e.message });
    next(e);
  }
});

router.get('/', async (req, res, next) => {
  try {
    const filter = { userId: req.user.id };
    if (isDate(req.query.date)) filter.date = req.query.date;
    const meals = await Meal.find(filter).sort({ date: -1, createdAt: -1 }).limit(100).lean();
    res.json(meals);
  } catch (e) {
    next(e);
  }
});

router.delete('/:id', async (req, res, next) => {
  try {
    const r = await Meal.deleteOne({ _id: req.params.id, userId: req.user.id });
    if (!r.deletedCount) return res.status(404).json({ error: 'Meal not found' });
    res.json({ ok: true });
  } catch (e) {
    if (e.name === 'CastError') return res.status(400).json({ error: 'Invalid id' });
    next(e);
  }
});

module.exports = router;
