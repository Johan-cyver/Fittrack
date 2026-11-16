// SQL side: users, workout summaries, weight log (structured, relational data).
// Uses libSQL (SQLite-compatible). On Vercel it talks to a hosted Turso database;
// locally it falls back to a plain SQLite file.
const { createClient } = require('@libsql/client');

const client = createClient({
  url: process.env.TURSO_DATABASE_URL || 'file:./fittrack.db',
  authToken: process.env.TURSO_AUTH_TOKEN,
});

const SCHEMA = [
  `CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    daily_calorie_goal INTEGER DEFAULT 2200,
    weekly_workout_goal INTEGER DEFAULT 4,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    duration_min INTEGER NOT NULL CHECK (duration_min > 0),
    calories_burned INTEGER DEFAULT 0,
    workout_date TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
  )`,
  `CREATE INDEX IF NOT EXISTS idx_workouts_user_date ON workouts(user_id, workout_date)`,
  `CREATE TABLE IF NOT EXISTS weight_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    weight_kg REAL NOT NULL CHECK (weight_kg > 0),
    logged_on TEXT NOT NULL,
    UNIQUE(user_id, logged_on)
  )`,
];

let ready = null;
function init() {
  if (!ready) {
    ready = client.batch(SCHEMA.map((sql) => ({ sql, args: [] })), 'write').catch((e) => {
      ready = null; // allow retry on next request
      throw e;
    });
  }
  return ready;
}

async function exec(sql, args = []) {
  await init();
  return client.execute({ sql, args });
}

const toObjects = (r) => r.rows.map((row) => Object.fromEntries(r.columns.map((c, i) => [c, row[i]])));

async function all(sql, args = []) {
  return toObjects(await exec(sql, args));
}

async function get(sql, args = []) {
  return (await all(sql, args))[0];
}

async function run(sql, args = []) {
  const r = await exec(sql, args);
  return {
    changes: r.rowsAffected,
    lastInsertRowid: r.lastInsertRowid != null ? Number(r.lastInsertRowid) : null,
  };
}

module.exports = { init, all, get, run };
