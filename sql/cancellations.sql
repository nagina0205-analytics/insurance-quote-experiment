-- 30-day cancellations among bound policies, split by where the vehicle details came from.
SELECT u.arm                       AS arm,
       p.vehicle_mismatch          AS vehicle_mismatch,
       count()                     AS policies,
       countIf(p.cancelled_at IS NOT NULL AND p.cancelled_at < p.bound_at + INTERVAL 30 DAY) AS cancelled_30d,
       round(cancelled_30d / policies, 4) AS cancel_rate,
       round(avg(p.annual_premium), 2)    AS avg_premium
FROM exp.policies AS p
INNER JOIN exp.users AS u ON u.user_id = p.user_id
WHERE toDate(u.assigned_at) >= toDate('{window_start}')
GROUP BY arm, vehicle_mismatch
ORDER BY arm, vehicle_mismatch
