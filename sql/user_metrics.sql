-- One row per assigned user with every metric the analysis needs.
-- Unit of randomization = unit of analysis = user, so standard errors stay simple.
WITH funnel AS (
    SELECT user_id,
           countIf(event = 'quote_started') > 0  AS started,
           countIf(event = 'quotes_viewed') > 0  AS viewed,
           countIf(event = 'policy_bound') > 0   AS bound
    FROM exp.events
    GROUP BY user_id
),
pol AS (
    SELECT user_id,
           max(vehicle_mismatch)                                                   AS vehicle_mismatch,
           countIf(cancelled_at IS NOT NULL AND cancelled_at < bound_at + INTERVAL 30 DAY) > 0 AS cancelled_30d,
           sumIf(annual_premium * {commission_rate},
                 cancelled_at IS NULL OR cancelled_at >= bound_at + INTERVAL 30 DAY)  AS retained_commission
    FROM exp.policies
    GROUP BY user_id
)
SELECT u.user_id                                  AS user_id,
       u.arm                                      AS arm,
       dateDiff('day', toDate('{start}'), toDate(u.assigned_at)) AS day,
       u.device                                   AS device,
       u.user_type                                AS user_type,
       u.state                                    AS state,
       u.prior_quotes_90d                         AS prior_quotes_90d,
       toUInt8(f.started)                         AS started,
       toUInt8(f.viewed)                          AS viewed,
       toUInt8(f.bound)                           AS bound,
       toUInt8(ifNull(p.cancelled_30d, 0))        AS cancelled_30d,
       toUInt8(f.bound AND NOT ifNull(p.cancelled_30d, 0)) AS retained_policy,
       ifNull(p.vehicle_mismatch, 0)              AS vehicle_mismatch,
       round(ifNull(p.retained_commission, 0), 2) AS retained_commission
FROM exp.users AS u
LEFT JOIN funnel AS f USING (user_id)
LEFT JOIN pol AS p USING (user_id)
ORDER BY u.user_id
