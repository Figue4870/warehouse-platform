WITH transfers AS (
    SELECT DISTINCT transfer_id, state, duration_seconds
    FROM "${database}"."transfers"
    WHERE dt >= CAST(date_add('day', -${days}, current_date) AS varchar)
)
SELECT state, count(*) AS transferencias, avg(duration_seconds) AS duracion_media_segundos
FROM transfers
GROUP BY state
ORDER BY state
