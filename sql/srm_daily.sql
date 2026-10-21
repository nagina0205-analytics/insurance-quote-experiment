-- Users logged per arm, per day and device. A sample ratio mismatch shows up here first.
SELECT toDate(assigned_at)                     AS day,
       device,
       countIf(arm = 'control')                AS control_users,
       countIf(arm = 'treatment')              AS treatment_users,
       round(treatment_users / (control_users + treatment_users), 4) AS treatment_share
FROM exp.users
GROUP BY day, device
ORDER BY day, device
