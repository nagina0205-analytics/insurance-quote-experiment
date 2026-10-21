-- Funnel by arm, counted in distinct users, for the analysis window only.
SELECT u.arm                                                AS arm,
       uniqExact(u.user_id)                                 AS users,
       uniqExactIf(e.user_id, e.event = 'quote_started')    AS started,
       uniqExactIf(e.user_id, e.event = 'quotes_viewed')    AS viewed,
       uniqExactIf(e.user_id, e.event = 'policy_bound')     AS bound
FROM exp.users AS u
LEFT JOIN exp.events AS e ON e.user_id = u.user_id
WHERE toDate(u.assigned_at) >= toDate('{window_start}')
GROUP BY arm
ORDER BY arm
