# FitTrack

A fitness tracker using **two databases**, each doing what it is best at.

| Data | Store | Why |
|---|---|---|
| Users, workout summaries, weight log, goals | **SQL via libSQL** (Turso in the cloud, plain SQLite file locally) | Structured, relational, needs constraints and aggregates |
| Exercises/sets per workout, meals with variable food items | **MongoDB** (`mongoose`) | Flexible nested documents |

A workout is split across both: the summary row lives in SQLite and the exercise/set detail lives in MongoDB, linked by `workoutId`. The dashboard (`/api/stats`) combines SQL aggregates with a Mongo aggregation pipeline.

## Run it

```bash
npm install
cp .env.example .env     # set JWT_SECRET to a long random string
# leave TURSO_* empty to use a local ./fittrack.db file
# make sure MongoDB is running (e.g. mongod, or: docker run -p 27017:27017 mongo)
npm start
```

Open http://localhost:3000 and sign up.

## Deploy on Vercel

1. Create a free **MongoDB Atlas** cluster and copy its connection string (allow access from anywhere, `0.0.0.0/0`, so Vercel can reach it).
2. Create a free **Turso** database; note its `libsql://...` URL and an auth token.
3. Push this repo to GitHub and import it in Vercel.
4. Add environment variables: `JWT_SECRET`, `MONGO_URI`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`.
5. Deploy. `api/index.js` exposes the Express app as a serverless function and `vercel.json` routes `/api/*` to it; `public/` is served statically. Tables are created automatically on first request.

## API (all but auth need `Authorization: Bearer <token>`)

- `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`, `PUT /api/auth/goals`
- `GET/POST /api/workouts`, `DELETE /api/workouts/:id`
- `GET/POST /api/meals`, `DELETE /api/meals/:id`
- `GET/POST /api/weight`, `DELETE /api/weight/:id`
- `GET /api/stats`

## Structure

```
server.js            Express app (exported for Vercel, listens when run directly)
api/index.js         Vercel serverless entry
vercel.json          Routes /api/* to the function
db/sql.js            SQL schema + helpers (libSQL)
db/mongo.js          Mongoose models + connection
middleware/auth.js   JWT helpers
routes/              auth, workouts, meals, weight, stats
public/              index.html, style.css, app.js (no build step)
```
