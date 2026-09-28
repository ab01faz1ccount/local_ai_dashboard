# گام ۱: Event System — گزارش

این تحویل **جایگزین** فایل‌های هم‌نام توی ریپو می‌شه، نه اضافه‌کردن کنارشون. ساختار پوشه‌ها دقیقاً مطابق ریپوئه.

## این گام چیه

پایه‌ای که بقیه‌ی گام‌ها (Logs، Notifications، Agent Trace، Analytics چارت‌دار) روش سوار می‌شن: یه event log ساختاریافته که هر اتفاق مهم برنامه رو ضبط می‌کنه — هم persist می‌شه (قابل query)، هم live پخش می‌شه (برای یه پنل زنده‌ی بعدی).

## فایل‌های جدید

- **`backend/core/events/taxonomy.py`** — لیست کامل انواع event طبق پرامپت نهایی (`runtime.started`, `model.loaded`, `agent.started`, `session.started`, `inference.*`, و `tool.*`/`mcp.*`/`permission.*` که فعلاً **هیچ‌کس emit نمی‌کنه** چون MCP/Tools/Permission هنوز نیستن — فقط تعریف شدن که وقتی اون فازها اومدن، این فایل دست نخوره).
- **`backend/core/events/bus.py`** — `EventBus`: تنها جایی که به جدول `events` می‌نویسه یا به subscriberها پوش می‌کنه. هیچ‌وقت exception پرتاب نمی‌کنه و هیچ‌وقت caller رو block نمی‌کنه (حتی اگه DB خراب باشه یا یه subscriber کند باشه) — چون یه event system که بتونه عملیات واقعی رو بشکنه، به کارش عمل نکرده.
- **`backend/core/events/device.py`** — یه شناسه‌ی ثابت برای «این دستگاه» (نه یه جدول Device کامل، فقط یه UUID ذخیره‌شده توی `app_settings`).
- **`backend/core/events/__init__.py`** — سطح دسترسی عمومی: `from ..core import events; events.emit(events.EventType.X, ...)`.

## تغییرات روی فایل‌های موجود

- **`storage/db.py`**: کلاس `Event` (جدول `events`, append-only, با `ON DELETE SET NULL` روی runtime/agent/session — یعنی حذف یه runtime تاریخچه‌ی eventهاش رو پاک نمی‌کنه) + دو تابع کمکی `insert_event`/`list_events`.
- **`storage/schema.sql`**: جدول `events` برای مستندسازی (نکته‌ی جانبی: متوجه شدم `schema.sql` از قبل با `projects`/`chats`/`chat_messages` واقعی توی `db.py` همگام نیست — این خارج از scope این گام بود، دست نزدم، ولی بد نیست یه روز sync بشه).
- **`core/runtime_manager.py`**: دقیق‌ترین بخش کار. `start()`/`stop()`/`restart()`/`get_status()` حالا event درست رو emit می‌کنن — شامل **تشخیص کرش**: اگه یه runtime بین دو تا poll (یعنی بدون اینکه کسی صریحاً `stop()` بزنه) از ONLINE به ERROR بره، `get_status()` (که هر ۱.۵ ثانیه توسط `/ws/metrics` صدا زده می‌شه) اینو می‌فهمه و `runtime.crashed` رو با `phase: "poll"` emit می‌کنه.
- **`core/models.py`**: `model.registered` دقیقاً همون لحظه‌ای emit می‌شه که یه ردیف جدید ساخته می‌شه — نه از سمت http.py (چون `scan_models_folder` کل کاتالوگ رو برمی‌گردونه، نه فقط تازه‌ها؛ اگه از بیرون emit می‌کردم هر بار scan مجدد یه سیل event غلط می‌داد).
- **`api/http.py`**: `agent.created/deleted`، `runtime.registered/removed`، `session.started/completed` + `agent.started/stopped` (روی attach/detach)، `model.removed`، `model.verification_completed`، `inference.started/completed/failed` (روی ارسال پیام چت) + دو route جدید: `GET /api/v1/events` (فیلتر بر اساس نوع/runtime/agent/session/زمان + pagination) و `GET /api/v1/events/types`.
- **`api/ws.py`**: `GET /ws/events` — تب زنده‌ی همون event log.
- **`api/discovery.py`**: `agent.created` روی مسیر Browse آفلاین هم اضافه شد (چون اون مسیر مستقیم Agent می‌سازه، نه از `http.py`).

## یه نکته‌ی مهم درباره‌ی ترتیب عملیات

حذف یه runtime/agent باید **قبل** از `db.delete()` رویداد `removed` رو emit کنه، نه بعدش — چون بعد از حذف، دیگه ردیفی با اون id وجود نداره و insert یه event با اون `runtime_id` به خاطر FK constraint رد می‌شه. این با mutation test تأیید شده (پایین‌تر).

## تست‌ها

**۵۰ تست جدید**، همه روی کد واقعی ریپو (نه mock/stub):

| فایل | تعداد | چی رو تست می‌کنه |
|---|---|---|
| `test_events.py` | ۱۶ | خود `EventBus`: persist، publish، چند subscriber، صف پر که oldest رو drop می‌کنه نه emitter رو block کنه، خطای persist که caller رو نمی‌شکنه، همزمانی از چند thread |
| `test_runtime_manager_events.py` | ۱۳ | هر حالت `start/stop/restart/get_status` — موفق، ناموفق، تشخیص کرش با poll، عدم تکرار event وقتی start و بعدش فوری poll می‌شه |
| `test_events_api.py` | ۱۶ | مسیرهای `/events`, `/events/types` (فیلتر، pagination، auth) + هر نقطه‌ی wiring توی http.py |
| `test_ws_events.py` | ۵ | `/ws/events`: auth، ترتیب رسیدن، چند socket همزمان، پاک شدن subscriber بعد از قطع اتصال |

با **mutation testing** هم دو تا از حساس‌ترین قسمت‌ها چک شد: خاموش کردن منطق تشخیص کرش → تست رد شد؛ جابه‌جا کردن ترتیب emit/delete → تست رد شد (و خطای FK دقیقاً همونی بود که پیش‌بینی می‌شد).

### یه‌چیز جانبی که پیدا و درست شد
`tests/backend/test_router.py` یه توکن ساختگی هاردکد شده داشت (`"Bearer test-token"`) که از یه نسخه‌ی قبلی stub مونده بود — روی ریپوی واقعی (با `core/security.py` واقعی) کار نمی‌کرد. درستش کردم تا توکن واقعی رو از `get_or_create_access_token()` بگیره. همچنین یه `tests/backend/conftest.py` جدید اضافه شد که قبلاً موقع تحویل جا افتاده بود — بدونش، تست‌ها باید دستی چیدمان می‌شدن.

**نتیجه‌ی نهایی: ۲۰۵ تست پاس، ۱ skip (تست permission که زیر root اجرا نمی‌شه).**

## چیزی که عمداً نساختم

UI برای دیدن این eventها (صفحه‌ی Logs یا Notification). این گام عمداً فقط پایه‌ست — طبق روحیه‌ی پرامپت نهایی («این‌جا هنوز UI بزرگی لازم نیست»). اگه بخوای، گام بعدی می‌تونه یا مستقیم بره سراغ **MCP Manager** (طبق برنامه‌ای که قبلاً گفتم)، یا یه صفحه‌ی ساده‌ی Logs بسازیم که همین API رو نشون بده — هرکدوم رو بگی شروع می‌کنم.
