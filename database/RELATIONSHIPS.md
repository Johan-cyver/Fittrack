# Database relationship notes

## MySQL
- `members` is the parent entity for memberships, payments and class bookings.
- `membership_plans` is the catalogue; `memberships` resolves the member-to-plan relationship over time.
- `payments` references the member and optionally the membership paid for.
- `trainers` is referenced by `fitness_classes`.
- `class_assignments` resolves the many-to-many relationship between members and classes. A unique key prevents duplicate bookings.

## MongoDB
| Collection | Variable data | SQL relationship |
|---|---|---|
| workout_logs | activities, durations, calories, exercises, sets, notes | `member_id -> members.id` |
| progress_logs | weight, measurements, notes, timestamped snapshots | `member_id -> members.id` |
| routines | exercises, schedules, targets | `member_id -> members.id` |
| activity_logs | flexible event metadata | `member_id -> members.id` |
| feedback | rating, comments, category, context | `member_id -> members.id` |

The cross-database member reference is an application-level relationship, not a foreign key: MongoDB and MySQL do not share a relational transaction manager.
