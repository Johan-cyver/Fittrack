// Mongo side: flexible documents (exercise sets inside a workout, meals with variable food items)
const mongoose = require('mongoose');

const workoutDetailSchema = new mongoose.Schema(
  {
    userId: { type: Number, required: true, index: true },
    workoutId: { type: Number, required: true, unique: true }, // links to SQL workouts.id
    exercises: [
      {
        name: { type: String, required: true, trim: true },
        sets: [{ reps: Number, weightKg: Number, durationSec: Number }],
        notes: String,
      },
    ],
    notes: String,
  },
  { timestamps: true }
);

const mealSchema = new mongoose.Schema(
  {
    userId: { type: Number, required: true, index: true },
    date: { type: String, required: true, index: true }, // YYYY-MM-DD
    mealType: { type: String, enum: ['breakfast', 'lunch', 'dinner', 'snack'], default: 'snack' },
    items: [
      {
        name: { type: String, required: true, trim: true },
        quantity: { type: String, trim: true },
        calories: { type: Number, default: 0, min: 0 },
        proteinG: { type: Number, default: 0, min: 0 },
        carbsG: { type: Number, default: 0, min: 0 },
        fatG: { type: Number, default: 0, min: 0 },
      },
    ],
    totalCalories: { type: Number, default: 0 },
  },
  { timestamps: true }
);

mealSchema.pre('validate', function (next) {
  this.totalCalories = (this.items || []).reduce((s, i) => s + (i.calories || 0), 0);
  next();
});

// `mongoose.models.X ||` avoids "OverwriteModelError" when a serverless function re-loads this file
const WorkoutDetail = mongoose.models.WorkoutDetail || mongoose.model('WorkoutDetail', workoutDetailSchema);
const Meal = mongoose.models.Meal || mongoose.model('Meal', mealSchema);

// Cached connection so warm serverless invocations reuse it
let connecting = null;
async function connectMongo() {
  if (mongoose.connection.readyState === 1) return;
  if (!connecting) {
    connecting = mongoose
      .connect(process.env.MONGO_URI || 'mongodb://127.0.0.1:27017/fittrack', { serverSelectionTimeoutMS: 8000 })
      .then(() => console.log('MongoDB connected'))
      .catch((e) => {
        connecting = null;
        throw e;
      });
  }
  await connecting;
}

module.exports = { connectMongo, WorkoutDetail, Meal };
