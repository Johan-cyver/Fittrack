// Run these examples in mongosh after selecting the FitTrack database.
// member_id is the SQL members.id value stored as an application-level reference.

// 1. Workout volume by activity for one member
db.workout_logs.aggregate([
  { $match: { member_id: 123 } },
  { $group: {
      _id: "$activity",
      sessions: { $sum: 1 },
      totalMinutes: { $sum: { $ifNull: ["$duration_min", 0] } },
      totalCalories: { $sum: { $ifNull: ["$calories", 0] } }
  }},
  { $sort: { sessions: -1 } }
]);

// 2. Chronological progress history
db.progress_logs.find({ member_id: 123 }).sort({ created_at: 1 });

// 3. Routines with nested exercise structures
db.routines.find({ member_id: 123 }).sort({ created_at: -1 });

// 4. Feedback summary by category
db.feedback.aggregate([
  { $match: { member_id: 123 } },
  { $group: { _id: "$category", entries: { $sum: 1 }, averageRating: { $avg: "$rating" } } }
]);

// Note: MongoDB cannot enforce a foreign key to MySQL. The Flask API scopes
// documents to the authenticated member and stores that member's SQL ID.
