import csv
import io
import os
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from functools import wraps

from flask import Flask, Response, abort, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
DATABASE = Path(__file__).with_name("farmers.db")


@contextmanager
def database_connection():
    connection = sqlite3.connect(DATABASE)
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                location TEXT NOT NULL,
                farming_type TEXT NOT NULL,
                password_hash TEXT
            )
        """)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(farmers)").fetchall()
        }
        if "password_hash" not in columns:
            connection.execute("ALTER TABLE farmers ADD COLUMN password_hash TEXT")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS farmer_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                farmer_id INTEGER NOT NULL,
                question_text TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'Waiting for a response'
            )
        """)
        question_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(farmer_questions)").fetchall()
        }
        if "answer" not in question_columns:
            connection.execute("ALTER TABLE farmer_questions ADD COLUMN answer TEXT")
        if "answered_at" not in question_columns:
            connection.execute("ALTER TABLE farmer_questions ADD COLUMN answered_at TEXT")
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
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT * FROM farmers WHERE id = ?", (farmer_id,)
        ).fetchone()


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        location = request.form.get("location", "").strip()
        farming_type = request.form.get("farming_type", "").strip()
        password = request.form.get("password", "")
        confirmation = request.form.get("confirmation", "")

        phone_digits = "".join(character for character in phone if character.isdigit())
        if not 7 <= len(phone_digits) <= 15:
            return render_template(
                "index.html", error="Enter a phone number containing 7 to 15 digits."
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
            connection.execute(
                """INSERT INTO farmers
                   (full_name, phone, location, farming_type, password_hash)
                   VALUES (?, ?, ?, ?, ?)""",
                (full_name, phone, location, farming_type, password_hash),
            )
        

        return redirect(url_for("home", registered="yes") + "#register")

    registered = request.args.get("registered") == "yes"
    return render_template("index.html", registered=registered)

@app.route("/maize-guide")
def maize_guide():
    return render_template("maize_guide.html")

@app.route("/chicken-guide")
def chicken_guide():
    return render_template("chicken_guide.html")
@app.route("/poultry-guide")
def poultry_guide():
    return render_template("chicken_guide.html")

@app.route("/rabbit-guide")
def rabbit_guide():
    return render_template("rabbit_guide.html")

@app.route("/dairy-cows-guide")
def dairy_cows_guide():
    return render_template("dairy_cows_guide.html")

@app.route("/goats-guide")
def goats_guide():
    return render_template("goats_guide.html")

@app.route("/sustainable-agriculture-guide")
def sustainable_agriculture_guide():
    return render_template("sustainable_agriculture_guide.html")
@app.route("/farmer-login", methods=["GET", "POST"])
def farmer_login():
    error = None
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        with database_connection() as connection:
            matching_farmers = connection.execute(
                """SELECT id, password_hash FROM farmers
                   WHERE full_name = ? COLLATE NOCASE AND password_hash IS NOT NULL""",
                (full_name,),
            ).fetchall()

        for farmer_id, password_hash in matching_farmers:
            if check_password_hash(password_hash, password):
                session.clear()
                session["farmer_id"] = farmer_id
                return redirect(url_for("farmer_portal"))
        error = "The name or password is incorrect."

    return render_template("farmer_login.html", error=error)


@app.route("/farmer-portal")
@farmer_login_required
def farmer_portal():
    farmer = get_farmer(session["farmer_id"])
    if farmer is None:
        session.clear()
        return redirect(url_for("farmer_login"))
    return render_template("farmer_portal.html", farmer=farmer)


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
                    "UPDATE farmers SET password_hash = ? WHERE id = ?",
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
                "UPDATE farmers SET phone = ?, location = ?, farming_type = ? WHERE id = ?",
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
            if signature_check is None:
                error = "Choose a JPG, PNG, or WEBP photo."
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
                        "INSERT INTO farmer_questions (farmer_id, question_text, photo_filename) VALUES (?, ?, ?)",
                        (farmer_id, question_text, saved_photo_name),
                    )
            except Exception:
                if saved_photo_name:
                    (QUESTION_PHOTO_DIR / saved_photo_name).unlink(missing_ok=True)
                raise
            return redirect(url_for("farmer_questions", sent="yes"))

    with database_connection() as connection:
        connection.row_factory = sqlite3.Row
        my_questions = connection.execute(
            """SELECT id, question_text, created_at, status, answer, answered_at, photo_filename FROM farmer_questions
               WHERE farmer_id = ? ORDER BY id DESC""",
            (farmer_id,),
        ).fetchall()

    return render_template(
        "farmer_questions.html", farmer=farmer, questions=my_questions,
        error=error, sent=request.args.get("sent") == "yes"
    )


@app.route("/question-photo/<int:question_id>")
def question_photo(question_id):
    with database_connection() as connection:
        connection.row_factory = sqlite3.Row
        question = connection.execute(
            "SELECT farmer_id, photo_filename FROM farmer_questions WHERE id = ?",
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
        connection.row_factory = sqlite3.Row
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
            "SELECT COUNT(*) FROM farmer_questions WHERE answer IS NULL OR TRIM(answer) = ''"
        ).fetchone()[0]
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
        question_exists = connection.execute(
            "SELECT 1 FROM farmer_questions WHERE id = ?", (question_id,)
        ).fetchone()
        if question_exists is None:
            abort(404)
        connection.execute(
            """UPDATE farmer_questions
               SET answer = ?, answered_at = CURRENT_TIMESTAMP, status = 'Answered'
               WHERE id = ?""",
            (answer, question_id),
        )
    return redirect(url_for("admin_questions", replied="yes"))

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        abort(403)
    if admin_exists():
        return redirect(url_for("login"))

    error = None
    if request.method == "POST":
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
                    "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
                    (username, generate_password_hash(password)),
                )
            return redirect(url_for("login", created="yes"))

    return render_template("setup.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    if not admin_exists():
        if request.remote_addr in {"127.0.0.1", "::1"}:
            return redirect(url_for("setup"))
        abort(403)

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with database_connection() as connection:
            admin = connection.execute(
                "SELECT username, password_hash FROM admin_users WHERE username = ?",
                (username,),
            ).fetchone()

        if admin and check_password_hash(admin[1], password):
            session.clear()
            session["admin_username"] = admin[0]
            return redirect(url_for("farmers"))
        error = "The username or password is incorrect."

    return render_template(
        "login.html", error=error, created=request.args.get("created") == "yes"
    )


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/farmers")
@login_required
def farmers():
    search_text = request.args.get("q", "").strip()
    with database_connection() as connection:
        connection.row_factory = sqlite3.Row
        if search_text:
            search_pattern = f"%{search_text}%"
            saved_farmers = connection.execute(
                """SELECT id, full_name, location, farming_type FROM farmers
                   WHERE full_name LIKE ? OR location LIKE ? OR farming_type LIKE ?
                   ORDER BY id DESC""",
                (search_pattern, search_pattern, search_pattern),
            ).fetchall()
        else:
            saved_farmers = connection.execute(
                "SELECT id, full_name, location, farming_type FROM farmers ORDER BY id DESC"
            ).fetchall()

        counts_by_type = dict(connection.execute(
            "SELECT farming_type, COUNT(*) FROM farmers GROUP BY farming_type"
        ).fetchall())

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
                   WHERE full_name LIKE ? OR location LIKE ? OR farming_type LIKE ?
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
            "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
            for value in row
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

        if not all([full_name, phone, location, farming_type]):
            return render_template(
                "edit_farmer.html", farmer=farmer,
                error="Please fill in every field."
            ), 400

        phone_digits = "".join(character for character in phone if character.isdigit())
        if not 7 <= len(phone_digits) <= 15:
            farmer_values = dict(farmer)
            farmer_values.update({
                "full_name": full_name, "phone": phone,
                "location": location, "farming_type": farming_type,
            })
            return render_template(
                "edit_farmer.html", farmer=farmer_values,
                error="Enter a phone number containing 7 to 15 digits."
            ), 400

        if new_password and len(new_password) < 5:
            return render_template(
                "edit_farmer.html", farmer=farmer,
                error="A new farmer password must contain at least 5 characters."
            ), 400

        with database_connection() as connection:
            if new_password:
                connection.execute(
                    """UPDATE farmers SET full_name = ?, phone = ?, location = ?,
                       farming_type = ?, password_hash = ? WHERE id = ?""",
                    (full_name, phone, location, farming_type,
                     generate_password_hash(new_password), farmer_id),
                )
            else:
                connection.execute(
                    """UPDATE farmers SET full_name = ?, phone = ?, location = ?,
                       farming_type = ? WHERE id = ?""",
                    (full_name, phone, location, farming_type, farmer_id),
                )

        return redirect(url_for("farmers", updated="yes"))

    return render_template("edit_farmer.html", farmer=farmer)


@app.route("/farmers/<int:farmer_id>/delete", methods=["POST"])
@login_required
def delete_farmer(farmer_id):
    with database_connection() as connection:
        connection.execute("DELETE FROM farmers WHERE id = ?", (farmer_id,))
    return redirect(url_for("farmers", deleted="yes"))


create_database()

if __name__ == "__main__":
    app.run(debug=True)
    
    










