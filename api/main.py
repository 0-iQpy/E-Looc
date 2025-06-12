import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import pytz
from dateutil import parser
from dotenv import load_dotenv
from supabase import create_client, Client
import time

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception(
        "SUPABASE_URL and SUPABASE_KEY must be set in your environment variables"
    )
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "admin_login"

# In app.py, modify get_manila_time() function
def get_manila_time():
    manila_tz = pytz.timezone("Asia/Manila")
    return datetime.now(manila_tz)

# Helper function to upload image to Supabase Storage
def upload_to_supabase_storage(file, bucket_name):
    if not file or not file.filename:
        app.logger.info("upload_to_supabase_storage: No file or filename provided.")
        return None

    try:
        # Corrected f-string:
        filename = f"{int(time.time())}_{secure_filename(file.filename)}"

        app.logger.info(f"Attempting to upload {filename} to bucket {bucket_name}")

        # Perform the upload
        supabase.storage.from_(bucket_name).upload(
            path=filename,
            file=file.read(), # file.read() is important to get bytes
            file_options={"content-type": file.content_type}
        )

        # If no exception was raised, the upload is successful.
        # Get the public URL using the Supabase client's method.
        public_url = supabase.storage.from_(bucket_name).get_public_url(filename)
        app.logger.info(f"Successfully uploaded {filename} to {bucket_name}. Public URL: {public_url}")
        return public_url

    except Exception as e:
        app.logger.error(f"Error uploading {file.filename if file else 'unknown file'} to {bucket_name}: {type(e).__name__} - {str(e)}")
        return None

# Helper function to delete image from Supabase Storage
def delete_from_supabase_storage(image_url, bucket_name):
    if not image_url:
        return False
    try:
        # Extract filename from URL
        filename = image_url.split(f"{bucket_name}/")[-1]
        if not filename:
            return False
        response = supabase.storage.from_(bucket_name).remove([filename])
        if response.status_code == 200 and response.json(): # Check if deletion was successful
             # Check if the first item in the response list (if any) indicates success
            if response.json()[0].get('message') == 'Successfully removed':
                return True
            else:
                app.logger.error(f"Supabase delete error response: {response.json()}")
                return False
        else:
            app.logger.error(f"Supabase delete error status {response.status_code}: {response.json()}")
            return False
    except Exception as e:
        app.logger.error(f"Error deleting from Supabase: {e}")
        return False

class User(UserMixin):
    def __init__(self, id, username, password_hash, name, role):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.name = name
        self.role = role

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    try:
        resp = supabase.table("users").select("*").eq("id", int(user_id)).single().execute()
        if resp.data:
            user = resp.data
            return User(
                id=user["id"],
                username=user["username"],
                password_hash=user["password_hash"],
                name=user["name"],
                role=user["role"],
            )
    except Exception:
        pass
    return None


@app.route("/")
def index():
    bulletins_resp = (
        supabase.table("bulletin_posts")
        .select("*, image_url")
        .eq("is_active", True)
        .order("date_posted", desc=True)
        .limit(8)
        .execute()
    )
    news_resp = (
        supabase.table("news_posts")
        .select("*, image_url")
        .eq("is_active", True)
        .order("date_posted", desc=True)
        .limit(8)
        .execute()
    )
    bulletins = bulletins_resp.data or []
    news = news_resp.data or []
    manila_time = get_manila_time().strftime("%B %d, %Y %I:%M %p")
    report_form_url = os.getenv('REPORT_FORM_URL', 'https://forms.gle/vbALkyz6zaZStF4k6')
    return render_template("home.html", bulletins=bulletins, news=news, report_form_url=report_form_url)


@app.route("/admin")
def admin_redirect():
    return redirect(url_for("admin_login"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if current_user.is_authenticated:
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        try:
            user_resp = supabase.table("users").select("*").eq("username", username).single().execute()
            user = user_resp.data
        except Exception:
            user = None

        if user and check_password_hash(user["password_hash"], password):
            user_obj = User(
                user["id"],
                user["username"],
                user["password_hash"],
                user["name"],
                user["role"],
            )
            login_user(user_obj)
            flash("Login successful!", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("admin_dashboard"))
        else:
            flash("Invalid username or password", "danger")

    return render_template("admin/login.html")


@app.route("/admin/logout")
@login_required
def admin_logout():
    logout_user()
    flash("You have been logged out", "success")
    return redirect(url_for("admin_login"))


@app.route("/admin/dashboard")
@login_required
def admin_dashboard():
    bulletin_count_resp = supabase.table("bulletin_posts").select("id", count="exact").execute()
    news_count_resp = supabase.table("news_posts").select("id", count="exact").execute()

    bulletin_count = bulletin_count_resp.count or 0
    news_count = news_count_resp.count or 0

    return render_template(
        "admin/dashboard.html", bulletin_count=bulletin_count, news_count=news_count
    )


# Bulletin Management

@app.route("/admin/bulletins")
@login_required
def admin_bulletins():
    bulletins_resp = supabase.table("bulletin_posts").select("*, image_url").order("date_posted", desc=True).execute()
    bulletins = bulletins_resp.data or []
    return render_template("admin/bulletins/index.html", bulletins=bulletins)


@app.route("/admin/bulletins/create", methods=["GET", "POST"])
@login_required
def admin_create_bulletin():
    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")
        is_active = bool(request.form.get("is_active"))
        image_file = request.files.get("image")
        image_url = None

        if image_file:
            image_url = upload_to_supabase_storage(image_file, "bulletin-images")
            if not image_url:
                flash("Image upload failed. Please try again.", "danger")
                return render_template("admin/bulletins/create.html")


        data = {
            "title": title,
            "content": content,
            "is_active": is_active,
            "created_by": current_user.id,
            "date_posted": get_manila_time().isoformat(),
            "image_url": image_url,
        }

        supabase.table("bulletin_posts").insert(data).execute()

        flash("Bulletin created successfully!", "success")
        return redirect(url_for("admin_bulletins"))

    return render_template("admin/bulletins/create.html")


@app.route("/admin/bulletins/edit/<int:id>", methods=["GET", "POST"])
@login_required
def admin_edit_bulletin(id):
    resp = supabase.table("bulletin_posts").select("*, image_url").eq("id", id).single().execute()
    bulletin = resp.data

    if not bulletin:
        flash("Bulletin not found", "danger")
        return redirect(url_for("admin_bulletins"))

    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")
        is_active = bool(request.form.get("is_active"))
        image_file = request.files.get("image")
        remove_image = request.form.get("remove_image") == "true"
        old_image_url = bulletin.get("image_url")

        update_data = {
            "title": title,
            "content": content,
            "is_active": is_active,
        }

        if remove_image:
            if old_image_url:
                delete_from_supabase_storage(old_image_url, "bulletin-images")
            update_data["image_url"] = None
        elif image_file:
            if old_image_url:
                delete_from_supabase_storage(old_image_url, "bulletin-images")
            new_image_url = upload_to_supabase_storage(image_file, "bulletin-images")
            if new_image_url:
                update_data["image_url"] = new_image_url
            else:
                flash("Image upload failed. Please try again.", "danger")
                return render_template("admin/bulletins/edit.html", bulletin=bulletin)

        supabase.table("bulletin_posts").update(update_data).eq("id", id).execute()

        flash("Bulletin updated successfully!", "success")
        return redirect(url_for("admin_bulletins"))

    return render_template("admin/bulletins/edit.html", bulletin=bulletin)


@app.route("/admin/bulletins/delete/<int:id>", methods=["POST"])
@login_required
def admin_delete_bulletin(id):
    # Fetch the bulletin to get its image_url before deleting
    resp = supabase.table("bulletin_posts").select("image_url").eq("id", id).single().execute()
    bulletin_data = resp.data

    if bulletin_data and bulletin_data.get("image_url"):
        delete_from_supabase_storage(bulletin_data["image_url"], "bulletin-images")

    supabase.table("bulletin_posts").delete().eq("id", id).execute()
    flash("Bulletin deleted successfully!", "success")
    return redirect(url_for("admin_bulletins"))


# News Management

@app.route("/admin/news")
@login_required
def admin_news():
    news_resp = supabase.table("news_posts").select("*, image_url").order("date_posted", desc=True).execute()
    news_items = news_resp.data or []
    return render_template("admin/news/index.html", news_items=news_items)


@app.route("/admin/news/create", methods=["GET", "POST"])
@login_required
def admin_create_news():
    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")
        is_active = bool(request.form.get("is_active"))
        image_file = request.files.get("image")
        image_url = None

        if image_file:
            image_url = upload_to_supabase_storage(image_file, "news-and-events-images")
            if not image_url:
                flash("Image upload failed. Please try again.", "danger")
                return render_template("admin/news/create.html")

        data = {
            "title": title,
            "content": content,
            "is_active": is_active,
            "created_by": current_user.id,
            "date_posted": get_manila_time().isoformat(),
            "image_url": image_url,
        }

        supabase.table("news_posts").insert(data).execute()

        flash("News item created successfully!", "success")
        return redirect(url_for("admin_news"))

    return render_template("admin/news/create.html")


@app.route("/admin/news/edit/<int:id>", methods=["GET", "POST"])
@login_required
def admin_edit_news(id):
    resp = supabase.table("news_posts").select("*, image_url").eq("id", id).single().execute()
    news = resp.data

    if not news:
        flash("News item not found", "danger")
        return redirect(url_for("admin_news"))

    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")
        is_active = bool(request.form.get("is_active"))
        image_file = request.files.get("image")
        remove_image = request.form.get("remove_image") == "true"
        old_image_url = news.get("image_url")

        update_data = {
            "title": title,
            "content": content,
            "is_active": is_active,
        }

        if remove_image:
            if old_image_url:
                delete_from_supabase_storage(old_image_url, "news-and-events-images")
            update_data["image_url"] = None
        elif image_file:
            if old_image_url:
                delete_from_supabase_storage(old_image_url, "news-and-events-images")
            new_image_url = upload_to_supabase_storage(image_file, "news-and-events-images")
            if new_image_url:
                update_data["image_url"] = new_image_url
            else:
                flash("Image upload failed. Please try again.", "danger")
                return render_template("admin/news/edit.html", news=news)

        supabase.table("news_posts").update(update_data).eq("id", id).execute()

        flash("News & Events updated successfully!", "success")
        return redirect(url_for("admin_news"))

    return render_template("admin/news/edit.html", news=news)


@app.route("/admin/news/delete/<int:id>", methods=["POST"])
@login_required
def admin_delete_news(id):
    # Fetch the news item to get its image_url before deleting
    resp = supabase.table("news_posts").select("image_url").eq("id", id).single().execute()
    news_data = resp.data

    if news_data and news_data.get("image_url"):
        delete_from_supabase_storage(news_data["image_url"], "news-and-events-images")

    supabase.table("news_posts").delete().eq("id", id).execute()
    flash("News item deleted successfully!", "success")
    return redirect(url_for("admin_news"))


# Setup initial admin user

@app.route("/setup", methods=["GET", "POST"])
def setup():
    users_resp = supabase.table("users").select("id").limit(1).execute()
    if users_resp.data and len(users_resp.data) > 0:
        flash("Setup has already been completed", "warning")
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")
        name = request.form.get("name")

        if password != confirm_password:
            flash("Passwords do not match", "danger")
            return redirect(url_for("setup"))

        password_hash = generate_password_hash(password)

        data = {
            "username": username,
            "password_hash": password_hash,
            "name": name,
            "role": "admin",
        }

        supabase.table("users").insert(data).execute()

        flash("Initial setup completed. You can now log in.", "success")
        return redirect(url_for("admin_login"))

    return render_template("admin/setup.html")


@app.errorhandler(500)
def internal_error(error):
    return f"500 error: {error}", 500

@app.template_filter("datetimeformat")
def datetimeformat(value, format="%B %d, %Y %I:%M %p"):
    """
    Convert an ISO‑8601 string or datetime into Asia/Manila time,
    then format it for display.
    """
    manila = pytz.timezone("Asia/Manila")
    # Accept either str or datetime
    if isinstance(value, str):
        dt = parser.isoparse(value)
    else:
        dt = value
    # Ensure timezone‑aware, then convert
    if dt.tzinfo is None:
        dt = manila.localize(dt)
    dt = dt.astimezone(manila)
    return dt.strftime(format)

@app.route("/credits")
def credit():
    return render_template("credits.html")

@app.route("/about")
def about():
    return render_template("about.html")
@app.route("/credits/alden_richards")
def alden_richards():
    return  render_template("alden.html")
if __name__ == "__main__":
    # Use 0.0.0.0 to be reachable in local network, change debug to False in production
    app.run(host="0.0.0.0", debug=True)#