-- Plus tariff: second vless link (basic_link in vpn_key, plus_link in vpn_key_plus)
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS vpn_key_plus TEXT;
