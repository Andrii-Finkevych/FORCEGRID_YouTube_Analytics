# app.py
# -*- coding: utf-8 -*-

import re
import requests
import datetime as dt
import pytz
import pandas as pd
import streamlit as st
from dateutil import parser as dtparser
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode

# ======================================================================================
# Налаштування сторінки
# ======================================================================================
st.set_page_config(
    page_title="YouTube ANLT by Finkevych",
    page_icon="📈",
    layout="wide",
)

# ======================================================================================
# API ключ (вшитий) і базовий URL
# ======================================================================================
API_KEY = "AIzaSyApr2mhue6CZthtma_vKkVk3qNaGQIk1YQ"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
TZ_NAME = "Europe/Kyiv"   # фіксована локальна таймзона

# ======================================================================================
# PRESETS — готові набори каналів (редагуй під себе)
# ======================================================================================
PRESETS = {
    "Meow Tales": [
        "https://www.youtube.com/@MeowMamas/videos", "https://www.youtube.com/@TailRippleMedia/videos", "https://www.youtube.com/watch?v=NCYD_-j2L4o",
        "https://www.youtube.com/@dassh3494/videos", "https://www.youtube.com/@ToraCatAI", "https://www.youtube.com/@MeowMemoirs97", "https://www.youtube.com/@MeowLifeTNMedia",
        "https://www.youtube.com/@MeowRelaxChannel", "https://www.youtube.com/@felinedreamtv", "https://www.youtube.com/@TinyPaws99", "https://www.youtube.com/@beamngqueen5677",
        "https://www.youtube.com/@HoneyCatAI", "https://www.youtube.com/@Amber_and_MamaPolka", "https://www.youtube.com/@StarAndManny", "https://www.youtube.com/@Daisy520-h2o",
        "https://www.youtube.com/@Capyat", "https://www.youtube.com/@KittensDreamYT", "https://www.youtube.com/@GingerCozyCorner_td",
        # додай сюди інші канали...
    ],
    "Fravix": [
        "https://www.youtube.com/@SimplePaintOfficial/videos", "https://www.youtube.com/@ThePaintExplainer/videos", "https://www.youtube.com/@ThePainfulExplainer/videos",
        "https://www.youtube.com/@ATalkingHat/videos",
        # додай сюди інші канали...
    ],
}

# ======================================================================================
# Стан
# ======================================================================================
if "ran" not in st.session_state:
    st.session_state.ran = False
if "sort_by" not in st.session_state:
    st.session_state.sort_by = "За датою"

# ======================================================================================
# Утиліти
# ======================================================================================
def format_int(n: int | float | None) -> str:
    if n is None or (isinstance(n, float) and pd.isna(n)):
        return ""
    try:
        n = int(n)
    except Exception:
        return ""
    return f"{n:,}".replace(",", " ")

def format_vph(v: float | None) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    try:
        return f"{int(round(v))}"
    except Exception:
        return ""

def format_multiplier(v: float | None) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return f"{v:.1f}x"

def parse_hms_to_seconds(s: str) -> int | None:
    if not s:
        return None
    s = s.strip()
    if not re.fullmatch(r"\d{1,2}:\d{1,2}:\d{1,2}|\d{1,2}:\d{1,2}|\d{1,5}", s):
        return None
    parts = [int(p) for p in s.split(":")]
    if len(parts) == 3:
        h, m, s2 = parts
        return h * 3600 + m * 60 + s2
    if len(parts) == 2:
        m, s2 = parts
        return m * 60 + s2
    return int(parts[0])

_ISO8601_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)
def iso8601_duration_to_seconds(dur: str) -> int:
    if not dur:
        return 0
    m = _ISO8601_DURATION.match(dur)
    if not m:
        return 0
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds

def ensure_api_key():
    if not API_KEY:
        st.error("Не знайдено YouTube API ключ. Очікується у змінній `API_KEY` зверху файлу.")
        st.stop()

def localize_and_format(ts_utc: dt.datetime, tzname: str) -> str:
    tz = pytz.timezone(tzname)
    local_dt = ts_utc.astimezone(tz)
    return local_dt.strftime("%Y-%m-%d %H:%M")

def format_elapsed_since(published_utc: dt.datetime, now_utc: dt.datetime) -> str:
    """Повертає рядок 'Після публікації' з точністю до години (дні та години)."""
    total_hours = int((now_utc - published_utc).total_seconds() // 3600)
    if total_hours < 0:
        total_hours = 0
    days = total_hours // 24
    hours = total_hours % 24
    if days > 0:
        return f"{days} дн {hours:02d} год"
    return f"{hours} год"

def extract_channel_id(text: str) -> dict:
    text = text.strip()
    if text.startswith("@"):
        return {"type": "handle", "value": text[1:]}
    m = re.search(r"(?:youtube\.com\/channel\/)(UC[0-9A-Za-z_-]{22})", text)
    if m:
        return {"type": "channel_id", "value": m.group(1)}
    m2 = re.search(r"(?:youtube\.com\/@)([A-Za-z0-9._-]+)", text)
    if m2:
        return {"type": "handle", "value": m2.group(1)}
    return {"type": "search", "value": text}

# ======================================================================================
# API клієнт
# ======================================================================================
def yt_get(path: str, params: dict) -> dict:
    ensure_api_key()
    final = {"key": API_KEY, **params}
    r = requests.get(f"{YOUTUBE_API}/{path}", params=final, timeout=30)
    r.raise_for_status()
    return r.json()

def resolve_channel(q: str) -> dict | None:
    cls = extract_channel_id(q)
    if cls["type"] == "handle":
        data = yt_get("channels", {"part": "snippet,statistics,contentDetails", "forHandle": f"@{cls['value']}"})
        if data.get("items"):
            it = data["items"][0]
            return {
                "id": it["id"],
                "title": it["snippet"]["title"],
                "subscribers": int(it.get("statistics", {}).get("subscriberCount", 0))
                if it.get("statistics", {}).get("hiddenSubscriberCount") is not True else None,
                "uploads": it["contentDetails"]["relatedPlaylists"]["uploads"],
            }
    if cls["type"] == "channel_id":
        data = yt_get("channels", {"part": "snippet,statistics,contentDetails", "id": cls["value"]})
        if data.get("items"):
            it = data["items"][0]
            return {
                "id": it["id"],
                "title": it["snippet"]["title"],
                "subscribers": int(it.get("statistics", {}).get("subscriberCount", 0))
                if it.get("statistics", {}).get("hiddenSubscriberCount") is not True else None,
                "uploads": it["contentDetails"]["relatedPlaylists"]["uploads"],
            }
    data = yt_get("search", {"part": "snippet", "q": cls["value"], "type": "channel", "maxResults": 1})
    if data.get("items"):
        ch_id = data["items"][0]["snippet"]["channelId"]
        data2 = yt_get("channels", {"part": "snippet,statistics,contentDetails", "id": ch_id})
        if data2.get("items"):
            it = data2["items"][0]
            return {
                "id": it["id"],
                "title": it["snippet"]["title"],
                "subscribers": int(it.get("statistics", {}).get("subscriberCount", 0))
                if it.get("statistics", {}).get("hiddenSubscriberCount") is not True else None,
                "uploads": it["contentDetails"]["relatedPlaylists"]["uploads"],
            }
    return None

def iterate_playlist_items(playlist_id: str, page_limit: int = 50):
    token = None
    pages = 0
    while True:
        payload = {"part": "contentDetails,snippet", "playlistId": playlist_id, "maxResults": 50, "pageToken": token or ""}
        data = yt_get("playlistItems", payload)
        yield data
        token = data.get("nextPageToken")
        pages += 1
        if not token or pages >= page_limit:
            break

def videos_batch(video_ids: list[str]) -> dict:
    if not video_ids:
        return {"items": []}
    return yt_get(
        "videos",
        {"part": "snippet,statistics,contentDetails,liveStreamingDetails", "id": ",".join(video_ids), "maxResults": 50},
    )

# ======================================================================================
# UI — все на одній сторінці
# ======================================================================================
st.title("📈 BladeGrid YouTube Analytics by Finkevych")

st.markdown("### Налаштування")
left, right = st.columns([3, 1])

# Спочатку пресети (можуть змінювати session_state ДО створення textarea)
def apply_preset():
    name = st.session_state.get("preset_choice")
    if name in PRESETS:
        st.session_state["channels_text"] = "\n".join(PRESETS[name])

with right:
    st.selectbox("Набір каналів", ["— Обрати —"] + list(PRESETS.keys()),
                 index=0, key="preset_choice")
    st.button("Заповнити канали", use_container_width=True, on_click=apply_preset)

with left:
    st.text_area(
        "Список каналів по одному в рядок. Підтримка @username або повних URL",
        key="channels_text",
        height=160,
        placeholder="@MrBeast\nhttps://www.youtube.com/@veritasium\nhttps://www.youtube.com/channel/UC-lHJZR3Gqxm24_Vd_AJ5Yw",
    )

c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    min_hms = st.text_input("Тривалість (від)", value="0:01:00")        # 60 секунд (більш звично)
with c2:
    max_hms = st.text_input("Тривалість (до)", value="2:00:00")    # 2 години
with c3:
    today_local = dt.datetime.now(pytz.timezone(TZ_NAME)).date()
    # дефолт — останні 3 дні (сьогодні та два попередні)
    date_from, date_to = st.date_input(
        "Діапазон опублікованих відео",
        value=(today_local - dt.timedelta(days=3), today_local),
    )

# видалено "За Viral"
sort_options = ["За датою", "За переглядами", "За VPH", "За Multiplier"]
default_sort = st.session_state.sort_by if st.session_state.sort_by in sort_options else sort_options[0]
sort_by = st.selectbox(
    "Сортувати результати:",
    options=sort_options,
    index=sort_options.index(default_sort),
    key="sort_by",
)

if st.button("Аналізувати", type="primary"):
    st.session_state.ran = True

if not st.session_state.ran:
    st.stop()

# ======================================================================================
# Валідації і межі
# ======================================================================================
channels_input = st.session_state.channels_text
if not channels_input.strip():
    st.warning("Введи хоч один канал або обери набір і натисни «Заповнити канали».")
    st.stop()

def parse_hms_optional(s): return parse_hms_to_seconds(s) if s else None
min_secs = parse_hms_optional(min_hms)
max_secs = parse_hms_optional(max_hms)
if min_secs is not None and max_secs is not None and min_secs > max_secs:
    st.error("Некоректний фільтр тривалості: 'Від' > 'До'.")
    st.stop()

ensure_api_key()
tz = pytz.timezone(TZ_NAME)
start_local = dt.datetime.combine(date_from, dt.time.min).replace(tzinfo=tz)
end_local   = dt.datetime.combine(date_to,   dt.time.max).replace(tzinfo=tz)
start_utc = start_local.astimezone(pytz.UTC)
end_utc   = end_local.astimezone(pytz.UTC)
now_utc   = dt.datetime.now(pytz.UTC)

# ======================================================================================
# Збір даних
# ======================================================================================
rows = []
for line in [ln.strip() for ln in channels_input.splitlines() if ln.strip()]:
    ch = resolve_channel(line)
    if not ch:
        st.warning(f"Не вдалося знайти канал для «{line}». Пропускаю.")
        continue

    uploads_id    = ch["uploads"]
    subscribers   = ch["subscribers"]
    channel_title = ch["title"]

    early_stop = False
    for page in iterate_playlist_items(uploads_id, page_limit=80):
        items = page.get("items", [])
        if not items:
            continue

        video_ids = []
        per_item_snippets = {}
        for it in items:
            vid = it.get("contentDetails", {}).get("videoId")
            if not vid:
                continue
            sn = it.get("snippet", {})
            lbc = (sn.get("liveBroadcastContent") or "").lower()
            if lbc in {"live", "upcoming"}:
                continue
            video_ids.append(vid)
            per_item_snippets[vid] = sn

        if not video_ids:
            continue

        data_v = videos_batch(video_ids)
        for v in data_v.get("items", []):
            vid = v["id"]
            sn  = v.get("snippet", {}) or per_item_snippets.get(vid, {})
            stt = v.get("statistics", {})
            cd  = v.get("contentDetails", {})
            lsd = v.get("liveStreamingDetails")

            lbc2 = (sn.get("liveBroadcastContent") or "").lower()
            is_live_now = (lbc2 == "live") and lsd and not lsd.get("actualEndTime")
            if lbc2 == "upcoming" or is_live_now:
                continue

            published_iso = cd.get("videoPublishedAt") or sn.get("publishedAt")
            if not published_iso:
                continue
            try:
                published_utc = dtparser.isoparse(published_iso).astimezone(pytz.UTC)
            except Exception:
                continue

            if published_utc < start_utc - dt.timedelta(days=2):
                early_stop = True
            if not (start_utc <= published_utc <= end_utc):
                continue

            dur_sec = iso8601_duration_to_seconds(cd.get("duration", ""))

            if min_secs is not None and dur_sec < min_secs:
                continue
            if max_secs is not None and dur_sec > max_secs:
                continue

            try:
                views = int(stt.get("viewCount", 0))
            except Exception:
                views = 0

            age_hours = max((now_utc - published_utc).total_seconds() / 3600.0, 1/60)  # мін. 1 хв
            vph = views / age_hours if age_hours > 0 else 0.0
            multiplier = (views / subscribers) if subscribers and subscribers > 0 else None

            if dur_sec >= 3600:
                dur_str = f"{dur_sec // 3600}:{(dur_sec % 3600)//60:02d}:{dur_sec % 60:02d}"
            else:
                dur_str = f"{dur_sec // 60}:{dur_sec % 60:02d}"

            title = sn.get("title", "")
            url   = f"https://www.youtube.com/watch?v={vid}"
            published_local_str = localize_and_format(published_utc, TZ_NAME)
            since_published_str = format_elapsed_since(published_utc, now_utc)

            rows.append({
                "Канал": channel_title,
                "Назва відео": title,
                "Підписники": subscribers,
                "Перегляди": views,               # raw до форматування
                "Multiplier_raw": multiplier if multiplier is not None else float("nan"),
                "Multiplier": None,               # заповнимо формат пізніше
                "VPH_raw": vph,
                "VPH": None,
                "Тривалість": dur_str,
                "Опубліковано": published_local_str,
                "Після публікації": since_published_str,
                "UTC_Published": published_utc,
                "URL": url,                       # простий текст — легко копіювати
                "age_hours": age_hours,
            })

        if early_stop:
            break

if not rows:
    st.warning("За заданими фільтрами нічого не знайдено.")
    st.stop()

df = pd.DataFrame(rows)

# ======================================================================================
# Сортування (без "Viral")
# ======================================================================================
if sort_by == "За датою":
    df = df.sort_values("UTC_Published", ascending=False)
elif sort_by == "За переглядами":
    df = df.sort_values("Перегляди", ascending=False)
elif sort_by == "За VPH":
    df = df.sort_values("VPH_raw", ascending=False)
elif sort_by == "За Multiplier":
    df = df.sort_values("Multiplier_raw", ascending=False, na_position="last")

# ======================================================================================
# Форматування відображення
# ======================================================================================
df["Підписники"] = df["Підписники"].apply(lambda x: format_int(x) if pd.notna(x) and x else "")
df["Перегляди"]  = df["Перегляди"].apply(format_int)
df["Multiplier"] = df["Multiplier_raw"].apply(lambda v: format_multiplier(v) if pd.notna(v) else "")
df["VPH"]        = df["VPH_raw"].apply(format_vph)

# Порядок колонок (без "Viral", додано "Після публікації")
columns_order = [
    "Канал","Назва відео","Підписники","Перегляди","Multiplier","VPH",
    "Тривалість","Опубліковано","Після публікації","URL"
]
df_show = df[columns_order].copy()

st.markdown("### Результати")

# ======================================================================================
# AG Grid — AUTOSIZE + можливість копіювати
# ======================================================================================
gob = GridOptionsBuilder.from_dataframe(df_show)
gob.configure_default_column(
    resizable=True, sortable=True, filter=True, wrapText=False, autoHeight=False,
    cellStyle={"textAlign":"left"}
)
gob.configure_column("Назва відео", width=420)
gob.configure_column("URL", width=360)
for c in ["Підписники","Перегляди","Multiplier","VPH","Тривалість","Опубліковано","Після публікації"]:
    gob.configure_column(c, width=140)

grid_opts = gob.build()
grid_opts.update({
    "enableCellTextSelection": True,   # можна виділяти і копіювати
    "ensureDomOrder": True,
    "suppressRowClickSelection": True,
})
grid_opts["onFirstDataRendered"] = JsCode("""
function(e){
  try{
    e.api.sizeColumnsToFit();
    setTimeout(function(){
      const all=[]; e.columnApi.getAllColumns().forEach(c=>all.push(c.getColId()));
      e.columnApi.autoSizeColumns(all, false);
    }, 50);
  }catch(err){console.warn(err);}
}
""")

AgGrid(
    df_show,
    gridOptions=grid_opts,
    height=600,
    fit_columns_on_grid_load=False,
    allow_unsafe_jscode=True,
    enable_enterprise_modules=False,
    update_mode=GridUpdateMode.NO_UPDATE,
    theme="balham",
)

# Підсумок
st.success(
    f"Знайдено відео: **{len(df_show):,}** | Каналів: **{len(set(df['Канал'])):,}** | "
    f"Діапазон дат: **{date_from.strftime('%Y-%m-%d')} → {date_to.strftime('%Y-%m-%d')} ({TZ_NAME})**"
)
