-- 1. Current memberships with member and plan details
SELECT m.id AS member_id, m.name, p.name AS plan_name,
       ms.start_date, ms.end_date, ms.status
FROM memberships ms
JOIN members m ON m.id = ms.member_id
JOIN membership_plans p ON p.id = ms.plan_id
WHERE ms.status = 'active' AND ms.end_date >= CURRENT_DATE
ORDER BY ms.end_date;

-- 2. Monthly collected revenue
SELECT DATE_FORMAT(paid_at, '%Y-%m') AS revenue_month,
       COUNT(*) AS payment_count, SUM(amount) AS total_revenue
FROM payments
WHERE status = 'paid'
GROUP BY DATE_FORMAT(paid_at, '%Y-%m')
ORDER BY revenue_month DESC;

-- 3. Trainer schedule with class booking counts
SELECT t.id AS trainer_id, t.name AS trainer,
       c.id AS class_id, c.title, c.starts_at, c.capacity,
       SUM(CASE WHEN a.status IN ('booked','attended') THEN 1 ELSE 0 END) AS occupied_seats
FROM fitness_classes c
LEFT JOIN trainers t ON t.id = c.trainer_id
LEFT JOIN class_assignments a ON a.class_id = c.id
WHERE c.status = 'scheduled'
GROUP BY t.id, t.name, c.id, c.title, c.starts_at, c.capacity
ORDER BY c.starts_at;

-- 4. Members without a current active membership
SELECT m.id, m.name, m.email
FROM members m
WHERE m.role = 'member'
  AND NOT EXISTS (
    SELECT 1 FROM memberships ms
    WHERE ms.member_id = m.id
      AND ms.status = 'active'
      AND ms.end_date >= CURRENT_DATE
  );

-- 5. Class availability
SELECT c.id, c.title, c.starts_at, c.capacity,
       c.capacity - COUNT(CASE WHEN a.status IN ('booked','attended') THEN 1 END) AS seats_remaining
FROM fitness_classes c
LEFT JOIN class_assignments a ON a.class_id = c.id
WHERE c.status = 'scheduled'
GROUP BY c.id, c.title, c.starts_at, c.capacity
ORDER BY c.starts_at;
