# -*- coding: utf-8 -*-
"""ar strings."""

LANG = {
    'admin.access_denied': 'صلاحيات الوصول غير كافية',
    'admin.access_revoked': '✅ تم إلغاء الوصول',
    'admin.action_without_notification': 'تم تنفيذ الإجراء دون إشعار.',
    'admin.activation_error_action': 'التفعيل اليدوي مطلوب.',
    'admin.activation_error_attempts': 'المحاولات: {attempts}/{max_attempts}',
    'admin.activation_error_error': 'الخطأ: `{error_msg}`',
    'admin.activation_error_status': 'تم تعليم الاشتراك كـ `failed`.',
    'admin.activation_error_subscription_id': 'معرف الاشتراك: `{subscription_id}`',
    'admin.activation_error_title': '⚠️ **خطأ في تفعيل اشتراك VPN**',
    'admin.activation_error_user': 'المستخدم: `{telegram_id}`',
    'admin.analytics': '💰 التحليلات',
    'admin.approve': 'موافقة',
    'admin.audit': '📜 التدقيق',
    'admin.audit_empty': '📜 التدقيق\n\nالتدقيق فارغ. لم تُسجّل أي إجراءات.',
    'admin.back': '🔙 رجوع',
    'admin.back_to_analytics': '🔙 العودة للتحليلات',
    'admin.back_to_broadcast': '🔙 رجوع',
    'admin.back_to_keys': '🔙 رجوع',
    'admin.back_to_stats': '🔙 العودة للإحصائيات',
    'admin.broadcast': '📣 الإشعارات',
    'admin.cancel': '🔙 إلغاء',
    'admin.check_logs': 'خطأ. تحقق من السجلات.',
    'admin.confirm': '✅ تأكيد',
    'admin.copy_key': '📋 نسخ المفتاح',
    'admin.create_discount': '🎯 إنشاء خصم',
    'admin.credit_balance': '💰 رصيد الائتمان',
    'admin.credit_balance_prompt': '💰 منح الرصيد\n\nأدخل معرف Telegram أو اسم المستخدم:',
    'admin.credit_balance_user_prompt': '💰 منح الرصيد\n\nالمستخدم: {user_id}\n\nأدخل المبلغ بالروبل:',
    'admin.credit_positive_sum': '❌ يجب أن يكون المبلغ موجباً.\n\nأدخل المبلغ بالروبل:',
    'admin.credit_sum_error': 'خطأ في معالجة المبلغ. تحقق من السجلات.',
    'admin.credit_sum_format': '❌ تنسيق مبلغ غير صالح.\n\nأدخل رقماً (مثلاً: 500 أو 100.50):',
    'admin.credit_user_not_found': 'خطأ: المستخدم غير موجود. ابدأ من جديد.',
    'admin.dashboard': '📊 لوحة التحكم',
    'admin.dashboard_title': '🛠 Atlas Secure · لوحة التحكم\n\nاختر إجراء:',
    'admin.db_connection_failed': '❌ فشل الاتصال بقاعدة البيانات',
    'admin.db_unavailable': '❌ قاعدة البيانات غير متاحة',
    'admin.degraded_mode': '⚠️ **البوت يعمل بوضع متدهور**\n\nقاعدة البيانات غير متاحة.\n\n• البوت يعمل ويستجيب للأوامر\n• العمليات الحرجة محظورة',
    'admin.delete_discount': '❌ حذف الخصم',
    'admin.discount_already_exists': '❌ المستخدم لديه بالفعل خصم {percent}%.\n\nاحذف الخصم الموجود أولاً.',
    'admin.discount_assign_days_prompt': '🎯 تعيين خصم\n\nأدخل أيام الصلاحية (أو 0 غير محدود):',
    'admin.discount_assign_prompt': '🎯 تعيين خصم\n\nأدخل نسبة الخصم (1 إلى 99):',
    'admin.discount_created': '✅ تم تعيين خصم شخصي {percent}%\n\nصالح حتى: {expires}',
    'admin.discount_days_nonnegative': 'يجب أن تكون الأيام غير سالبة. حاول مرة أخرى:',
    'admin.discount_enter_1_99': 'أدخل رقماً من 1 إلى 99:',
    'admin.discount_enter_days': 'أدخل رقماً (أيام أو 0 غير محدود):',
    'admin.discount_error': '❌ خطأ في إنشاء الخصم',
    'admin.discount_expires_30': '30 يومًا',
    'admin.discount_expires_7': '7 أيام',
    'admin.discount_expires_unlimited': 'غير محدود',
    'admin.discount_manual': 'إدخال يدوي',
    'admin.discount_not_found': '❌ الخصم غير موجود أو محذوف',
    'admin.discount_percent_1_99': 'يجب أن تكون نسبة الخصم من 1 إلى 99. حاول مرة أخرى:',
    'admin.discount_removed': '✅ تم حذف الخصم الشخصي',
    'admin.enter_number': '❌ أدخل رقماً',
    'admin.enter_positive_number': '❌ أدخل رقماً موجباً',
    'admin.export': '📤 تصدير البيانات',
    'admin.export_error': 'خطأ في تصدير البيانات. تحقق من السجلات.',
    'admin.export_file_sent': '✅ تم إرسال الملف',
    'admin.export_invalid_type': 'نوع تصدير غير صالح',
    'admin.export_no_data': 'لا توجد بيانات للتصدير',
    'admin.export_prompt': '📤 تصدير البيانات\n\nاختر نوع التصدير:',
    'admin.export_subscriptions': '🔑 الاشتراكات النشطة',
    'admin.export_users': '👥 المستخدمون',
    'admin.forward': '➡️ للأمام',
    'admin.go_to_instruction': '🔌 الانتقال إلى التعليمات',
    'admin.grant_1_year': '🗓 منح 1 سنة',
    'admin.grant_access': '🟢 منح الوصول',
    'admin.grant_access_error': '❌ خطأ في منح الوصول: {error}',
    'admin.grant_custom': '⚙️ مخصص (يوم/ساعة/دقيقة)',
    'admin.grant_days_1': '1 يوم',
    'admin.grant_days_14': '14 يومًا',
    'admin.grant_days_7': '7 أيام',
    'admin.grant_days_prompt': 'اختر مدة الاشتراك:',
    'admin.grant_fail_no_keys': '❌ No free VPN keys available',
    'admin.grant_minutes_10': '⏱ 10 دقائق',
    'admin.grant_success': '✅ تم تفعيل الوصول لمدة {days} أيام.\n\nتم التفعيل من قبل المدير.',
    'admin.grant_unit_days': '📅 أيام',
    'admin.grant_unit_hours': '🕐 ساعات',
    'admin.grant_unit_minutes': '⏱ دقائق',
    'admin.grant_user_notification': '✅ You have been granted access to Atlas Secure for {days} days.\nVPN key: {vpn_key}\nExpires: {date}',
    'admin.grant_user_notification_10m': '⏱ تم تفعيل الوصول لمدة 10 دقائق.\n\nيمكنك الاتصال فوراً.\nسيُعلق الوصول تلقائياً عند انتهاء المدة.',
    'admin.grant_vip': '👑 منح VIP',
    'admin.incident_disable': '❌ Disable',
    'admin.incident_edit': '📝 تعديل النص',
    'admin.incident_edit_text': '📝 Edit text',
    'admin.incident_enable': '✅ Enable',
    'admin.incident_status_off': '⚪ Incident mode off',
    'admin.incident_status_on': '🟢 Incident mode active',
    'admin.incident_text_label': 'نص الحادث:',
    'admin.incident_text_prompt': 'Enter incident text (or send /cancel to cancel):',
    'admin.incident_title': '🚨 Incident',
    'admin.keys': '🔑 مفاتيح VPN',
    'admin.metrics': '📈 المقاييس',
    'admin.my_profile': '👤 ملفي الشخصي',
    'admin.next_page': '➡️ التالي',
    'admin.no_access': 'لا يوجد وصول',
    'admin.no_active_subscription': '❌ المستخدم ليس لديه اشتراك نشط',
    'admin.no_active_subscriptions_reissue': '❌ لا توجد اشتراكات نشطة لإعادة الإصدار',
    'admin.notify_no': '🔕 لا',
    'admin.notify_yes': '🔔 نعم',
    'admin.operation_cancelled': '❌ تم إلغاء العملية',
    'admin.payment_notification': '💰 New payment\nUser: @{username}\nTelegram ID: {telegram_id}\nTariff: {tariff} months\nPrice: {price} ₽',
    'admin.pending_activations_row': '{idx}. ID: `{subscription_id}` | User: `{telegram_id}` | المحاولات: {attempts} | منذ {pending_since}\n   الخطأ: `{error}`\n',
    'admin.pending_activations_title': '⚠️ **تفعيلات VPN معلقة**\n',
    'admin.pending_activations_top': '\n**أقدم 5:**\n',
    'admin.pending_activations_total': 'إجمالي المعلقة: **{count}**\n',
    'admin.prev': '⬅️ رجوع',
    'admin.promo_enter_text': 'يرجى إدخال رمز الترويج كنص.',
    'admin.promo_stats': '📊 إحصائيات العروض',
    'admin.recovered': '✅ **تم استعادة الخدمة**\n\nقاعدة البيانات متاحة مرة أخرى.\n\n• البوت يعمل بالكامل',
    'admin.referral_history': '📋 سجل الإحالة',
    'admin.referral_stats': '🤝 إحصائيات الإحالة',
    'admin.referral_top': '📈 أفضل المحيلين',
    'admin.refresh': '🔄 تحديث',
    'admin.reissue_all_keys': '🔄 إعادة إصدار جميع المفاتيح',
    'admin.reissue_bulk_error': '❌ خطأ في إعادة الإصدار الجماعي: {error}',
    'admin.reissue_error': 'خطأ في إعادة إصدار المفتاح. تحقق من السجلات.',
    'admin.reissue_for_user': '👤 إعادة إصدار للمستخدم',
    'admin.reissue_invalid_id': 'تنسيق telegram_id غير صالح. استخدم رقماً.',
    'admin.reissue_key': '🔁 إعادة إصدار المفتاح',
    'admin.reissue_success': 'تم إعادة إصدار المفتاح بنجاح',
    'admin.reissue_usage': 'الاستخدام: /reissue_key <telegram_id>',
    'admin.reissue_user_notification': '🔐 VPN key updated\n\nYour VPN connection key has been updated.\n\n🔑 <b>New key:</b>\n<code>{vpn_key}</code>\n\nUse this key in your VPN app.\n\nThe old key is no longer valid.',
    'admin.reject': 'رفض',
    'admin.revoke_access': '🔴 إلغاء الوصول',
    'admin.revoke_confirm_text': '❌ إلغاء الوصول\n\nإشعار المستخدم؟',
    'admin.revoke_fail_no_sub': '❌ User has no active subscription',
    'admin.revoke_success': '✅ تم إلغاء الوصول.\n\nتم إبلاغ المستخدم.',
    'admin.revoke_user_notification': '⛔ Your access to Atlas Secure has been revoked by the administrator.',
    'admin.revoke_vip': '❌ إلغاء VIP',
    'admin.search': '🔍 بحث',
    'admin.send': '✅ إرسال',
    'admin.sort_by_cashback': '💰 حسب الكاشباك',
    'admin.sort_by_invited': '👥 حسب المدعوين',
    'admin.sort_by_revenue': '📈 حسب الإيرادات',
    'admin.stats': '📊 الإحصائيات',
    'admin.subscription_history': '🧾 تاريخ الاشتراك',
    'admin.subscription_history_empty': '🧾 سجل الاشتراك\n\nسجل الاشتراك فارغ.',
    'admin.system': '🚨 النظام',
    'admin.test_first_purchase': '💰 اختبار إشعار أول شراء',
    'admin.test_menu': '🧪 الاختبارات',
    'admin.test_reminders': '⏰ اختبار التذكيرات',
    'admin.test_renewal': '🔄 اختبار إشعار التجديد',
    'admin.test_trial': '🎁 اختبار إشعار الت trial',
    'admin.unlimited': 'غير محدود',
    'admin.user': '👤 المستخدم',
    'admin.user_info_error': 'خطأ في الحصول على معلومات المستخدم. تحقق من السجلات.',
    'admin.user_not_found': '❌ المستخدم غير موجود',
    'admin.user_not_found_check_id': 'المستخدم غير موجود.\nتحقق من معرف Telegram أو اسم المستخدم.',
    'admin.user_prompt_enter_id': '👤 المستخدم\n\nأدخل معرف Telegram أو اسم المستخدم:',
    'admin.vip_already_assigned': 'VIP معيّن مسبقاً',
    'admin.vip_assign_error': '❌ خطأ في تعيين حالة VIP',
    'admin.vip_granted': '✅ تم منح حالة VIP',
    'admin.vip_not_found': '❌ حالة VIP غير موجودة أو ملغاة',
    'admin.vip_revoked': '✅ تم إلغاء حالة VIP',
    'broadcast._ab_stats': '📊 إحصائيات A/B',
    'broadcast._ab_stats_detail_error': 'خطأ في الحصول على إحصائيات A/B. تحقق من السجلات.',
    'broadcast._ab_stats_empty': '📊 إحصائيات A/B\n\nلم يتم العثور على اختبارات A/B.',
    'broadcast._ab_stats_error': 'خطأ في الحصول على قائمة اختبارات A/B. تحقق من السجلات.',
    'broadcast._ab_stats_select': '📊 إحصائيات A/B\n\nاختر الإشعار لعرض الإحصائيات:',
    'broadcast._ab_test': '🔬 اختبار A/B',
    'broadcast._confirm_send': '✅ إرسال',
    'broadcast._create': '➕ إنشاء إشعار',
    'broadcast._enter_message': 'أدخل نص الإشعار:',
    'broadcast._enter_title': 'أدخل عنوان الإشعار:',
    'broadcast._enter_variant_a': 'أدخل نص المتغير أ:',
    'broadcast._enter_variant_b': 'أدخل نص المتغير ب:',
    'broadcast._invalid_id': 'خطأ: معرف إشعار غير صالح.',
    'broadcast._normal': '📝 إشعار عادي',
    'broadcast._not_found': 'الإشعار غير موجود.',
    'broadcast._preview_confirm': '📋 معاينة الإشعار:\n\n{preview}\n\nتأكيد الإرسال:',
    'broadcast._report_partial': '⚠️ اكتمل البث مع أخطاء.\n\nالإجمالي: {total}\nالنجاح: {sent}\nالفشل: {failed}\n\nالمستخدمون الفاشلون:\n{failed_list}\n\n📝 ID: {broadcast_id}',
    'broadcast._report_success': '✅ اكتمل البث.\n\nالإجمالي: {total}\nالنجاح: {sent}\nالفشل: 0\n\n📝 ID: {broadcast_id}',
    'broadcast._section_title': '📣 الإشعارات\n\nاختر إجراءً:',
    'broadcast._segment_active': '🔐 الاشتراكات النشطة فقط',
    'broadcast._segment_all': '🌍 جميع المستخدمين',
    'broadcast._select_segment': 'اختر شريحة المستلمين:',
    'broadcast._select_type': 'اختر نوع الإشعار:',
    'broadcast._sending': '📤 جاري الإرسال...\n\nالمستخدمون: {total}\nيرجى الانتظار.',
    'broadcast._type_info': 'ℹ️ معلومات',
    'broadcast._type_maintenance': '🔧 صيانة',
    'broadcast._type_promo': '🎯 ترويجي',
    'broadcast._type_security': '🔒 أمان',
    'broadcast._no_sub_enter_text': 'أدخل النص (مستخدمون بدون اشتراك وتجربة فقط):',
    'broadcast._no_sub_preview': '📋 معاينة:\n\n{preview}\n\nالمستلمون: {total}\n\nتأكيد الإرسال؟',
    'broadcast._no_sub_sending': '📤 جاري الإرسال... المستلمون: {total}. يرجى الانتظار.',
    'broadcast._no_sub_completed': '✅ تم.\n\nالمستلمون: {total}\nتم: {sent}\nفشل: {failed}\nتخطي: {skipped}\nالمدة: {duration:.1f} ث.',
    'broadcast._no_sub_zero_recipients': 'لا يوجد مستلمون لهذا الجزء.',
    'broadcast._validation_ab_empty': 'خطأ: نصوص A و B غير مكتملة. ابدأ من جديد.',
    'broadcast._validation_incomplete': 'خطأ: بيانات غير كاملة. ابدأ من جديد.',
    'broadcast._validation_message_empty': 'خطأ: نص الإشعار غير مكتمل. ابدأ من جديد.',
    'buy.back_to_tariffs': '← رجوع',
    'buy.button_price': '{price} ₽ — {period}',
    'buy.button_price_discount': '{base} ₽ → {final} ₽ — {period}',
    'buy.corporate': '🧩 الوصول المؤسسي\nتكوين مخصص لاحتياجات الشركة.\nبنية تحتية مخصصة، تحكم في الوصول\nودعم شخصي.',
    'buy.corporate_access_button': '🧩 الوصول المؤسسي',
    'buy.corporate_back': '◀️ رجوع',
    'buy.corporate_button': '🧩 الوصول المؤسسي',
    'buy.corporate_confirm': '✅ تأكيد',
    'buy.corporate_consent': 'بإرسال الطلب، فإنك توافق\nعلى معالجة اسم المستخدم ومعرف Telegram،\nوكذلك المعلومات المقدمة طواعية\nفي إطار الطلب.',
    'buy.corporate_request_accepted': 'تم قبول الطلب.\n\nتم إرساله للمراجعة الفردية.\nسيتصل بك المدير بشأن الوصول المؤسسي،\nيرجى الانتظار.',
    'buy.enter_promo': '🎟 أدخل رمز الترويج',
    'buy.enter_promo_button': '🎟 Enter promo code',
    'buy.enter_promo_text': 'أدخل رمز الترويج:',
    'buy.invoice_description': 'Atlas Secure VPN تعريفة {tariff_name}، اشتراك {months} شهر',
    'buy.invoice_label': 'للدفع',
    'buy.period_1': 'شهر واحد',
    'buy.period_2_4': '{months} أشهر',
    'buy.period_5_plus': '{months} أشهر',
    'buy.promo_applied': '🎁 تم تطبيق رمز الترويج. الخصم مشمول في السعر.',
    'buy.promo_applied_with_ttl': '🎁 تم تطبيق رمز الترويج. الخصم مشمول. صالح لـ {minutes} دقيقة أخرى.',
    'buy.promo_enter_text_hint': 'يرجى إدخال رمز الترويج كنص.',
    'buy.renew_button': '🔐 شراء / تجديد الوصول',
    'buy.renewal_payment_label': 'تجديد الاشتراك',
    'buy.select_basic_button': '✅ اختيار Premium',
    'buy.select_plus_button': '🔑 اختيار Platinum',
    'buy.select_tariff': '📊 التعريفات\n\n🪙 التعريفة: Premium\n\n🔹 للاستخدام اليومي\n📲 ممتاز للشبكات الاجتماعية\n🚀 يدعم: Instagram، YouTube 4K، TikTok، الويب وغيرها\n🔒 حماية موثوقة للحركة الأساسية\n💡 اتصال بسيط وفعال\n\n👉 مثالي للاستخدام اليومي دون مهام معقدة\n\n⸻⸻⸻\n\n🔑 التعريفة: Platinum\n\n🔥 وصول ذو أولوية للخوادم\n📶 يعمل مع 5G بدون قيود\n🛡 حماية وتعمية محسّنة\n🚫 آمن\n⚡ أولوية لأقصى سرعة للبث والألعاب والتنزيلات\n\n👉 لمن يريد أقصى راحة وحرية على الإنترنت',
    'buy.select_tariff_type': 'اختر الباقة:',
    'buy.tariff_basic': '🪙 Premium',
    'buy.tariff_basic_desc': '🪙 التعريفة: Premium\n\n🔹 للاستخدام اليومي\n📲 ممتاز للشبكات الاجتماعية\n🚀 يدعم: Instagram, YouTube 4K, TikTok, الويب وغيرها\n🔒 حماية موثوقة للحركة الأساسية\n💡 اتصال بسيط وفعال\n\n👉 مثالي للاستخدام اليومي دون مهام معقدة',
    'buy.tariff_basic_description': '🪙 الباقة: Premium\n\n🔹 للاستخدام اليومي\n📲 ممتاز لوسائل التواصل الاجتماعي\n🚀 يدعم: Instagram، YouTube 4K، TikTok، الويب وغيرها\n🔒 حماية أساسية موثوقة للحركة\n💡 اتصال بسيط وفعال\n\n👉 مثالي للاستخدام اليومي دون مهام معقدة',
    'buy.tariff_basic_selected': '🔐 تم اختيار تعريفة Premium\nأي فترة تهمك؟',
    'buy.tariff_button_1': '1 month · For trial · 149 ₽',
    'buy.tariff_button_12': "12 months · Don't think about access · 899 ₽",
    'buy.tariff_button_3': '3 months · Most popular · 399 ₽ ⭐',
    'buy.tariff_button_6': '6 أشهر · تجديد أقل · 599 ₽',
    'buy.tariff_corporate': '🧩 الوصول المؤسسي\nتكوين مخصص لاحتياجات الشركة.\nبنية تحتية مخصصة، تحكم في الوصول\nودعم شخصي.',
    'buy.tariff_label_basic': '🪙 Premium',
    'buy.tariff_label_plus': '🔑 Platinum',
    'buy.tariff_plus': '🔑 Platinum',
    'buy.tariff_plus_desc': '🔑 التعريفة: Platinum\n\n🔥 وصول ذو أولوية للخوادم\n📶 يعمل مع 5G بدون قيود\n🛡 حماية وتعمية محسّنة\n🚫 آمن\n⚡ أولوية لأقصى سرعة للبث والألعاب والتنزيلات\n\n👉 لمن يريد أقصى راحة وحرية على الإنترنت',
    'buy.tariff_plus_description': '🔑 التعريفة: Platinum\n\n🔥 وصول ذو أولوية للخوادم\n📶 يعمل مع 5G بدون قيود\n🛡 حماية وتعمية محسّنة\n🚫 آمن\n⚡ أولوية لأقصى سرعة للبث والألعاب والتنزيلات\n\n👉 لمن يريد أقصى راحة وحرية على الإنترنت',
    'buy.tariff_plus_selected': '🔐 تم اختيار تعريفة Platinum\nأي فترة تهمك؟',
    'buy.tariff_select_basic_button': '✅ اختيار Premium',
    'buy.tariff_select_plus_button': '🔑 اختيار Platinum',
    'buy.vpn': '🔐 شراء الوصول',
    'common.back': '← Back',
    'common.cancel': '❌ إلغاء',
    'common.go_to_connection': '🔌 Go to Connection',
    'common.rate_limit_message': 'طلبات كثيرة جداً. يرجى المحاولة لاحقاً.',
    'common.user': 'مستخدم',
    'common.username_not_set': 'غير محدد',
    'errors.access_denied': 'تم رفض الوصول.',
    'errors.analytics': 'خطأ في تحميل التحليلات',
    'errors.check_logs': 'خطأ. تحقق من السجلات.',
    'errors.dashboard_data': 'خطأ في جلب بيانات لوحة التحكم',
    'errors.data_fetch': '❌ خطأ في جلب البيانات: {error}',
    'errors.db_init_stage_warning': '⚠️ قاعدة البيانات لا تزال قيد التهيئة (STAGE). قد تكون بعض الوظائف غير متاحة.',
    'errors.details': 'خطأ في جلب التفاصيل',
    'errors.discount_too_low': 'المبلغ بعد الخصم أقل من الحد الأدنى للدفع بالبطاقة (64 ₽).\nيرجى اختيار تعريفة أخرى.',
    'errors.function_disabled': 'هذه الميزة غير متاحة.',
    'errors.generic': 'خطأ',
    'errors.insufficient_balance': 'رصيد غير كافٍ.\n\nالسعر: {amount:.2f} ₽\nالرصيد: {balance:.2f} ₽\nالنقص: {shortage:.2f} ₽',
    'errors.invalid_amount': 'مبلغ غير صالح',
    'errors.invalid_tariff': 'خطأ: تعريفة غير صالحة',
    'errors.metrics': 'خطأ في جلب المقاييس. تحقق من السجلات.',
    'errors.no_active_subscription': 'لم يتم العثور على اشتراك نشط.',
    'errors.payment_already_processed': 'تمت معالجة الدفع مسبقاً.',
    'errors.payment_create': 'خطأ في إنشاء الفاتورة. يرجى المحاولة لاحقاً.',
    'errors.payment_min_amount': 'المبلغ بعد الخصم أقل من الحد الأدنى للدفع بالبطاقة (64 ₽).\nيرجى اختيار تعريفة أخرى.',
    'errors.payment_not_found': 'لم يتم العثور على الدفع.',
    'errors.payment_processing': 'خطأ في معالجة الدفع. يرجى التواصل مع الدعم.',
    'errors.payments_unavailable': 'المدفوعات غير متاحة مؤقتاً',
    'errors.pending_payment_exists': 'لديك دفعة قيد الانتظار بالفعل.',
    'errors.profile_load': 'خطأ في تحميل الملف الشخصي. يرجى المحاولة لاحقاً.',
    'errors.promo_stats': 'خطأ في جلب إحصائيات العروض.',
    'errors.referral_stats': 'خطأ في جلب إحصائيات الإحالة',
    'errors.rewards_history': 'خطأ في جلب تاريخ المكافآت',
    'errors.session_expired': '⏳ Session expired. Please try again.',
    'errors.session_expired_processing': 'الدفع قيد المعالجة. يرجى الانتظار.',
    'errors.start_command': 'يرجى البدء بالأمر /start',
    'errors.stats': 'خطأ في جلب الإحصائيات',
    'errors.stats_search': 'خطأ في البحث عن الإحصائيات',
    'errors.stats_sort': 'خطأ في ترتيب الإحصائيات',
    'errors.subscription_activation': 'خطأ في تفعيل الاشتراك. يرجى التواصل مع الدعم.',
    'errors.tariff': 'خطأ في الباقة',
    'errors.top_referrers': 'خطأ في جلب أفضل المحيلين',
    'errors.try_later': '⚠️ حدث خطأ. يرجى المحاولة مرة أخرى لاحقاً.',
    'errors.vpn_key_creation': 'خطأ في إنشاء مفتاح VPN. تحقق من السجلات.',
    'incident.banner': '⚠️ أعمال تقنية جارية',
    'instruction._device_android': '🤖 Android',
    'instruction._device_desktop': '💻 Windows / macOS',
    'instruction._device_ios': '📱 iOS',
    'instruction._download_android': '🤖 Android',
    'instruction._download_desktop': '💻 Windows',
    'instruction._download_ios': '📱 iOS',
    'instruction._download_macos': '🍎 MacOS',
    'instruction._download_tv': '📺 TV',
    'instruction._text': "🔑 Step 1. Add Key\n\n1. Open v2RayTun app\n2. Tap «➕ Add Configuration»\n3. Select «Import by Link»\n4. Paste the key received from bot\n5. Confirm addition\n\n⚠️ Important:\n— Delete old key before adding new one\n— One key works only for one user\n\n━━━━━━━━━━━━━━━━━━\n🚀 Step 2. Connect\n━━━━━━━━━━━━━━━━━━\n\n1. Select the added configuration\n2. Tap «Connect» button\n3. Wait for «Connected» status\n\nAfter connection:\n— All internet traffic is protected\n—  works automatically\n\n━━━━━━━━━━━━━━━━━━\n❓ If not connecting\n━━━━━━━━━━━━━━━━━━\n\n• Check that subscription is active\n• Make sure you're using v2RayTun\n• Delete old  configurations",
    'lang.button_ar': '🇸🇦 العربية',
    'lang.button_de': '🇩🇪 Deutsch',
    'lang.button_en': '🇺🇸 English',
    'lang.button_kk': '🇰🇿 Қазақша',
    'lang.button_ru': '🇷🇺 Русский',
    'lang.button_tj': '🇹🇯 Тоҷикӣ',
    'lang.button_uz': "🇺🇿 O'zbek",
    'lang.change': '🌍 تغيير اللغة',
    'lang.changed': 'تم تغيير اللغة',
    'lang.changed_toast': '✅ تم تغيير اللغة',
    'lang.select': 'مرحباً بك في Atlas Secure\n\nالوصول الآمن الخاص\nبدون إعدادات معقدة.\n\nيرجى اختيار لغتك:',
    'lang.select_title': '🌍 اختر لغتك:',
    'main.about': 'ℹ️ حول الخدمة',
    'main.about_text': 'Atlas Secure — نظام بيئي رقمي،\nمُنشأ داخل Telegram.\n\n🔐 هندسة بدون تخزين السجلات\n⚡ سرعة اتصال عالية ومستقرة\n📶 عمل صحيح في LTE / 5G / Wi-Fi\n🧩 مفاتيح وصول شخصية\n🇪🇺 أصفر \n🛡 الخصوصية افتراضياً\n\n🌍 واجهة متعددة اللغات\n💳 طرق دفع آمنة\n\nالنظام البيئي مبني بحيث\nيبقى الاتصال مستقراً،\nوالإدارة — بسيطة وشفافة.',
    'main.about_title': '🔎 About Atlas Secure',
    'main.ecosystem': '⚪️ Our Ecosystem',
    'main.ecosystem_title': '🔐 Our Ecosystem · Atlas Secure',
    'main.ecosystem_text': 'Atlas Secure develops as a unified digital environment within Telegram.\n\n⚪️ Available in the ecosystem:\n\n📶 Stable access service\n• High speed without limits\n• Stable messengers\n• Banking and gov services work\n• Stable mobile connection\n\n📊 Only Tracker (coming soon)\nPersonal habit assistant — goals and focus in one place.\n\n⚙️ All services unified in one management system.\n\nAtlas Secure is not separate features, but a connected environment where everything works together.',
    'main.auto_renew_disable': '⏸ Disable auto-renewal',
    'main.auto_renew_disabled': '⏸ Auto-renewal disabled',
    'main.auto_renew_enable': '🔄 Enable auto-renewal',
    'main.auto_renew_enabled': '✅ Auto-renewal enabled',
    'main.auto_renewal_insufficient_balance': '⚠️ رصيد غير كافٍ للتجديد التلقائي.\n\nالمطلوب: {amount:.2f} ₽\nالرصيد: {balance:.2f} ₽\nالنقص: {shortage:.2f} ₽\n\nيرجى شحن رصيدك للتجديد التلقائي.',
    'main.auto_renewal_success': '✅ تم تجديد الاشتراك تلقائياً لمدة {days} يوماً.\n\nصالح حتى: {expires_date}\nالمخصوم من الرصيد: {amount:.2f} ₽',
    'main.balance_topup_success': '✅ تم شحن الرصيد بنجاح بمبلغ {amount:.2f} ₽',
    'main.balance_topup_waiting': '₿ شحن الرصيد بالعملة الرقمية\n\nالمبلغ: {amount} ₽\n\n⏳ انتظار تأكيد الدفع. عادةً يستغرق حتى 5 دقائق. سيتم شحن الرصيد تلقائياً.',
    'main.buy': '🔐 شراء الوصول',
    'main.change_language': '🌍 تغيير اللغة',
    'main.check_payment': '✅ التحقق من الدفع',
    'main.contact_manager_button': '💬 التواصل للحصول على وصول VIP',
    'main.crypto_pay_button': '💳 الذهاب للدفع',
    'main.crypto_payment': 'الدفع بالعملة الرقمية',
    'main.crypto_payment_invoice': 'ادفع الفاتورة عبر Crypto Bot. سيُفعّل الاشتراك تلقائياً بعد الدفع.',
    'main.crypto_payment_redirect': 'الدفع عبر Telegram Crypto Bot\n\nسيتم تحويلك إلى Crypto Bot للدفع.',
    'main.crypto_payment_waiting': '₿ الدفع بالعملة الرقمية\n\nالمبلغ: {amount:.2f} ₽\n\n⏳ انتظار تأكيد الدفع. عادةً يستغرق حتى 5 دقائق. سيُمنح الوصول تلقائياً.',
    'main.db_init_stage_warning': '⚠️ قاعدة البيانات لا تزال قيد التهيئة (STAGE). قد تكون بعض الوظائف غير متاحة.',
    'main.enter_promo_text': 'أدخل رمز الترويج:',
    'main.friend_dual': 'صديقان',
    'main.friend_plural': 'أصدقاء',
    'main.friend_singular': 'صديق',
    'main.get_access': '🔐 الحصول على الوصول',
    'main.help': '🛡 الدعم',
    'main.home_welcome_text': '🔐 Atlas Secure\n\nسعداء برؤيتك في Atlas Secure 🤝\n\nنوفر:\n⚙️ عمل مستقر للخدمات المألوفة\n⚡ اتصال سريع وموثوق\n🛡 الخصوصية افتراضياً\n\nأنت تستخدم الإنترنت كالمعتاد —\nنحن نهتم بالاستقرار والحماية.',
    'main.incident_banner': '⚠️ Technical work in progress',
    'main.incident_status_warning': '\n\n⚠️ WARNING: Incident mode active\n{incident_text}',
    'main.instruction': '🔌 التعليمات',
    'main.insufficient_balance': 'الرصيد غير كافٍ.\n\nالسعر: {amount:.2f} ₽\nالرصيد: {balance:.2f} ₽\nالنقص: {shortage:.2f} ₽',
    'main.insufficient_balance_for_subscription': 'الرصيد غير كافٍ.\n\nالسعر: {amount:.2f} ₽\nالرصيد: {balance:.2f} ₽\nالنقص: {shortage:.2f} ₽',
    'main.invalid_promo': '❌ Invalid promo code',
    'main.no': 'لا',
    'main.no_subscription': '👤 ملف الوصول\n\nالوصول غير مفعّل حالياً.\n\nAtlas Secure يقدم\nوصولاً خاصاً آمناً\nبمفتاح اتصال فردي.\n\nيمكنك الحصول على الوصول\nفي أي وقت مناسب.',
    'main.our_channel': 'قناتنا',
    'main.pay_balance': '💰 الرصيد (المتاح: {balance:.2f} ₽)',
    'main.pay_card': '💳 Bank Card',
    'main.pay_crypto': '🤖 CryptoBot',
    'main.pay_with_card': '💳 Pay with card',
    'main.personal_discount_label': '🎯 Personal Discount {percent}%',
    'main.privacy_policy': 'سياسة الخصوصية',
    'main.privacy_policy_text': '🔐 سياسة الخصوصية Atlas Secure\n\nيعتمد Atlas Secure على مبدأ\nتقليل البيانات.\n\nلا نجمع ولا نخزن معلومات\nغير مطلوبة لتشغيل الخدمة.\n\nما لا نخزنه:\n• سجل الاتصال\n• عناوين IP وحركة الشبكة\n• استعلامات DNS\n• بيانات الموارد المزارة\n• بيانات وصفية لنشاط المستخدم\n\nالهندسة تنفذ مبدأ Zero-Logs.\n\nما قد يُعالَج:\n• حالة الوصول\n• مدة صلاحية الاشتراك\n• معرف المفتاح التقني\n\nهذه البيانات غير مرتبطة\nبنشاطك الشبكي.\n\nالمدفوعات:\nلا يعالج ولا يخزن Atlas Secure\nبيانات الدفع. الدفع يمر عبر\nأنظمة مصرفية ودفع\nخارج بنيتنا التحتية.\n\nمشاركة البيانات:\nلا نشارك البيانات مع أطراف ثالثة\nولا نستخدم متتبعات\nأو تحليلات أو أدوات إعلانية.\n\nالدعم:\nنعالج فقط المعلومات\nالتي تقدمها طوعاً\nلحل طلب محدد.\n\n🔒 سياسة الخصوصية: <a href="https://telegra.ph/Politika-konfidencialnosti-02-16-32">قراءة</a>\n📜 اتفاقية المستخدم: <a href="https://telegra.ph/Polzovatelskoe-soglashenie-02-16-19">قراءة</a>\n\nAtlas Secure.\nالخصوصية مدمجة في هندسة الخدمة.',
    'main.game': "ألعاب 🎮",
    'games.menu_title': "🎮 مرحباً بك في قاعة الألعاب!\nهنا يمكنك الاسترخاء ومحاولة حظك — والفوز بأيام اشتراك إضافية.\n\n🎳 البولينغ — ارمِ الكرات واحصل على أيام مكافأة\n🎲 النرد — ارمِ النرد واحصل على أيام اشتراك بقدر الرقم الذي ظهر\n💣 بومبر — لعبة استراتيجية للبقاء على قيد الحياة\n\nاختر لعبة وجرّب حظك! 🍀",
    'games.button_bowling': "🎳 البولينغ",
    'games.button_dice': "🎲 النرد",
    'games.button_bomber': "💣 بومبر",
    'games.back_to_games': "🔙 العودة للألعاب",
    'games.bowling_cooldown': "نادي البولينغ مغلق 🎳\nاللعبة التالية متاحة خلال: {days}ي {hours}س",
    'games.bowling_paywall': "🎳 نادي البولينغ للمشتركين فقط!\n\nاشترِ اشتراكاً للعب.",
    'games.bowling_strike_success': "🎳 <b>سترايك!</b> تم إسقاط جميع الكرات!\n\n🎉 تهانينا! فزت بـ +7 أيام اشتراك.\n\nالوصول حتى: {date}",
    'games.bowling_strike_error': "🎳 <b>سترايك!</b> تم إسقاط جميع الكرات!\n\n🎉 تهانينا! فزت بـ +7 أيام اشتراك.\n\n⚠️ خطأ في الإضافة. يرجى التواصل مع الدعم.",
    'games.bowling_no_strike': "🎳 أسقطت {value} كرات من 6.\n\nللأسف، ليس سترايك 😔 جرّب مرة أخرى بعد 7 أيام!",
    'games.dice_cooldown': "⏳ لقد رميت النرد بالفعل!\nالرمية التالية متاحة خلال: {days} يوم {hours} ساعة",
    'games.dice_paywall': "🎲 لعبة النرد للمشتركين فقط!\n\nاشترِ اشتراكاً للعب.",
    'games.dice_success': "🎲 ظهر: {value}!\n\n🎉 تم إضافة {value} يوم اشتراك لك!\n\nاشتراكك صالح حتى: {date}",
    'games.dice_error': "🎲 ظهر: {value}!\n\n🎉 تم إضافة {value} يوم اشتراك لك!\n\n⚠️ خطأ في الإضافة. يرجى التواصل مع الدعم.",
    'games.bomber_rules': "💣 بومبر\n\nالقواعد:\n• ضع القنابل على الميدان، تجنب ألغام البوت\n• إذا خطوت على قنبلتك — انفجار! 💥\n• إذا خطوت على لغم البوت — انفجار! 💥\n• اضغط 'إنهاء' للخروج بأمان\n\nحظاً موفقاً! 🍀",
    'games.bomber_finish': "🚩 إنهاء",
    'games.bomber_self_destruct': "🧨 بوم! انفجرت على قنبلتك!\n\nانتهت اللعبة. جرّب مرة أخرى!",
    'games.bomber_mine_exploded': "💥 بوم! انفجرت على لغم البوت!\n\nانتهت اللعبة. جرّب مرة أخرى!",
    'games.bomber_safe_exit': "😮‍💨 خرجت من اللعبة سالمًا!\n\nالقنابل الناجية: {count}",
    'main.profile': '👤 ملفي الشخصي',
    'main.profile_active': '👤 ملف الوصول\n\nحالة الوصول: نشط\nمدفوع حتى {date}\n\nأنت متصل. الوصول يعمل بثبات.\n\nمفتاح الوصول الشخصي\nيُستخدم للاتصال في تطبيق VPN.\nالاتصال مستمر طالما الوصول نشط.\n\n{vpn_key}\n\nعند التجديد، تُضاف المدة المختارة\nتلقائياً للوصول الحالي.\n\nحتى نهاية المدة، لا يمكنك\nالعودة للإعدادات والدفع.',
    'main.profile_auto_renew_disabled': '🔁 Auto-renewal: disabled',
    'main.profile_auto_renew_enabled': '🔁 Auto-renewal: {next_billing_date}',
    'main.profile_buy_hint': 'اضغط «شراء الاشتراك» في القائمة للحصول على الوصول.',
    'main.profile_payment_check': '🕒 الدفع قيد التحقق.\n\nهذا إجراء أمني قياسي.\nبعد التأكيد سيظهر الوصول تلقائياً.',
    'main.profile_renewal_hint': '',
    'main.profile_renewal_hint_new': 'عند التجديد، تُضاف المدة المختارة\nتلقائياً للوصول الحالي.',
    'main.profile_subscription_active': 'الاشتراك:\n— 🟢 نشط حتى {date}',
    'main.profile_subscription_inactive': 'الاشتراك:\n— 🔴 غير نشط',
    'main.profile_subscription_pending': 'الاشتراك:\n— ⏳ بانتظار التفعيل\n\nصالح حتى: {date}',
    'main.profile_welcome': 'مرحباً بك في Atlas Secure!\n\n👤 {username}\n\n💰 الرصيد: {balance:.2f} ₽',
    'main.profile_welcome_full': 'مرحباً بك في Atlas Secure!\n\n👤 {username}\n\n💰 الرصيد: {balance:.2f} ₽',
    'main.promo_applied': '🎁 Promo code applied. Discount already included in price.',
    'main.promo_discount_label': '🎟 Promo code',
    'main.referral': '💎 برنامج الولاء',
    'main.reissue_notification_text': 'تم تحديث مفتاح VPN الخاص بك ونقله إلى إصدار خادم جديد.\n\nللعمل بشكل صحيح:\n— احذف المفتاح القديم من تطبيق VPN\n— أضف مفتاح الوصول الجديد\n\nالمفتاح:\n\n{vpn_key}\n\nالتحديث ضروري للحفاظ على الاستقرار وأداء الاتصال.',
    'main.reissue_notification_title': '🔐 تحديث مفتاح VPN',
    'main.reminder_admin_1day_6h': '⏳ Temporary Atlas Secure access expires in 6 hours.\n\nWe recommend purchasing a full subscription\nto maintain stable access without interruption.',
    'main.reminder_admin_7days_24h': '⏳ Temporary Atlas Secure access expires in 24 hours.\n\nWe recommend purchasing a 1-month subscription\nfor continuous and stable connection.',
    'main.reminder_paid_24h': '⏳ Your Atlas Secure access expires in 24 hours.\n\nWe recommend renewing your subscription in advance\nto maintain continuous connection.',
    'main.reminder_paid_3d': '⏳ Your Atlas Secure access expires in 3 days.\n\nYou can renew your subscription in advance\nto avoid connection interruption.',
    'main.reminder_paid_3h': '⏳ ينتهي وصولك إلى Atlas Secure خلال 3 ساعات.\n\nجدّد اشتراكك الآن\nلتجنب انقطاع الاتصال.',
    'main.renewal_pay_button': '💳 Pay',
    'main.renewal_payment_text': 'ادفع لتجديد الاشتراك.\n\nسيُجرى التجديد\nلنفس مدة الوصول الحالي.',
    'main.sbp_payment_text': 'After making the transfer, confirm payment.\n\n⸻\n\nTransfer details\n\nBank: Ozon\nCard account: 2204321075030551\n\nAmount to confirm: {amount} ₽',
    'main.select_payment': 'اختر طريقة الدفع.',
    'main.select_payment_method': 'Choose payment method:\n\nAmount: {price:.2f} ₽',
    'main.settings': '⚙️ Settings',
    'main.settings_title': '⚙️ Atlas Secure Settings',
    'main.service_status': '📊 Service Status',
    'main.service_status_text': '📊 Atlas Secure Service Status\n\nCurrent status: 🟢 Service works stably\n\nAll main components function\nin normal mode:\n• access is active\n• key issuance works\n• support is available\n\nAtlas Secure is built as private\ndigital infrastructure\nwith priority on stability\nand predictable operation.\n\nOur principles:\n• target uptime — 99.9%\n• planned work is done in advance\n• critical incidents are resolved\n  in priority order\n• data loss is architecturally excluded\n\nIn case of technical work\nor changes, users\nare notified in advance through bot.\n\nLast status update:\nautomatically',
    'main.service_unavailable': '⚠️ الخدمة غير متاحة مؤقتاً. يرجى المحاولة لاحقاً.',
    'main.service_unavailable_payment': '⚠️ Service temporarily unavailable. Please try again later.',
    'main.smart_notif_3days_before_expiry': 'تذكير:\nالوصول سيبقى نشطاً 3 أيام أخرى.\n\nالتجديد يستغرق أقل من دقيقة\nويحافظ على الإعدادات الحالية.',
    'main.smart_notif_3days_usage': 'يُستخدم Atlas Secure بدون حدود\nولا يتطلب تجديداً يدوياً\nحتى نهاية مدة الوصول.',
    'main.smart_notif_7days_before_expiry': 'مدة الوصول\nتنتهي خلال 7 أيام.\n\nيمكنك تجديدها مقدماً\nبدون انقطاع الاتصال.',
    'main.smart_notif_expired_24h': 'الوصول مُعلق.\n\nيمكنك استعادته في أي وقت —\nبدون إعادة تهيئة.',
    'main.smart_notif_expiry_day': 'تنتهي مدة الوصول اليوم.\n\nعند التجديد تُحفظ المفتاح والإعدادات.',
    'main.smart_notif_first_connection': 'الاتصال نشط.\n\nالوصول يعمل بثبات\nولا يحتاج انتباهك.',
    'main.smart_notif_no_traffic_20m': "If you haven't connected yet —\nusually it takes no more than a minute.\n\nThe key is ready and assigned to you.",
    'main.smart_notif_no_traffic_24h': 'تذكير:\nالوصول نشط وجاهز للاستخدام.\n\nالاتصال لا يؤثر على إعدادات الجهاز\nولا يتطلب أذونات إضافية.',
    'main.smart_notif_vip_offer': 'للمستخدمين ذوي الوصول النشط\nمتوفر مستوى دعم موسع.\n\nلا يُباع تلقائياً\nويُدرس بشكل فردي.',
    'main.subscribe_1_month_button': '🔐 اشتراك شهر واحد',
    'main.support': '🛡 الدعم',
    'main.support_button': '🆘 الدعم',
    'main.support_text': '🛡 دعم Atlas Secure\n\nإذا كان لديك أسئلة حول الوصول\nأو الدفع أو تشغيل الخدمة —\nاكتب إلينا مباشرة.\n\nنرد يدوياً\nونعالج الطلبات\nحسب الأولوية.\n\nيمكنك التواصل مع الدعم\nفي أي وقت — نحن هنا.',
    'main.title': 'القائمة الرئيسية',
    'main.topup_amount_invalid': 'يرجى إدخال رقم.',
    'main.topup_amount_too_high': 'Maximum top-up amount: 100,000 ₽. Please enter a smaller amount.',
    'main.topup_amount_too_low': 'Minimum top-up amount: 100 ₽. Please enter an amount of at least 100 ₽.',
    'main.topup_balance': '➕ شحن الرصيد',
    'main.topup_balance_select_amount': 'اختر مبلغ الشحن:',
    'main.topup_balance_success': '✅ تم شحن الرصيد\n\nالرصيد: {balance:.2f} ₽',
    'main.topup_custom_amount': 'مبلغ آخر',
    'main.topup_enter_amount': 'Enter your amount from 100 ₽',
    'main.topup_invoice_description': 'شحن الرصيد بمبلغ {amount} ₽',
    'main.topup_invoice_label': 'شحن الرصيد',
    'main.topup_invoice_title': 'شحن رصيد Atlas Secure',
    'main.topup_select_payment_method': 'شحن الرصيد بمبلغ {amount} ₽\n\nاختر طريقة الدفع:',
    'main.trial_activated_text': '🔒 <b>تم تفعيل الوصول التجريبي</b>\n\nأنت محمي لمدة 3 أيام.\n\n🔑 <b>مفتاح الاتصال:</b>\n<code>{vpn_key}</code>\n\nاستخدمه في تطبيق VPN.\n\n⏰ <b>صالح حتى:</b> {expires_date}',
    'main.trial_activation_error': '❌ خطأ في تفعيل الفترة التجريبية. يرجى المحاولة لاحقاً أو التواصل مع الدعم.',
    'main.trial_button': '🎁 فترة تجريبية 3 أيام',
    'main.trial_expired_text': '🔓 <b>انتهى الوصول التجريبي</b>\n\nانتهت فترة التجربة.\n\n🎟 استخدم رمز الترويج <b>YAbx30</b> للحصول على خصم 30% على اشتراكك الأول.\n\nاشترك الآن لمواصلة استخدام الوصول الآمن.',
    'main.trial_not_available': '❌ الفترة التجريبية غير متاحة. لقد استخدمتها أو لديك اشتراك نشط.',
    'main.trial_notification_18h': '🚀 Stable and fast connection\n\nYour VPN is working reliably.',
    'main.trial_notification_30h': '☕ Hello! VPN is active\n\nContinue using secure access.',
    'main.trial_notification_42h': '⚠️ 30 hours of trial access remaining\n\nMake the most of your time.',
    'main.trial_notification_54h': '⌛ آخر 18 ساعة\n\nستنتهي الفترة التجريبية قريباً.',
    'main.trial_notification_60h': '🛡 VPN will be disabled soon\n\n12 hours of trial access remaining.\n\nContinue using protection — subscribe now.',
    'main.trial_notification_6h': '✨ Just a reminder\n\nVPN is better to keep on all the time to protect your data.',
    'main.trial_notification_71h': '🚨 Last hour of trial access\n\nVPN will be disabled in one hour.\n\nSubscribe now to continue using secure access.',
    'main.user_fallback': 'مستخدم',
    'main.vip_access_button': '👑 ترقية مستوى الوصول',
    'main.vip_access_text': '👑 وصول VIP في Atlas Secure\n\nVIP هو مستوى دعم موسع\nلمن يقدر الاستقرار والأولوية.\n\nما يقدمه VIP:\n⚡️ بنية تحتية ذات أولوية وتأخير أدنى\n🛠 تهيئة وصول شخصية\n💬 دعم ذو أولوية دون انتظار\n🚀 وصول مبكر للتحديثات\n\nVIP مناسب لك إذا:\n• تستخدم الوصول يومياً\n• لا تريد التعامل مع الإعدادات\n• تقدر التشغيل المتوقع\n\nالسعر:\n1\u202f990 ₽ / شهر\nأو 9\u202f990 ₽ / 6 أشهر\n\nيُفعّل VIP مع اشتراك نشط.\nاترك طلباً — سننفذ كل شيء لك.\n\nVIP — عندما يكون الوصول موجوداً ببساطة\nولا تفكر به.',
    'main.vip_discount_label': '👑 وصول VIP',
    'main.vip_status_active': '👑 Your VIP status is active',
    'main.vip_status_badge': '👑 VIP status active',
    'main.welcome': '🔐 Atlas Secure\n\n🧩 الوصول الرقمي الخاص\n⚙️ عمل مستقر للخدمات المألوفة\n🛡 الخصوصية افتراضياً\n\nأنت تتصل —\nكل شيء آخر يعمل في الخلفية.',
    'main.welcome_discount_label': '🎁 Welcome Discount',
    'main.yes': 'نعم',
    'payment.already_processed': '✅ This payment has already been processed.',
    'payment.approved': '✅ تم تفعيل الوصول\n\nمفتاح الوصول الشخصي جاهز.\n\n🔑 سيُرسل مفتاح الوصول الشخصي في الرسالة التالية.\n\n🟢 الوصول صالح حتى:\n{date}\n\nالمفتاح مُعيَّن لك\nوسيكون متاحاً في ملفك الشخصي.\n\n👉 الاتصال يستغرق دقيقة واحدة كحد أقصى.\nإذا احتجت مساعدة — نحن هنا.',
    'payment.balance': '💰 الرصيد (متاح: {balance:.2f} ₽)',
    'payment.card': '💳 بطاقة بنكية',
    'payment.crypto': '🤖 CryptoBot',
    'payment.crypto_pay_button': '💳 الانتقال للدفع',
    'payment.crypto_unavailable': 'الدفع بالعملات المشفرة غير متاح مؤقتاً',
    'payment.crypto_waiting': '₿ دفع بالعملات المشفرة\n\nالمبلغ: {amount:.2f} ₽\n\n⏳ في انتظار تأكيد الدفع. عادة حتى 5 دقائق. سيتم منح الوصول تلقائياً.',
    'payment.expired': '❌ انتهت صلاحية الدفع. يرجى إنشاء دفعة جديدة.',
    'payment.fallback_first': '🎉 تم تفعيل الاشتراك\n\n📅 صالح حتى: {date}',
    'payment.fallback_renewal': '🔄 تم تجديد الاشتراك\n\n📅 صلاحية جديدة حتى: {date}',
    'payment.label': 'الدفع',
    'payment.paid_button': 'تأكيد الدفع',
    'payment.pending': 'التأكيد قيد المعالجة\n\nتم تسجيل الدفع.\nالتحقق يستغرق حتى 5 دقائق.\nيُجرى تفعيل الوصول تلقائياً.',
    'payment.pending_activation': '✅ تم إنشاء الاشتراك!\n\n📅 صالح حتى: {date}\n\n⏳ التفعيل جارٍ. سيُرسل مفتاح VPN إليك قريباً.\n\nإذا لم يصل المفتاح خلال ساعة، يرجى التواصل مع الدعم.',
    'payment.rejected': '❌ لم يُؤكد الدفع.\n\nإذا كنت متأكداً أنك دفعت —\nتواصل مع الدعم.',
    'payment.sbp': 'SBP',
    'payment.select_method': 'اختر طريقة الدفع:\n\nالمبلغ: {price:.2f} ₽',
    'payment.success': '✅ تمت معالجة الدفع بنجاح!',
    'payment.success_first': '🎉 <b>تم تفعيل الاشتراك بنجاح</b>\n\n📅 <b>صالح حتى:</b> {date}\n\n🔐 <b>مفتاح الاتصال:</b>\n<code>{vpn_key}</code>',
    'payment.success_renewal': '🔄 <b>تم تجديد الاشتراك</b>\n\n📅 <b>صلاحية جديدة حتى:</b> {date}\n\n🔐 <b>مفتاحك الحالي</b> (نفس UUID):\n<code>{vpn_key}</code>',
    'payment.test': 'وضع الخدمة غير متاح',
    'profile.access_key_label': 'مفتاح الوصول:',
    'profile.auto_renew_disabled': '🔁 التجديد التلقائي: معطل',
    'profile.auto_renew_enabled': '🔁 التجديد التلقائي: {next_billing_date}',
    'profile.buy_hint': 'انقر على «شراء الاشتراك» في القائمة للحصول على الوصول.',
    'profile.copy_key': '📋 Copy Key',
    'profile.renewal_hint': 'عند التجديد، تُضاف الفترة المحددة\nتلقائياً إلى الوصول الحالي.',
    'profile.subscription_active': 'الاشتراك:\n— 🟢 نشط حتى {date}',
    'profile.subscription_inactive': 'الاشتراك:\n— 🔴 غير نشط',
    'profile.subscription_pending': 'الاشتراك:\n— ⏳ تنشيط معلق\n\nصالح حتى: {date}',
    'profile.topup_balance': '➕ شحن الرصيد',
    'profile.withdraw_funds': '💸 Withdraw funds',
    'profile.vpn_key_copied_toast': '🔑 VPN key copied',
    'profile.welcome_full': 'مرحباً بك في Atlas Secure!\n\n👤 {username}\n\n💰 الرصيد: {balance:.2f} ₽',
    'referral.action_purchase': 'شراء',
    'referral.action_renewal': 'تجديد',
    'referral.action_topup': 'تعبئة',
    'referral.active_paid': '💎 النشطون مع اشتراك: {count}',
    'referral.active_with_subscription': '💎 النشطون بالاشتراك: {count}',
    'referral.cashback_amount': '💳 مبلغ {action_type}: {amount:.2f} ₽',
    'referral.cashback_balance_auto': 'تم تعبئة الرصيد تلقائياً.',
    'referral.cashback_level': '📊 مستواك: {percent}%',
    'referral.cashback_max_level': '🎯 لقد وصلت إلى المستوى الأقصى!',
    'referral.cashback_progress': '👥 للمستوى التالي: متبقي {needed} {friend}',
    'referral.cashback_referred': '👤 الإحالة: {referred}',
    'referral.cashback_reward': '💰 تم إضافة الكاش باك: {amount:.2f} ₽ ({percent}%)',
    'referral.cashback_subscription_period': '⏰ فترة الاشتراك: {period}',
    'referral.cashback_title': '🎉 قام إحالتك بـ {action_type}!',
    'referral.copy_link': '📋 Copy Link',
    'referral.current_status': '🏆 الحالة الحالية: {status}',
    'referral.first_payment_notification': 'عندما يقوم إحالتك بالدفع الأول، سيتم إضافة استرداد نقدي لك!',
    'referral.friend_dual': 'أصدقاء',
    'referral.friend_plural': 'أصدقاء',
    'referral.friend_singular': 'صديق',
    'referral.how_it_works': '📊 How the program works',
    'referral.how_it_works_text': '📊 How the referral program works\n\n1. Send your referral link to a friend\n2. Friend clicks the link and registers\n3. When friend purchases a subscription, you get cashback\n\n🎁 Cashback levels:\n• 0-24 friends → 10% cashback\n• 25-49 friends → 25% cashback\n• 50+ friends → 45% cashback\n\n💰 Cashback is automatically credited to your balance\non each referral purchase.\n\n💡 Level is determined by the number of referrals\nwho have paid for a subscription AT LEAST ONCE.',
    'referral.last_activity': '📅 آخر نشاط: {date}',
    'referral.level_progress': '\n\n📈 Your level: {current_level}% cashback\n{referrals_to_next} referrals left to reach {next_level}% level',
    'referral.link_copied': 'تم إرسال الرابط',
    'referral.max_level': "\n\n🎉 You've reached the maximum level {current_level}%!",
    'referral.max_level_reached': '🏆 لقد وصلت إلى المستوى الأقصى للبرنامج',
    'referral.next_level_line': '🚀 للمستوى {next_status_name}:\nمتبقي {remaining_invites} اتصال',
    'referral.program': '💎 Loyalty Program',
    'referral.program_screen': '📊 النشاط وحالة الوصول\n\n👤 الحسابات المتصلة: {total_referred}\n\n💎 المكافآت المحسوبة: {total_cashback:.2f} ₽\n🏆 الحالة الحالية: {current_status_name}\n📈 مستوى الاسترداد: {cashback_percent}%\n\n{next_level_line}\n\n📅 آخر نشاط: {last_activity_date}',
    'referral.program_status_footer': '🚀 To next level: {remaining_invites} invites left',
    'referral.program_text': '💎 Loyalty Program\n\nInvite friends and get cashback\nto your balance for their payments.\n\n📊 Your statistics:\nInvited friends: {total_referred}\nYour cashback: {cashback_percent}%\nEarned: {total_cashback:.2f} ₽\n\n🔗 Your link:\n{referral_link}\n\n💡 How it works:\n• 0-24 friends → 10% cashback\n• 25-49 friends → 25% cashback\n• 50+ friends → 45% cashback',
    'referral.registered_date': '📅 التاريخ: {date}',
    'referral.registered_notification': '🎉 New referral registered!\n\n👤 User: {user}\n📅 Date: {date}\n\n{first_payment_msg}',
    'referral.registered_title': '🎉 تم تسجيل إحالة جديدة!',
    'referral.registered_user': '👤 المستخدم: {user}',
    'referral.reward_notification': '🔥 حصلت على استرداد الإحالة!\n\nصديقك اشترك.\n💰 الممنوح: {amount:.2f} ₽\nالرصيد: {balance:.2f} ₽',
    'referral.rewards_earned': '💎 المكافآت المستحقة: {amount:.2f} ₽',
    'referral.screen_title': '📊 النشاط وحالة الوصول',
    'referral.share_button': '📤 مشاركة الرابط',
    'referral.share_link_button': '📤 Share link',
    'referral.stats_button': 'More',
    'referral.stats_next_level_line': '🚀 To level {next_status_name}:\n{remaining_invites} connections left',
    'referral.stats_screen': '🔐 برنامج ولاء Atlas Secure\n\n💎 حالتك تفتح مزايا إضافية.\nاحصل على مكافآت بالمشاركة في نظام Atlas Secure — بلا حدود.\n\n⸻\n\n🏆 مستويات الوصول\n\nالوصول الفضيّ\n— حتى 24 مدعوّاً\n— 10% استرداد إلى الرصيد\n\nالوصول الذهبيّ\n— 25–49 مدعوّاً\n— 25% استرداد\n— امتيازات موسعة\n\nالوصول البلاتينيّ\n— 50+ مدعوّاً\n— 45% استرداد\n— أقصى مستوى وصول\n\n⸻\n\n🔗 رابطك الشخصي:\n{referral_link}\n\n🪙 تُحسب المكافآت إلى رصيد حسابك تلقائياً.\n\n⸻\n\n📊 الحالة الحالية: {current_status_name}\n{status_footer}',
    'referral.status_footer': '🚀 للمستوى التالي: متبقي {remaining_invites} دعوة',
    'referral.total_invited': '👤 إجمالي المدعوين: {count}',
    'referral.trial_activated_notification': '🎉 أحالتك فعّلت الفترة التجريبية!\n\n👤 المستخدم: {user}\n⏰ الفترة التجريبية: 3 أيام\n\n{first_payment_msg}',
    'referral.trial_activated_title': '🎉 قام إحالتك بتفعيل الفترة التجريبية!',
    'referral.trial_activated_user': '👤 المستخدم: {user}',
    'referral.trial_period': '⏰ الفترة التجريبية: 3 أيام',
    'reminder.admin_1day_6h': '⏳ ينتهي الوصول المؤقت لـ Atlas Secure خلال 6 ساعات.\n\nنوصي بشراء اشتراك كامل\nللحفاظ على وصول مستقر دون انقطاع.',
    'reminder.admin_7days_24h': '⏳ ينتهي الوصول المؤقت لـ Atlas Secure خلال 24 ساعة.\n\nنوصي بشراء اشتراك شهر واحد\nللاتصال المستمر والمستقر.',
    'reminder.paid_24h': '⏳ ينتهي وصولك إلى Atlas Secure خلال 24 ساعة.\n\nنوصي بتجديد اشتراكك مسبقاً\nللحفاظ على اتصال مستمر.',
    'reminder.paid_3d': '⏳ ينتهي وصولك إلى Atlas Secure خلال 3 أيام.\n\nيمكنك تجديد اشتراكك مسبقاً\nلتجنب انقطاع الاتصال.',
    'reminder.paid_3h': '⏳ ينتهي وصولك إلى Atlas Secure خلال 3 ساعات.\n\nجدد اشتراكك الآن\nلتجنب انقطاع الاتصال.',
    'subscription.auto_renew_disable': '⏸ تعطيل التجديد التلقائي',
    'subscription.auto_renew_disabled_toast': '⏸ تم تعطيل التجديد التلقائي',
    'subscription.auto_renew_enable': '🔄 تفعيل التجديد التلقائي',
    'subscription.auto_renew_enabled_toast': '✅ تم تفعيل التجديد التلقائي',
    'subscription.auto_renew_success': '✅ تم تجديد الاشتراك تلقائياً لمدة {days} يوماً.\n\nصالح حتى: {expires_date}\nتم الخصم من الرصيد: {amount:.2f} ₽',
    'subscription.expiring_reminder': '⏳ تنتهي مدة الوصول قريباً.\n\n3 أيام متبقية حتى انتهاء اشتراكك.\n\nيمكنك تجديد الوصول في أي وقت —\nالشراء المتكرر يمدد المدة تلقائياً.',
    'subscription.history': '📄 سجل الاشتراك',
    'subscription.history_action_manual_reissue': 'إعادة إصدار مفتاح يدوياً',
    'subscription.history_action_purchase': 'شراء',
    'subscription.history_action_reissue': 'إعادة إصدار المفتاح',
    'subscription.history_action_renewal': 'تجديد',
    'subscription.history_empty': 'سجل الاشتراك فارغ',
    'subscription.history_expires': 'حتى:',
    'subscription.history_key_label': 'المفتاح:',
    'subscription.renew': '🔁 تجديد الوصول',
    'support.write_button': '💬 الكتابة إلى الدعم',
    'trial.button': '🎁 فترة تجريبية 3 أيام',
    'trial.expired': '🔓 <b>انتهى الوصول التجريبي</b>\n\nانتهت فترة التجربة.\n\n🎟 استخدم رمز الترويج <b>YABX30</b> للحصول على خصم 30٪ على اشتراكك الأول.\n\nاشترك الآن لمتابعة استخدام الوصول الآمن.',
    'trial.notification_60h': '🛡 سيتم إيقاف VPN قريباً\n\nتبقى 12 ساعة من الوصول التجريبي.\n\nاستمر في استخدام الحماية — اشترك الآن.',
    'trial.notification_6h': '✨ تذكير بسيط\n\nمن الأفضل إبقاء VPN قيد التشغيل دائماً لحماية بياناتك.',
    'trial.notification_71h': '🚨 الساعة الأخيرة من الوصول التجريبي\n\nسيتم إيقاف VPN خلال ساعة.\n\nاشترك الآن لمتابعة استخدام الوصول الآمن.',
}
