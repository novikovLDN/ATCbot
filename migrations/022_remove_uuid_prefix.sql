UPDATE subscriptions
SET uuid = regexp_replace(uuid, '^(stage-|prod-|test-)', '')
WHERE uuid ~ '^(stage-|prod-|test-)';