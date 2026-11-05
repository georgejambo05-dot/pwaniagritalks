import csv
import io
import os
import secrets
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import psycopg
from psycopg.rows import dict_row
from contextlib import contextmanager
from pathlib import Path

from functools import wraps

from flask import Flask, Response, abort, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
DATABASE_URL = os.environ.get("DATABASE_URL")


@contextmanager
def database_connection():
    if not DATABASE_URL:
        raise RuntimeError("Set DATABASE_URL to your Neon PostgreSQL connection string.")
    connection = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-to-a-random-secret-string')

QUESTION_PHOTO_DIR = Path(__file__).with_name("instance") / "question_photos"
MAX_QUESTION_PHOTO_BYTES = 5 * 1024 * 1024
SECRET_KEY_FILE = Path(__file__).with_name(".flask_secret_key")
app.config["MAX_CONTENT_LENGTH"] = MAX_QUESTION_PHOTO_BYTES + 1024 * 1024


def load_secret_key():
    """Keep a random session-signing key in a local file between app restarts."""
    try:
        with SECRET_KEY_FILE.open("x", encoding="utf-8") as secret_file:
            secret_file.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    return SECRET_KEY_FILE.read_text(encoding="utf-8").strip()


if os.environ.get("RENDER"):
    app.secret_key = os.environ.get("SECRET_KEY")
    if not app.secret_key:
        raise RuntimeError("Set SECRET_KEY in the hosting service environment.")
    app.config.update(SESSION_COOKIE_SECURE=True)
else:
    app.secret_key = load_secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


def csrf_token():
    """Create a token so another website cannot silently submit our forms."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_post_token():
    if request.method == "POST":
        submitted = request.form.get("_csrf_token", "")
        expected = session.get("csrf_token", "")
        if not secrets.compare_digest(submitted, expected):
            abort(400)


def create_database():
    """Create the tables and add the password column to older farmer databases."""
    with database_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS farmers (
                id BIGSERIAL PRIMARY KEY,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                location TEXT NOT NULL,
                farming_type TEXT NOT NULL,
                password_hash TEXT
            )
        """)
        columns = {
            row["column_name"] for row in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'farmers'"
            ).fetchall()
        }
        if "password_hash" not in columns:
            connection.execute("ALTER TABLE farmers ADD COLUMN password_hash TEXT")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id BIGSERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS farmer_questions (
                id BIGSERIAL PRIMARY KEY,
                farmer_id BIGINT NOT NULL,
                question_text TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'Waiting for a response'
            )
        """)
        # In-app alerts are stored separately so existing farmer and question records remain untouched.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS farmer_notifications (
                id BIGSERIAL PRIMARY KEY,
                farmer_id BIGINT NOT NULL,
                question_id BIGINT,
                message TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                read_at TIMESTAMP
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_farmer_notifications_farmer_unread
            ON farmer_notifications (farmer_id, read_at, created_at DESC)
        """)
        question_columns = {
            row["column_name"] for row in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'farmer_questions'"
            ).fetchall()
        }
        if "answer" not in question_columns:
            connection.execute("ALTER TABLE farmer_questions ADD COLUMN answer TEXT")
        if "answered_at" not in question_columns:
            connection.execute("ALTER TABLE farmer_questions ADD COLUMN answered_at TIMESTAMP")
        if "photo_filename" not in question_columns:
            connection.execute("ALTER TABLE farmer_questions ADD COLUMN photo_filename TEXT")


def admin_exists():
    with database_connection() as connection:
        return connection.execute("SELECT 1 FROM admin_users LIMIT 1").fetchone() is not None


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "admin_username" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped_view


def farmer_login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "farmer_id" not in session:
            return redirect(url_for("farmer_login"))
        return view(*args, **kwargs)
    return wrapped_view


def get_farmer(farmer_id):
    """Find one farmer by ID, or return None if it does not exist."""
    with database_connection() as connection:
        return connection.execute(
            "SELECT * FROM farmers WHERE id = %s", (farmer_id,)
        ).fetchone()


def normalise_phone(phone):
    """Return a canonical +254 number for valid Kenyan 07/01 mobile numbers.

    Accept local (07XXXXXXXX / 01XXXXXXXX), international
    (+2547XXXXXXXX / +2541XXXXXXXX), and international without plus
    (2547XXXXXXXX / 2541XXXXXXXX) formats. Return None for invalid input.
    """
    raw = str(phone or "").strip()
    if not raw or any(
        not (character.isdigit() or character in "+() -")
        for character in raw
    ):
        return None

    digits = "".join(character for character in raw if character.isdigit())

    if len(digits) == 10 and digits.startswith(("07", "01")):
        return "+254" + digits[1:]

    if len(digits) == 12 and digits.startswith("254") and digits[3] in ("7", "1"):
        return "+" + digits

    return None


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        county = request.form.get("county", "").strip()
        local_area = request.form.get("local_area", "").strip()
        allowed_counties = {
            "Mombasa", "Kwale", "Kilifi", "Tana River", "Lamu", "Taita-Taveta",
            "Garissa", "Wajir", "Mandera", "Marsabit", "Isiolo", "Meru",
            "Tharaka-Nithi", "Embu", "Kitui", "Machakos", "Makueni", "Nyandarua",
            "Nyeri", "Kirinyaga", "Murang'a", "Kiambu", "Turkana", "West Pokot",
            "Samburu", "Trans Nzoia", "Uasin Gishu", "Elgeyo-Marakwet", "Nandi",
            "Baringo", "Laikipia", "Nakuru", "Narok", "Kajiado", "Kericho",            "Bomet", "Kakamega", "Vihiga", "Bungoma", "Busia", "Siaya",
            "Kisumu", "Homa Bay", "Migori", "Kisii", "Nyamira", "Nairobi"
        }
        location = county + " County" + (" - " + local_area if local_area else "")
        farming_type = request.form.get("farming_type", "").strip()
        if county not in allowed_counties:
            return render_template(
                "index.html", error="Please select your county from the list."
            ), 400
        password = request.form.get("password", "")
        confirmation = request.form.get("confirmation", "")

        if not full_name or not farming_type:
            return render_template(
                "index.html", error="Please complete your name and farming type."            ), 400
        normal_phone = normalise_phone(phone)
        if normal_phone is None:
            return render_template(
                "index.html",
                error="Enter a valid Kenyan mobile number starting with 07 or 01, or use the equivalent +254 format."
            ), 400
        if len(password) < 5:
            return render_template(
                "index.html", error="Choose a password with at least 5 characters."
            ), 400
        if password != confirmation:
            return render_template(
                "index.html", error="The two password entries do not match."
            ), 400

        password_hash = generate_password_hash(password)
        with database_connection() as connection:
            # Serialise registrations for the same phone number to prevent duplicates.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (normal_phone,),
            )
            existing_phones = connection.execute(
                "SELECT phone FROM farmers WHERE phone IS NOT NULL AND TRIM(phone) <> ''"
            ).fetchall()
            # Ignore legacy records whose phone value is not a valid Kenyan mobile
            # number; compare valid old and new records in the same canonical format.
            if any(
                existing_normal_phone == normal_phone
                for existing_normal_phone in (
                    normalise_phone(row["phone"]) for row in existing_phones
                )
                if existing_normal_phone is not None
            ):
                return render_template(
                    "index.html",
                    error="An account with this phone number is already registered. Please log in instead."
                ), 409
            connection.execute(
                """INSERT INTO farmers
                   (full_name, phone, location, farming_type, password_hash)
                   VALUES (%s, %s, %s, %s, %s)""",
                (full_name, normal_phone, location, farming_type, password_hash),
            )


        return redirect(url_for("home", registered="yes") + "#register")

    registered = request.args.get("registered") == "yes"
    return render_template("index.html", registered=registered)


KENYAN_COUNTIES = ["Mombasa", "Kwale", "Kilifi", "Tana River", "Lamu", "Taita-Taveta", "Garissa", "Wajir", "Mandera", "Marsabit", "Isiolo", "Meru", "Tharaka-Nithi", "Embu", "Kitui", "Machakos", "Makueni", "Nyandarua", "Nyeri", "Kirinyaga", "Murang'a", "Kiambu", "Turkana", "West Pokot", "Samburu", "Trans Nzoia", "Uasin Gishu", "Elgeyo-Marakwet", "Nandi", "Baringo", "Laikipia", "Nakuru", "Narok", "Kajiado", "Kericho", "Bomet", "Kakamega", "Vihiga", "Bungoma", "Busia", "Siaya", "Kisumu", "Homa Bay", "Migori", "Kisii", "Nyamira", "Nairobi"]

WEATHER_CODES = {0:"Clear sky",1:"Mainly clear",2:"Partly cloudy",3:"Overcast",45:"Fog",48:"Fog",51:"Light drizzle",53:"Moderate drizzle",55:"Dense drizzle",56:"Freezing drizzle",57:"Heavy freezing drizzle",61:"Slight rain",63:"Moderate rain",65:"Heavy rain",66:"Light freezing rain",67:"Heavy freezing rain",71:"Slight snow",73:"Moderate snow",75:"Heavy snow",77:"Snow grains",80:"Slight rain showers",81:"Moderate rain showers",82:"Heavy rain showers",85:"Snow showers",86:"Heavy snow showers",95:"Thunderstorm",96:"Thunderstorm with hail",99:"Thunderstorm with heavy hail"}

def fetch_weather_json(url):
    """Fetch JSON from Open-Meteo and reject unsuccessful or invalid responses."""
    req = Request(url, headers={"User-Agent": "PwaniAgritalks/1.0"})
    with urlopen(req, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("The weather provider returned an invalid response.")
    return payload


def normalise_place_text(value):
    return " ".join(str(value or "").casefold().replace("-", " ").split())


def fetch_wttr_weather(place):
    """Backup weather provider used when Open-Meteo is temporarily rate-limited."""
    location = f'{place["latitude"]},{place["longitude"]}'
    backup_url = "https://wttr.in/" + location + "?" + urlencode({"format": "j1", "lang": "en", "m": ""})
    data = fetch_weather_json(backup_url)
    current_items = data.get("current_condition") or []
    forecast_items = data.get("weather") or []
    if not current_items or not forecast_items:
        raise ValueError("Backup weather provider returned incomplete data.")

    current_source = current_items[0]
    description = (current_source.get("weatherDesc") or [{}])[0].get("value", "Conditions unavailable")
    current = {
        "temperature_2m": current_source.get("temp_C"),
        "apparent_temperature": current_source.get("FeelsLikeC"),
        "relative_humidity_2m": current_source.get("humidity"),
        "precipitation": current_source.get("precipMM"),
        "wind_speed_10m": current_source.get("windspeedKmph")
    }

    days = []
    for item in forecast_items[:3]:
        hourly = item.get("hourly") or []
        day_description = "Conditions unavailable"
        if hourly:
            midday = hourly[min(4, len(hourly) - 1)]
            day_description = (midday.get("weatherDesc") or [{}])[0].get("value", day_description)
        rain_chances = [
            int(hour.get("chanceofrain") or 0)
            for hour in hourly if str(hour.get("chanceofrain") or "").isdigit()
        ]
        days.append({
            "date": item.get("date", ""),
            "condition": day_description,
            "high": item.get("maxtempC"),
            "low": item.get("mintempC"),
            "rain_chance": max(rain_chances) if rain_chances else None,
            "rain_total": item.get("totalPrecipMM", "0")
        })

    return {
        "place": place.get("name", "Selected area"),
        "county": "",
        "current": current,
        "condition": description,
        "days": days,
        "source": "wttr.in backup"
    }


@app.route("/weather")
@farmer_login_required
def weather():
    county = request.args.get("county", "").strip()
    local_area = request.args.get("local_area", "").strip()
    weather_data = None
    error = None

    if county:
        if county not in KENYAN_COUNTIES:
            error = "Please select a valid Kenyan county."
        else:
            search_name = local_area or county
            try:
                geocode_url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode({
                    "name": search_name, "count": 20, "language": "en",
                    "format": "json", "countryCode": "KE"
                })
                places = fetch_weather_json(geocode_url).get("results") or []

                if not places and local_area:
                    fallback_url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode({
                        "name": county, "count": 20, "language": "en",
                        "format": "json", "countryCode": "KE"
                    })
                    places = fetch_weather_json(fallback_url).get("results") or []

                if not places:
                    error = "We could not find that area. Try a nearby town or leave the town field blank."
                else:
                    county_key = normalise_place_text(county)

                    def county_match(place):
                        admin_names = [place.get("admin1"), place.get("admin2"),
                                       place.get("admin3"), place.get("admin4")]
                        return any(
                            county_key in normalise_place_text(name)
                            or normalise_place_text(name) in county_key
                            for name in admin_names if name
                        )

                    place = next((item for item in places if county_match(item)), places[0])
                    forecast_url = "https://api.open-meteo.com/v1/forecast?" + urlencode({
                        "latitude": place["latitude"], "longitude": place["longitude"],
                        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
                        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum",
                        "timezone": "Africa/Nairobi", "forecast_days": 3
                    })
                    try:
                        forecast = fetch_weather_json(forecast_url)
                    except HTTPError as provider_error:
                        if provider_error.code != 429:
                            raise
                        app.logger.warning("Open-Meteo rate limit reached; using wttr.in backup.")
                        weather_data = fetch_wttr_weather(place)
                        forecast = None

                    if forecast is not None:
                        current, daily = forecast.get("current") or {}, forecast.get("daily") or {}
                        required = ("time", "weather_code", "temperature_2m_max",
                                    "temperature_2m_min", "precipitation_sum")
                        if not current or any(key not in daily for key in required):
                            raise ValueError("The weather provider returned incomplete forecast data.")

                        days = []
                        rain_chances = daily.get("precipitation_probability_max") or []
                        for n, date_value in enumerate(daily["time"][:3]):
                            days.append({
                                "date": date_value,
                                "condition": WEATHER_CODES.get(daily["weather_code"][n], "Conditions unavailable"),
                                "high": daily["temperature_2m_max"][n],
                                "low": daily["temperature_2m_min"][n],
                                "rain_chance": rain_chances[n] if n < len(rain_chances) else None,
                                "rain_total": daily["precipitation_sum"][n]
                            })

                        weather_data = {
                            "place": place.get("name", search_name), "county": county,
                            "current": current,
                            "condition": WEATHER_CODES.get(current.get("weather_code"), "Conditions unavailable"),
                            "days": days,
                            "source": "Open-Meteo"
                        }
            except Exception:
                app.logger.exception("Weather lookup failed for county=%s, area=%s", county, local_area)
                error = "Weather information is temporarily unavailable. Please try again shortly."

    return render_template("weather.html", counties=KENYAN_COUNTIES, county=county,
                           local_area=local_area, weather=weather_data, error=error)

@app.route("/maize-guide")
@farmer_login_required
def maize_guide():
    return render_template("maize_guide.html")

@app.route("/chicken-guide")
@farmer_login_required
def chicken_guide():
    return render_template("chicken_guide.html")
@app.route("/poultry-guide")
@farmer_login_required
def poultry_guide():
    return render_template("chicken_guide.html")

@app.route("/rabbit-guide")
@farmer_login_required
def rabbit_guide():
    return render_template("rabbit_guide.html")

@app.route("/dairy-cows-guide")
@farmer_login_required
def dairy_cows_guide():
    return render_template("dairy_cows_guide.html")

@app.route("/goats-guide")
@farmer_login_required
def goats_guide():
    return render_template("goats_guide.html")

@app.route("/sustainable-agriculture-guide")
@farmer_login_required
def sustainable_agriculture_guide():
    return render_template("sustainable_agriculture_guide.html")
@app.route("/farmer-login", methods=["GET", "POST"])
def farmer_login():
    error = None
    if request.method == "POST":
        login_identifier = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        with database_connection() as connection:
            if any(character.isdigit() for character in login_identifier):
                normal_phone = normalise_phone(login_identifier)
                if normal_phone is None:
                    matching_farmers = []
                else:
                    all_farmers = connection.execute(
                        "SELECT id, phone, password_hash FROM farmers WHERE password_hash IS NOT NULL"
                    ).fetchall()
                    matching_farmers = [
                        farmer for farmer in all_farmers
                        if normalise_phone(farmer["phone"]) == normal_phone
                    ]
            else:
                # Keep name-based login working for farmers who used the older form.
                matching_farmers = connection.execute(
                    """SELECT id, password_hash FROM farmers
                       WHERE full_name ILIKE %s AND password_hash IS NOT NULL""",
                    (login_identifier,),
                ).fetchall()
        for matching_farmer in matching_farmers:
            if check_password_hash(matching_farmer["password_hash"], password):
                session.clear()
                session["farmer_id"] = matching_farmer["id"]
                return redirect(url_for("farmer_portal"))
        error = "The phone number or password is incorrect. If you have not registered, create an account first."

    return render_template("farmer_login.html", error=error)


@app.route("/farmer-portal")
@farmer_login_required
def farmer_portal():
    farmer = get_farmer(session["farmer_id"])
    if farmer is None:
        session.clear()
        return redirect(url_for("farmer_login"))
    with database_connection() as connection:
        unread_notifications = connection.execute(
            "SELECT COUNT(*) AS count FROM farmer_notifications WHERE farmer_id = %s AND read_at IS NULL",
            (session["farmer_id"],),
        ).fetchone()["count"]
    return render_template(
        "farmer_portal.html", farmer=farmer,
        unread_notifications=unread_notifications
    )


@app.route("/farmer-notifications", methods=["GET", "POST"])
@farmer_login_required
def farmer_notifications():
    farmer_id = session["farmer_id"]
    if request.method == "POST":
        notification_id = request.form.get("notification_id", type=int)
        if notification_id is None:
            abort(400)
        with database_connection() as connection:
            connection.execute(
                """UPDATE farmer_notifications SET read_at = CURRENT_TIMESTAMP
                   WHERE id = %s AND farmer_id = %s AND read_at IS NULL""",
                (notification_id, farmer_id),
            )
        return redirect(url_for("farmer_notifications"))

    with database_connection() as connection:
        notifications = connection.execute(
            """SELECT id, question_id, message, created_at, read_at
               FROM farmer_notifications WHERE farmer_id = %s
               ORDER BY created_at DESC, id DESC LIMIT 100""",
            (farmer_id,),
        ).fetchall()
        unread_notifications = connection.execute(
            "SELECT COUNT(*) AS count FROM farmer_notifications WHERE farmer_id = %s AND read_at IS NULL",
            (farmer_id,),
        ).fetchone()["count"]
    return render_template(
        "farmer_notifications.html",
        notifications=notifications,
        unread_notifications=unread_notifications,
    )


@app.route("/farmer-change-password", methods=["GET", "POST"])
@farmer_login_required
def farmer_change_password():
    farmer = get_farmer(session["farmer_id"])
    if farmer is None:
        session.clear()
        return redirect(url_for("farmer_login"))

    error = None
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirmation = request.form.get("confirmation", "")

        if not check_password_hash(farmer["password_hash"], current_password):
            error = "Your current password is incorrect."
        elif len(new_password) < 5:
            error = "Choose a new password with at least 5 characters."
        elif new_password != confirmation:
            error = "The two new password entries do not match."
        else:
            with database_connection() as connection:
                connection.execute(
                    "UPDATE farmers SET password_hash = %s WHERE id = %s",
                    (generate_password_hash(new_password), session["farmer_id"]),
                )
            return redirect(url_for("farmer_portal", changed="yes"))

    return render_template("farmer_change_password.html", error=error)

@app.route("/farmer-profile", methods=["GET", "POST"])
@farmer_login_required
def edit_own_profile():
    farmer = get_farmer(session["farmer_id"])
    if farmer is None:
        session.clear()
        return redirect(url_for("farmer_login"))

    error = None
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        location = request.form.get("location", "").strip()
        farming_type = request.form.get("farming_type", "").strip()
        allowed_types = {"crops", "livestock", "mixed", "other"}

        if not phone or not location or farming_type not in allowed_types:
            error = "Please complete each field and choose a farming type."
        else:
            phone_digits = "".join(character for character in phone if character.isdigit())
            if not 7 <= len(phone_digits) <= 15:
                error = "Enter a phone number containing 7 to 15 digits."

        if error:
            farmer_values = dict(farmer)
            farmer_values.update({
                "phone": phone, "location": location, "farming_type": farming_type
            })
            return render_template(
                "edit_my_profile.html", farmer=farmer_values, error=error
            ), 400

        with database_connection() as connection:
            connection.execute(
                "UPDATE farmers SET phone = %s, location = %s, farming_type = %s WHERE id = %s",
                (phone, location, farming_type, session["farmer_id"]),
            )
        return redirect(url_for("farmer_portal", updated="yes"))

    return render_template("edit_my_profile.html", farmer=farmer)

@app.route("/farmer-logout", methods=["POST"])
@farmer_login_required
def farmer_logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/farmer-questions", methods=["GET", "POST"])
@farmer_login_required
def farmer_questions():
    farmer_id = session["farmer_id"]
    farmer = get_farmer(farmer_id)
    if farmer is None:
        session.clear()
        return redirect(url_for("farmer_login"))

    error = None
    if request.method == "POST":
        question_text = request.form.get("question_text", "").strip()
        photo = request.files.get("photo")
        photo_bytes = None
        photo_extension = None

        if not question_text:
            error = "Please write your question before submitting."
        elif len(question_text) > 2000:
            error = "Please keep your question under 2,000 characters."
        elif photo and photo.filename:
            photo_extension = Path(photo.filename).suffix.lower()
            allowed_signatures = {
                ".jpg": lambda data: data.startswith(b"\xff\xd8\xff"),
                ".jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
                ".png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
                ".webp": lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
            }
            signature_check = allowed_signatures.get(photo_extension)
            if signature_check is None:                error = "Choose a JPG, PNG, or WEBP photo."
            else:
                photo_bytes = photo.stream.read(MAX_QUESTION_PHOTO_BYTES + 1)
                if len(photo_bytes) > MAX_QUESTION_PHOTO_BYTES:
                    error = "Please choose a photo smaller than 5 MB."
                elif not signature_check(photo_bytes):
                    error = "That file does not look like a valid JPG, PNG, or WEBP photo."

        if error is None:
            saved_photo_name = None
            if photo_bytes is not None:
                QUESTION_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
                saved_photo_name = f"{secrets.token_hex(24)}{photo_extension}"
                (QUESTION_PHOTO_DIR / saved_photo_name).write_bytes(photo_bytes)
            try:
                with database_connection() as connection:
                    connection.execute(
                        "INSERT INTO farmer_questions (farmer_id, question_text, photo_filename) VALUES (%s, %s, %s)",
                        (farmer_id, question_text, saved_photo_name),
                    )
            except Exception:
                if saved_photo_name:                    (QUESTION_PHOTO_DIR / saved_photo_name).unlink(missing_ok=True)
                raise
            return redirect(url_for("farmer_questions", sent="yes"))

    with database_connection() as connection:
        my_questions = connection.execute(
            """SELECT id, question_text, created_at, status, answer, answered_at, photo_filename FROM farmer_questions
               WHERE farmer_id = %s ORDER BY id DESC""",
            (farmer_id,),
        ).fetchall()

    return render_template(
        "farmer_questions.html", farmer=farmer, questions=my_questions,
        error=error, sent=request.args.get("sent") == "yes"
    )


@app.route("/question-photo/<int:question_id>")
def question_photo(question_id):
    with database_connection() as connection:
        question = connection.execute(
            "SELECT farmer_id, photo_filename FROM farmer_questions WHERE id = %s",
            (question_id,),
        ).fetchone()
    if question is None or not question["photo_filename"]:
        abort(404)
    if "admin_username" not in session and session.get("farmer_id") != question["farmer_id"]:
        abort(403)

    response = send_from_directory(
        QUESTION_PHOTO_DIR, question["photo_filename"], as_attachment=False, max_age=0
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/questions")
@login_required
def admin_questions():
    with database_connection() as connection:
        questions = connection.execute(
            """SELECT farmer_questions.id, farmer_questions.question_text,
                      farmer_questions.created_at, farmer_questions.status,
                      farmer_questions.answer, farmer_questions.answered_at, farmer_questions.photo_filename,
                      farmers.full_name, farmers.location
               FROM farmer_questions
               JOIN farmers ON farmers.id = farmer_questions.farmer_id
               ORDER BY farmer_questions.id DESC"""
        ).fetchall()
        unanswered_count = connection.execute(
            "SELECT COUNT(*) AS count FROM farmer_questions WHERE answer IS NULL OR TRIM(answer) = ''"
        ).fetchone()["count"]
    return render_template(
        "admin_questions.html", questions=questions, unanswered_count=unanswered_count
    )

@app.route("/questions/<int:question_id>/reply", methods=["POST"])
@login_required
def reply_to_question(question_id):
    answer = request.form.get("answer", "").strip()
    if not answer:
        return redirect(url_for("admin_questions", error="empty"))
    if len(answer) > 2000:
        return redirect(url_for("admin_questions", error="long"))

    with database_connection() as connection:
        question = connection.execute(
            "SELECT farmer_id FROM farmer_questions WHERE id = %s", (question_id,)
        ).fetchone()
        if question is None:
            abort(404)
        connection.execute(
            """UPDATE farmer_questions
               SET answer = %s, answered_at = CURRENT_TIMESTAMP, status = 'Answered'
               WHERE id = %s""",
            (answer, question_id),
        )
        connection.execute(
            """INSERT INTO farmer_notifications (farmer_id, question_id, message)
               VALUES (%s, %s, %s)""",
            (
                question["farmer_id"],
                question_id,
                "The PWANIAGRITALKS admin has answered your farming question.",
            ),
        )
    return redirect(url_for("admin_questions", replied="yes"))

@app.route("/setup", methods=["GET", "POST"])
@app.route("/create-first-admin", methods=["GET", "POST"])
def setup():
    # Remote setup is permitted only when a private, one-time environment token is configured.
    setup_token = os.environ.get("ADMIN_SETUP_TOKEN", "")
    if not setup_token:
        abort(403)
    if admin_exists():
        return redirect(url_for("login"))

    error = None
    if request.method == "POST":
        submitted_token = request.form.get("setup_token", "")
        if not secrets.compare_digest(submitted_token, setup_token):
            abort(403)

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirmation = request.form.get("confirmation", "")

        if len(username) < 3:
            error = "Choose a username with at least 3 characters."
        elif len(password) < 12:
            error = "Choose a password with at least 12 characters."
        elif password != confirmation:
            error = "The two password entries do not match."
        else:
            with database_connection() as connection:
                connection.execute(
                    "INSERT INTO admin_users (username, password_hash) VALUES (%s, %s)",
                    (username, generate_password_hash(password)),
                )
            return redirect(url_for("login", created="yes"))

    return render_template("setup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
@app.route("/admin-login", methods=["GET", "POST"])
def login():
    if not admin_exists():
        if os.environ.get("ADMIN_SETUP_TOKEN"):
            return redirect(url_for("setup"))
        abort(403)

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with database_connection() as connection:
            admin = connection.execute(
                "SELECT username, password_hash FROM admin_users WHERE username = %s",
                (username,),
            ).fetchone()

        if admin and check_password_hash(admin["password_hash"], password):
            session.clear()
            session["admin_username"] = admin["username"]
            return redirect(url_for("admin_dashboard"))
        error = "The username or password is incorrect."

    return render_template(
        "login.html", error=error, created=request.args.get("created") == "yes"
    )


@app.route("/admin-recover", methods=["GET", "POST"])
def admin_recover():
    setup_token = os.environ.get("ADMIN_SETUP_TOKEN", "")

    if not setup_token:
        abort(403)

    error = None

    if request.method == "POST":
        submitted_token = request.form.get("recovery_token", "")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirmation = request.form.get("confirmation", "")

        if not secrets.compare_digest(submitted_token, setup_token):
            abort(403)

        if len(username) < 3:
            error = "Username must have at least 3 characters."
        elif len(password) < 12:
            error = "Password must have at least 12 characters."
        elif password != confirmation:
            error = "Passwords do not match."
        else:
            with database_connection() as connection:
                result = connection.execute(
                    """
                    UPDATE admin_users
                    SET username = %s, password_hash = %s
                    WHERE id = (
                        SELECT id FROM admin_users ORDER BY id LIMIT 1
                    )                    RETURNING id
                    """,
                    (username, generate_password_hash(password)),
                ).fetchone()

            if result:
                return redirect(url_for("login"))

            error = "No administrator account exists to recover."

    return render_template("admin_recover.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))



@app.route("/admin-dashboard")
@login_required
def admin_dashboard():
    with database_connection() as connection:
        total_farmers = connection.execute(
            "SELECT COUNT(*) AS count FROM farmers"
        ).fetchone()["count"]
        total_questions = connection.execute(
            "SELECT COUNT(*) AS count FROM farmer_questions"
        ).fetchone()["count"]
        unanswered_questions = connection.execute(
            "SELECT COUNT(*) AS count FROM farmer_questions WHERE answer IS NULL OR TRIM(answer) = ''"
        ).fetchone()["count"]
        counts_by_type = {
            row["farming_type"]: row["count"]
            for row in connection.execute(
                "SELECT farming_type, COUNT(*) AS count FROM farmers GROUP BY farming_type"
            ).fetchall()
        }
    return render_template(
        "admin_dashboard.html",
        admin_username=session["admin_username"],
        total_farmers=total_farmers,
        total_questions=total_questions,
        unanswered_questions=unanswered_questions,
        counts_by_type=counts_by_type,
    )

@app.route("/farmers")
@login_required
def farmers():
    search_text = request.args.get("q", "").strip()
    with database_connection() as connection:
        if search_text:
            search_pattern = f"%{search_text}%"
            saved_farmers = connection.execute(
                """SELECT id, full_name, location, farming_type FROM farmers
                   WHERE full_name LIKE %s OR location LIKE %s OR farming_type LIKE %s
                   ORDER BY id DESC""",
                (search_pattern, search_pattern, search_pattern),
            ).fetchall()
        else:
            saved_farmers = connection.execute(
                "SELECT id, full_name, location, farming_type FROM farmers ORDER BY id DESC"
            ).fetchall()

        counts_by_type = {
            row["farming_type"]: row["count"] for row in connection.execute(
                "SELECT farming_type, COUNT(*) AS count FROM farmers GROUP BY farming_type"
            ).fetchall()
        }

    total_farmers = sum(counts_by_type.values())
    return render_template(
        "farmers.html", farmers=saved_farmers, search_text=search_text,
        counts_by_type=counts_by_type, total_farmers=total_farmers,
        admin_username=session["admin_username"]
    )


@app.route("/farmers/export")
@login_required
def export_farmers():
    search_text = request.args.get("q", "").strip()
    with database_connection() as connection:
        if search_text:
            search_pattern = f"%{search_text}%"
            saved_farmers = connection.execute(
                """SELECT full_name, location, farming_type FROM farmers
                   WHERE full_name LIKE %s OR location LIKE %s OR farming_type LIKE %s
                   ORDER BY id DESC""",
                (search_pattern, search_pattern, search_pattern),
            ).fetchall()
        else:
            saved_farmers = connection.execute(
                "SELECT full_name, location, farming_type FROM farmers ORDER BY id DESC"
            ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Full name", "Location", "Farming type"])
    for row in saved_farmers:
        safe_row = [
            "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
            for value in row.values()
        ]
        writer.writerow(safe_row)

    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=farmers.csv"},
    )


@app.route("/farmers/<int:farmer_id>/edit", methods=["GET", "POST"])
@login_required
def edit_farmer(farmer_id):
    farmer = get_farmer(farmer_id)
    if farmer is None:
        abort(404)

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        location = request.form.get("location", "").strip()
        farming_type = request.form.get("farming_type", "").strip()
        new_password = request.form.get("new_password", "")
        normal_phone = normalise_phone(phone)

        if not all([full_name, phone, location, farming_type]):
            return render_template(
                "edit_farmer.html", farmer=farmer,
                error="Please fill in every field."
            ), 400

        if normal_phone is None:
            farmer_values = dict(farmer)
            farmer_values.update({
                "full_name": full_name, "phone": phone,
                "location": location, "farming_type": farming_type,
            })
            return render_template(
                "edit_farmer.html", farmer=farmer_values,
                error="Enter a valid Kenyan mobile number starting with 07 or 01, or use the equivalent +254 format."
            ), 400

        if farming_type not in {"crops", "livestock", "mixed", "other"}:
            return render_template(
                "edit_farmer.html", farmer=farmer,
                error="Choose a valid farming type."
            ), 400

        if new_password and len(new_password) < 5:
            return render_template(
                "edit_farmer.html", farmer=farmer,
                error="A new farmer password must contain at least 5 characters."
            ), 400

        with database_connection() as connection:
            other_phones = connection.execute(
                "SELECT phone FROM farmers WHERE id <> %s",
                (farmer_id,),
            ).fetchall()
            if any(
                existing_phone == normal_phone
                for existing_phone in (
                    normalise_phone(row["phone"]) for row in other_phones
                )
                if existing_phone is not None
            ):
                farmer_values = dict(farmer)
                farmer_values.update({
                    "full_name": full_name, "phone": phone,
                    "location": location, "farming_type": farming_type,
                })
                return render_template(
                    "edit_farmer.html", farmer=farmer_values,
                    error="That phone number is already registered to another farmer."
                ), 409

            if new_password:
                connection.execute(
                    """UPDATE farmers SET full_name = %s, phone = %s, location = %s,
                       farming_type = %s, password_hash = %s WHERE id = %s""",
                    (full_name, normal_phone, location, farming_type,
                     generate_password_hash(new_password), farmer_id),
                )
            else:
                connection.execute(
                    """UPDATE farmers SET full_name = %s, phone = %s, location = %s,
                       farming_type = %s WHERE id = %s""",
                    (full_name, normal_phone, location, farming_type, farmer_id),
                )

        return redirect(url_for("farmers", updated="yes"))

    return render_template("edit_farmer.html", farmer=farmer)


@app.route("/farmers/<int:farmer_id>/delete", methods=["POST"])
@login_required
def delete_farmer(farmer_id):
    with database_connection() as connection:
        farmer_exists = connection.execute(
            "SELECT 1 FROM farmers WHERE id = %s",
            (farmer_id,),
        ).fetchone()
        if farmer_exists is None:
            abort(404)

        linked_questions = connection.execute(
            "SELECT 1 FROM farmer_questions WHERE farmer_id = %s LIMIT 1",
            (farmer_id,),
        ).fetchone()
        if linked_questions:
            return redirect(url_for("farmers", delete_blocked="yes"))

        connection.execute("DELETE FROM farmers WHERE id = %s", (farmer_id,))

    return redirect(url_for("farmers", deleted="yes"))


create_database()


if __name__ == "__main__":
    app.run(debug=True)
