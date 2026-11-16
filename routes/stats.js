const express = require('express');
const db = require('../db/sql');
const { Meal } = require('../db/mongo');
const wrap = require('../middleware/wrap');
const { requireAuth } = require('../middleware/auth');

const router = express.Router();
router.use(requireAuth);

const ymd = (d) => d.toISOString().slice(0, 10);

// Dashboard stats combine both databases: SQL aggregates + Mongo aggregation.
router.get('/', wrap(async (req, res) => {
  const uid = req.user.id;
  const today = new Date();
  const weekStart = new Date(today);
  weekStart.setDate(today.getDate() - 6);

  const [user, week, byType, weights, daily] = await Promise.all([
    db.get('SELECT daily_calorie_goal, weekly_workout_goal FROM users WHERE id = ?', [uid]),
    db.get(
      `SELECT COUNT(*) AS workouts, COALESCE(SUM(duration_min),0) AS minutes, COALESCE(SUM(calories_burned),0) AS burned
       FROM workouts WHERE user_id = ? AND workout_date >= ?`,
      [uid, ymd(weekStart)]
    ),
    db.all(
      `SELECT type, COUNT(*) AS count, SUM(duration_min) AS minutes
       FROM workouts WHERE user_id = ? GROUP BY type ORDER BY count DESC LIMIT 6`,
      [uid]
    ),
    db.all('SELECT weight_kg, logged_on FROM weight_logs WHERE user_id = ? ORDER BY logged_on ASC LIMIT 60', [uid]),
    Meal.aggregate([
      { $match: { userId: uid, date: { $gte: ymd(weekStart) } } },
      { $group: { _id: '$date', calories: { $sum: '$totalCalories' } } },
      { $sort: { _id: 1 } },
    ]),
  ]);

  const todayRow = daily.find((d) => d._id === ymd(today));

  res.json({
    goals: user,
    week,
    byType,
    weights,
    caloriesToday: todayRow ? todayRow.calories : 0,
    caloriesByDay: daily.map((d) => ({ date: d._id, calories: d.calories })),
    today: ymd(today),
  });
}));

module.exports = router;
