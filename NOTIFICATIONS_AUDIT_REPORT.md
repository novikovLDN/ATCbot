# ================================
# NOTIFICATIONS SYSTEM AUDIT REPORT
# ================================
# Branch: stage
# Date: 2024
# Type: READ-ONLY AUDIT (NO CODE CHANGES)
# ================================

## SECTION 1 — GLOBAL OVERVIEW

### Total Notification Types: **~35 distinct notification types**

### High-Level Grouping:

1. **Payment Notifications** (5 types)
   - Payment approved (success)
   - Payment rejected
   - Balance topup success
   - Balance purchase success/renewal
   - Payment pending

2. **Trial Notifications** (9 types)
   - Trial activation
   - Trial expiration
   - Trial reminders (6 scheduled: 6h, 18h, 30h, 42h, 54h, 60h, 71h)
   - Smart offer after trial

3. **Subscription Lifecycle** (12 types)
   - Smart notifications (9 types: no traffic 20m/24h, first connection, 3 days usage, 7/3 days before expiry, expiry day, expired 24h, VIP offer)
   - Reminders (3 types: 3 days, 24h, 3h before expiry)
   - Auto-renewal success
   - Auto-renewal insufficient balance

4. **Referral Notifications** (1 type)
   - Referral cashback notification

5. **Admin Notifications** (2 types)
   - Degraded mode alert
   - Recovery notification

6. **System/Error Messages** (6+ types)
   - Service unavailable
   - Error messages (various)
   - Profile messages
   - Menu responses

### User-Initiated vs System-Initiated:

- **User-Initiated**: ~5 types (menu responses, profile views, payment confirmations)
- **System-Initiated**: ~30 types (scheduled reminders, trial notifications, auto-renewal, referral rewards, admin alerts)

### Blocking vs Informational:

- **Blocking User Flow**: 0 types (all notifications are informational; errors may block but are not "notifications")
- **Informational**: All 35 types

---

## SECTION 2 — DETAILED NOTIFICATION INVENTORY

### 1. Payment Approved (Success)
- **Source**: `handlers.py:4172, 4282, 5053`
- **Trigger**: After successful payment finalization (`finalize_purchase` completes)
- **Trigger Type**: Payment webhook / user action
- **Recipient**: User
- **Message Type**: Text + inline keyboard
- **Buttons**: YES
  - Button: "📋 Скопировать ключ" → `callback_data="copy_key"`
  - Button: "🔌 Перейти к подключению" → `callback_data="go_to_connection"`
- **Localization**: YES (ru, en, uz, tj)
- **Idempotency**: NO (can be sent multiple times if payment processed multiple times)
- **Criticality**: CRITICAL (affects access)

### 2. Payment Rejected
- **Source**: `handlers.py:8425`
- **Trigger**: Payment verification fails
- **Trigger Type**: Payment webhook
- **Recipient**: User
- **Message Type**: Plain text
- **Buttons**: NO
- **Localization**: YES
- **Idempotency**: NO
- **Criticality**: CRITICAL

### 3. Balance Purchase Success (New Subscription)
- **Source**: `handlers.py:2983-2989`
- **Trigger**: Balance purchase completes successfully (first-time subscription)
- **Trigger Type**: User action (callback)
- **Recipient**: User
- **Message Type**: Text + inline keyboard
- **Buttons**: YES (same as payment approved)
- **Localization**: Partial (hardcoded Russian with HTML)
- **Idempotency**: NO
- **Criticality**: CRITICAL

### 4. Balance Purchase Success (Renewal)
- **Source**: `handlers.py:2974-2980`
- **Trigger**: Balance purchase completes successfully (renewal)
- **Trigger Type**: User action (callback)
- **Recipient**: User
- **Message Type**: Text + inline keyboard
- **Buttons**: YES
- **Localization**: Partial (hardcoded Russian)
- **Idempotency**: NO
- **Criticality**: CRITICAL

### 5. Balance Topup Success
- **Source**: `handlers.py:3949-3953`
- **Trigger**: Balance topup via Telegram Payments completes
- **Trigger Type**: Payment webhook
- **Recipient**: User
- **Message Type**: Plain text
- **Buttons**: NO
- **Localization**: YES
- **Idempotency**: NO
- **Criticality**: IMPORTANT

### 6. Trial Activation
- **Source**: `handlers.py:1664-1680`
- **Trigger**: User activates trial period
- **Trigger Type**: User action (callback)
- **Recipient**: User
- **Message Type**: Text + inline keyboard
- **Buttons**: YES (instruction buttons)
- **Localization**: YES
- **Idempotency**: YES (trial can only be activated once)
- **Criticality**: CRITICAL

### 7. Trial Expiration
- **Source**: `trial_notifications.py:449-458`
- **Trigger**: Trial expires (`trial_expires_at <= now`)
- **Trigger Type**: Scheduled task (every 5 minutes)
- **Recipient**: User
- **Message Type**: Plain text
- **Buttons**: NO
- **Localization**: YES
- **Idempotency**: YES (uses `trial_completed_sent` flag)
- **Criticality**: IMPORTANT

### 8-14. Trial Reminders (6h, 18h, 30h, 42h, 54h, 60h, 71h)
- **Source**: `trial_notifications.py:194-283`
- **Trigger**: Scheduled based on hours since trial activation
- **Trigger Type**: Scheduled task (every 5 minutes)
- **Recipient**: User
- **Message Type**: Text (with button for 60h, 71h)
- **Buttons**: YES (for 60h, 71h only) → `callback_data="menu_buy_vpn"`
- **Localization**: YES
- **Idempotency**: YES (uses `trial_notif_{hours}h_sent` flags)
- **Criticality**: IMPORTANT

### 15. Smart Offer After Trial
- **Source**: `trial_notifications.py:404-435`
- **Trigger**: Trial expires, user has no paid subscription
- **Trigger Type**: Scheduled task
- **Recipient**: User
- **Message Type**: Text + inline keyboard
- **Buttons**: YES
  - "🔐 Купить доступ" → `callback_data="menu_buy_vpn"`
  - "👤 Мой профиль" → `callback_data="menu_profile"`
- **Localization**: Partial (hardcoded Russian)
- **Idempotency**: YES (uses `smart_offer_sent` flag)
- **Criticality**: IMPORTANT

### 16. Smart Notification: No Traffic 20 Minutes
- **Source**: `reminders.py:172-181`
- **Trigger**: 20 minutes after activation, no traffic detected
- **Trigger Type**: Scheduled task (every 45 minutes)
- **Recipient**: User
- **Message Type**: Plain text
- **Buttons**: NO
- **Localization**: YES
- **Idempotency**: YES (uses `smart_notif_no_traffic_20m_sent` flag)
- **Criticality**: INFORMATIONAL

### 17. Smart Notification: No Traffic 24 Hours
- **Source**: `reminders.py:184-196`
- **Trigger**: 24 hours after activation, no traffic detected
- **Trigger Type**: Scheduled task
- **Recipient**: User
- **Message Type**: Plain text
- **Buttons**: NO
- **Localization**: YES
- **Idempotency**: YES (uses `smart_notif_no_traffic_24h_sent` flag)
- **Criticality**: INFORMATIONAL

### 18. Smart Notification: First Connection
- **Source**: `reminders.py:199-213`
- **Trigger**: First traffic detected (1-2 hours after first traffic)
- **Trigger Type**: Scheduled task
- **Recipient**: User
- **Message Type**: Plain text
- **Buttons**: NO
- **Localization**: YES
- **Idempotency**: YES (uses `smart_notif_first_connection_sent` flag)
- **Criticality**: INFORMATIONAL

### 19. Smart Notification: 3 Days Usage
- **Source**: `reminders.py:216-227`
- **Trigger**: 3 days after first traffic
- **Trigger Type**: Scheduled task
- **Recipient**: User
- **Message Type**: Plain text
- **Buttons**: NO
- **Localization**: YES
- **Idempotency**: YES (uses `smart_notif_3days_usage_sent` flag)
- **Criticality**: INFORMATIONAL

### 20. Smart Notification: 7 Days Before Expiry
- **Source**: `reminders.py:230-242`
- **Trigger**: 7 days before subscription expiry
- **Trigger Type**: Scheduled task
- **Recipient**: User
- **Message Type**: Text + inline keyboard
- **Buttons**: YES → `callback_data="menu_buy_vpn"`
- **Localization**: YES
- **Idempotency**: YES (uses `smart_notif_7days_before_expiry_sent` flag)
- **Criticality**: IMPORTANT

### 21. Smart Notification: 3 Days Before Expiry
- **Source**: `reminders.py:245-256`
- **Trigger**: 3 days before subscription expiry
- **Trigger Type**: Scheduled task
- **Recipient**: User
- **Message Type**: Text + inline keyboard
- **Buttons**: YES → `callback_data="menu_buy_vpn"`
- **Localization**: YES
- **Idempotency**: YES (uses `smart_notif_3days_before_expiry_sent` flag)
- **Criticality**: IMPORTANT

### 22. Smart Notification: Expiry Day
- **Source**: `reminders.py:259-271`
- **Trigger**: On expiry day (8:00-12:00)
- **Trigger Type**: Scheduled task
- **Recipient**: User
- **Message Type**: Text + inline keyboard
- **Buttons**: YES → `callback_data="menu_buy_vpn"`
- **Localization**: YES
- **Idempotency**: YES (uses `smart_notif_expiry_day_sent` flag)
- **Criticality**: IMPORTANT

### 23. Smart Notification: Expired 24 Hours
- **Source**: `reminders.py:274-286`
- **Trigger**: 24 hours after expiry
- **Trigger Type**: Scheduled task
- **Recipient**: User
- **Message Type**: Text + inline keyboard
- **Buttons**: YES → `callback_data="menu_buy_vpn"`
- **Localization**: YES
- **Idempotency**: YES (uses `smart_notif_expired_24h_sent` flag)
- **Criticality**: IMPORTANT

### 24. Smart Notification: VIP Offer
- **Source**: `reminders.py:289-302`
- **Trigger**: 14 days after first traffic, active user
- **Trigger Type**: Scheduled task
- **Recipient**: User
- **Message Type**: Text + inline keyboard
- **Buttons**: YES → `callback_data="menu_vip_access"`
- **Localization**: YES
- **Idempotency**: YES (uses `smart_notif_vip_offer_sent` flag)
- **Criticality**: INFORMATIONAL

### 25-27. Reminders: 3 Days, 24h, 3h Before Expiry
- **Source**: `reminders.py:393-447`
- **Trigger**: Time-based (3 days, 24h, 3h before expiry)
- **Trigger Type**: Scheduled task (every 45 minutes)
- **Recipient**: User
- **Message Type**: Text + inline keyboard
- **Buttons**: YES → `callback_data="menu_buy_vpn"`
- **Localization**: YES
- **Idempotency**: YES (uses `reminder_3d_sent`, `reminder_24h_sent`, `reminder_3h_sent` flags)
- **Criticality**: IMPORTANT

### 28. Auto-Renewal Success
- **Source**: `auto_renewal.py:242-253`
- **Trigger**: Auto-renewal completes successfully
- **Trigger Type**: Scheduled task (every 5-15 minutes)
- **Recipient**: User
- **Message Type**: Plain text
- **Buttons**: NO
- **Localization**: YES
- **Idempotency**: YES (uses `last_auto_renewal_at` timestamp)
- **Criticality**: CRITICAL

### 29. Referral Cashback Notification
- **Source**: `handlers.py:999-1064`
- **Trigger**: Referral reward processed after purchase
- **Trigger Type**: Payment webhook / user action
- **Recipient**: Referrer (not buyer)
- **Message Type**: Plain text
- **Buttons**: NO
- **Localization**: NO (hardcoded Russian)
- **Idempotency**: YES (reward processed once per purchase_id)
- **Criticality**: IMPORTANT

### 30. Admin: Degraded Mode Alert
- **Source**: `admin_notifications.py:20-59`
- **Trigger**: Bot enters degraded mode (DB unavailable)
- **Trigger Type**: System startup / DB failure
- **Recipient**: Admin
- **Message Type**: Plain text (Markdown)
- **Buttons**: NO
- **Localization**: NO (hardcoded Russian)
- **Idempotency**: YES (uses global flag `_degraded_notification_sent`)
- **Criticality**: CRITICAL (for operations)

### 31. Admin: Recovery Notification
- **Source**: `admin_notifications.py:63-98`
- **Trigger**: Bot recovers from degraded mode
- **Trigger Type**: DB recovery
- **Recipient**: Admin
- **Message Type**: Plain text (Markdown)
- **Buttons**: NO
- **Localization**: NO (hardcoded Russian)
- **Idempotency**: YES (uses global flag `_recovered_notification_sent`)
- **Criticality**: CRITICAL (for operations)

---

## SECTION 3 — USER JOURNEY MAPPING

### New User / Trial
1. **Trial Activation** → Notification: Trial activated (with VPN key)
2. **6h after activation** → Trial reminder (no button)
3. **18h after activation** → Trial reminder (no button)
4. **20m after activation, no traffic** → Smart notification: no traffic 20m
5. **30h after activation** → Trial reminder (no button)
6. **24h after activation, no traffic** → Smart notification: no traffic 24h
7. **First traffic detected** → Smart notification: first connection (1-2h after traffic)
8. **42h after activation** → Trial reminder (no button)
9. **54h after activation** → Trial reminder (no button)
10. **60h after activation** → Trial reminder (with button)
11. **71h after activation** → Trial reminder (with button)
12. **Trial expires** → Trial expiration notification
13. **If no paid subscription** → Smart offer with promo code

**Possible Overlaps**: Trial reminders and smart notifications can overlap if user activates trial but doesn't use it.

### Active Subscriber
1. **3 days after first traffic** → Smart notification: 3 days usage
2. **7 days before expiry** → Smart notification: 7 days before expiry (with button)
3. **3 days before expiry** → Smart notification: 3 days before expiry (with button) + Reminder: 3 days (with button)
4. **24h before expiry** → Reminder: 24h (with button)
5. **3h before expiry** → Reminder: 3h (with button)
6. **Expiry day (8:00-12:00)** → Smart notification: expiry day (with button)
7. **24h after expiry** → Smart notification: expired 24h (with button)
8. **14 days after first traffic, active user** → Smart notification: VIP offer (with button)

**Possible Overlaps**: Multiple reminders can be sent simultaneously if scheduler runs multiple times before flags are set.

### Subscription Renewal
1. **Payment success** → Payment approved notification (with VPN key)
2. **If referral reward** → Referral cashback notification (to referrer)
3. **Auto-renewal success** → Auto-renewal success notification

**No Overlaps**: Renewal notifications are distinct.

### Balance Topup
1. **Topup success** → Balance topup success notification
2. **If referral reward** → Referral cashback notification (to referrer)

**No Overlaps**: Topup notifications are distinct.

### Referral Activity
1. **Referred user makes purchase** → Referral cashback notification (to referrer only)

**No Overlaps**: Referral notifications are distinct.

---

## SECTION 4 — BUTTON & INTERACTION AUDIT

### Notifications WITH Buttons:

1. **Payment Approved** → "📋 Скопировать ключ" (`copy_key`), "🔌 Перейти к подключению" (`go_to_connection`)
2. **Balance Purchase Success** → Same as payment approved
3. **Trial Activation** → Instruction buttons
4. **Trial Reminders (60h, 71h)** → "🔐 Купить доступ" (`menu_buy_vpn`)
5. **Smart Offer After Trial** → "🔐 Купить доступ" (`menu_buy_vpn`), "👤 Мой профиль" (`menu_profile`)
6. **Smart Notifications (7d, 3d, expiry day, expired 24h)** → "🔁 Продлить доступ" (`menu_buy_vpn`)
7. **Reminders (3d, 24h, 3h)** → "🔁 Продлить доступ" (`menu_buy_vpn`)
8. **VIP Offer** → "👑 Улучшить уровень доступа" (`menu_vip_access`)

### Orphaned Buttons:
- **NONE** — All buttons have valid callbacks

### Dead Callbacks:
- **NONE** — All callbacks are handled

### UX Issues:

1. **Balance Purchase Success**: Hardcoded Russian text, not localized
2. **Referral Cashback**: Hardcoded Russian text, not localized
3. **Admin Notifications**: Hardcoded Russian text
4. **Trial Smart Offer**: Hardcoded Russian text with promo code "YABX30"
5. **Multiple Reminder Overlaps**: User can receive both smart notification and reminder for same expiry window

### Notifications WITHOUT Buttons That Should Have Them:

1. **Auto-Renewal Success** → Should have button to view profile or disable auto-renewal
2. **Balance Topup Success** → Should have button to buy subscription or view profile
3. **Trial Expiration** → Should have button to buy subscription
4. **Payment Rejected** → Should have button to retry payment or contact support

---

## SECTION 5 — TECHNICAL RISK ANALYSIS

### 1. Duplicate Notifications
- **Risk**: HIGH
- **Location**: Payment success notifications, balance purchase success
- **Cause**: No idempotency check before sending; if `finalize_purchase` is called multiple times, notification sent multiple times
- **Impact**: User receives duplicate VPN keys, confusion

### 2. Race Conditions in Schedulers
- **Risk**: MEDIUM
- **Location**: `reminders.py:send_smart_notifications`, `trial_notifications.py:process_trial_notifications`
- **Cause**: Multiple scheduler runs before flags are set; anti-spam check (60 minutes) may not prevent all duplicates
- **Impact**: User receives multiple reminders for same event

### 3. Missing Idempotency
- **Risk**: HIGH
- **Location**: Payment success notifications (handlers.py:4172, 4282)
- **Cause**: No check if notification already sent; relies on payment idempotency only
- **Impact**: Duplicate notifications if payment processed twice

### 4. Notifications Sent After Failed Transactions
- **Risk**: LOW
- **Location**: All payment-related notifications
- **Cause**: Notifications sent AFTER transaction commit; if commit fails, notification not sent (correct behavior)
- **Impact**: None (correct behavior)

### 5. Notifications Sent Before Transaction Commit
- **Risk**: NONE
- **Location**: All notifications
- **Cause**: All notifications sent after `finalize_purchase` / `finalize_balance_purchase` completes (transaction already committed)
- **Impact**: None (correct behavior)

### 6. Notifications Sent Without User Existence Check
- **Risk**: MEDIUM
- **Location**: Scheduled notifications (reminders, trial notifications)
- **Cause**: User may have deleted account or blocked bot; handled by `TelegramForbiddenError` catch
- **Impact**: Logged warnings, no crash

### 7. Notifications That Can Spam Users
- **Risk**: MEDIUM
- **Location**: 
  - Trial reminders (7 notifications in 72 hours)
  - Smart notifications + reminders (can overlap)
  - Multiple expiry reminders (3d, 24h, 3h, expiry day, expired 24h)
- **Cause**: Multiple notification systems (smart notifications + reminders) can send overlapping messages
- **Impact**: User receives too many notifications, may block bot

### 8. Hardcoded Text (Not Localized)
- **Risk**: LOW (UX issue, not technical)
- **Location**: 
  - Balance purchase success (handlers.py:2974-2989)
  - Referral cashback (handlers.py:1039-1047)
  - Admin notifications (admin_notifications.py)
  - Trial smart offer (trial_notifications.py:405-413)
- **Impact**: Non-Russian users see Russian text

### 9. Missing Error Handling
- **Risk**: LOW
- **Location**: Most notification sends have try/except, but some may not handle all Telegram API errors
- **Impact**: Potential crashes if Telegram API returns unexpected errors

---

## SECTION 6 — CLEANUP & IMPROVEMENT CANDIDATES

### Notifications Likely Obsolete:
- **NONE** — All notifications serve a purpose

### Notifications That Overlap in Purpose:
1. **Smart Notification: 3 Days Before Expiry** + **Reminder: 3 Days** → Both sent at same time, redundant
2. **Smart Notification: 7 Days Before Expiry** + **Reminder: 3 Days** → Can overlap if scheduler timing aligns
3. **Smart Notification: Expiry Day** + **Reminder: 3h** → Can overlap on expiry day

### Notifications That Should Be Merged:
1. **Smart Notifications + Reminders** → Should be unified into single reminder system to avoid overlaps

### Notifications That Should Be Optional / Configurable:
1. **Smart Notification: No Traffic 20m** → May be too early, should be configurable
2. **Smart Notification: VIP Offer** → Should be optional (currently always sent to active users after 14 days)
3. **Trial Reminders (6h, 18h, 30h, 42h, 54h)** → Too many reminders, should be configurable or reduced

### Notifications That Should Be Moved After Transaction Commit:
- **ALL notifications are already sent after transaction commit** (correct behavior)

### Notifications That Should Be Disabled on STAGE:
1. **Admin: Degraded Mode Alert** → Should be disabled or use different admin ID for STAGE
2. **Auto-Renewal** → Should be disabled on STAGE (or use test mode)
3. **Referral Cashback** → Should be disabled on STAGE (or use test amounts)

### Additional Improvements:
1. **Add idempotency flags** for payment success notifications
2. **Unify reminder systems** (smart notifications + reminders) to prevent overlaps
3. **Localize all hardcoded texts** (balance purchase, referral cashback, admin notifications, trial smart offer)
4. **Add buttons** to auto-renewal success, balance topup success, trial expiration, payment rejected
5. **Reduce trial reminder frequency** (currently 7 reminders in 72 hours is excessive)
6. **Add notification preferences** (allow users to opt-out of non-critical notifications)

---

## SECTION 7 — SUMMARY FOR PRODUCT DECISION

### Is the current notification system consistent?
**PARTIALLY** — There are two parallel reminder systems (smart notifications + reminders) that can overlap. Some notifications are localized, others are hardcoded Russian. Payment notifications have different formats for balance vs card payments.

### Is it safe for production?
**MOSTLY YES, WITH RISKS** — 
- ✅ Transaction safety: All notifications sent after commit
- ✅ Error handling: Most notifications have try/except
- ⚠️ Duplicate risk: Payment success notifications lack idempotency
- ⚠️ Spam risk: Multiple reminder systems can send overlapping messages
- ⚠️ Race conditions: Scheduler timing can cause duplicate reminders

### Does it align with a premium paid product UX?
**PARTIALLY** — 
- ✅ Good: Comprehensive reminder system, helpful smart notifications
- ❌ Issues: Too many trial reminders (7 in 72h), hardcoded Russian text, missing buttons on some notifications, overlapping reminder systems

### TOP 5 Highest-Impact Notification Problems:

1. **Duplicate Payment Success Notifications** (HIGH RISK)
   - Problem: No idempotency check before sending payment success notification
   - Impact: User receives duplicate VPN keys, confusion, support tickets
   - Fix: Add idempotency flag (e.g., `payment_notification_sent` in payments table)

2. **Overlapping Reminder Systems** (MEDIUM RISK)
   - Problem: Smart notifications + reminders can send overlapping messages
   - Impact: User receives multiple reminders for same event, may block bot
   - Fix: Unify into single reminder system or add deduplication logic

3. **Excessive Trial Reminders** (MEDIUM RISK)
   - Problem: 7 trial reminders in 72 hours is excessive
   - Impact: User annoyance, may block bot during trial
   - Fix: Reduce to 3-4 reminders or make configurable

4. **Hardcoded Russian Text** (LOW RISK, HIGH UX IMPACT)
   - Problem: Balance purchase success, referral cashback, admin notifications, trial smart offer not localized
   - Impact: Non-Russian users see Russian text, poor UX
   - Fix: Move all texts to localization system

5. **Missing Buttons on Critical Notifications** (LOW RISK, MEDIUM UX IMPACT)
   - Problem: Auto-renewal success, balance topup success, trial expiration, payment rejected lack action buttons
   - Impact: User must navigate manually, poor UX
   - Fix: Add relevant buttons (view profile, buy subscription, retry payment, contact support)

---

## END OF AUDIT REPORT
