require('dotenv').config();
const path = require('path');
const express = require('express');
const { init: initSql } = require('./db/sql');
const { connectMongo } = require('./db/mongo');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Make sure both databases are ready before any API route runs (connections are cached, so this is cheap)
app.use('/api', async (req, res, next) => {
  try {
    await Promise.all([initSql(), connectMongo()]);
    next();
  } catch (e) {
    next(e);
  }
});

app.use('/api/auth', require('./routes/auth'));
app.use('/api/workouts', require('./routes/workouts'));
app.use('/api/meals', require('./routes/meals'));
app.use('/api/weight', require('./routes/weight'));
app.use('/api/stats', require('./routes/stats'));
app.use('/api', (req, res) => res.status(404).json({ error: 'Not found' }));

app.use((err, req, res, next) => {
  console.error(err);
  res.status(500).json({ error: 'Server error' });
});

// Vercel imports the app (see api/index.js); `npm start` runs it as a normal server.
if (require.main === module) {
  const PORT = process.env.PORT || 3000;
  Promise.all([initSql(), connectMongo()])
    .then(() => app.listen(PORT, () => console.log(`FitTrack running on http://localhost:${PORT}`)))
    .catch((e) => {
      console.error('Database startup failed:', e.message);
      process.exit(1);
    });
}

module.exports = app;
