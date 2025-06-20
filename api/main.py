import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
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
import traceback

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
    app.logger.info(f"delete_from_supabase_storage called with image_url: '{image_url}', bucket_name: '{bucket_name}'")
    if not image_url:
        app.logger.info("delete_from_supabase_storage: No image_url provided. Exiting.")
        return False # No URL, so nothing to delete, but not an error in deletion itself. Consider if True is better. For now, False.

    filename = ""
    try:
        app.logger.debug(f"Attempting to extract filename. Splitting URL '{image_url}' by '/{bucket_name}/'")
        parts = image_url.split(f"/{bucket_name}/")

        if len(parts) < 2 or not parts[1]:
            app.logger.warning(f"Could not extract valid filename. URL: '{image_url}', Bucket: '{bucket_name}'. Parts: {parts}. Exiting.")
            return False

        filename = parts[1]
        if '?' in filename: # Remove query parameters if they exist
            filename = filename.split('?')[0]
        app.logger.info(f"Extracted filename for deletion: '{filename}'")

        # Attempt to remove the file
        app.logger.info(f"Attempting Supabase storage.from_('{bucket_name}').remove(['{filename}'])")
        response_list = supabase.storage.from_(bucket_name).remove([filename])
        app.logger.info(f"Supabase raw response for deleting '{filename}': {response_list}")

        if response_list is None:
            app.logger.error(f"Supabase returned None response for deletion of '{filename}'. This is unexpected. Assuming failure.")
            return False

        # Default to True. If response_list is empty (e.g., file not found, or simple success), it's a success.
        # If there are items in response_list, we check them for errors.
        deletion_successful = True
        for item_idx, item in enumerate(response_list):
            app.logger.debug(f"Processing response item {item_idx} for '{filename}': {item}")
            if not isinstance(item, dict):
                app.logger.error(f"Response item {item_idx} for '{filename}' is not a dict: {item}. Marking as failure.")
                deletion_successful = False
                break # An invalid response item means we can't be sure, so flag as error

            # Check for an error field within the item. Supabase often uses 'error' key.
            # It could be None or an object.
            item_error = item.get("error")
            if item_error is not None: # If 'error' key exists and is not None, it's an error
                app.logger.error(f"Deletion failure reported by Supabase for '{filename}' (item {item_idx}). Error: '{item_error}'. Message: '{item.get('message', 'N/A')}'. Full item: {item}")
                deletion_successful = False
                break # Single file deletion, one error means failure
            else:
                # No 'error' key, or 'error' key is None. This is good.
                # Check for 'message' field for more info, some supabase versions might put "Successfully deleted" or similar here
                # item_message = item.get("message")
                # item_status_code = item.get("status_code", item.get("status"))
                # For now, the absence of an 'error' object is sufficient for success per item.
                app.logger.info(f"No error reported in response item {item_idx} for '{filename}'. Assuming success for this item. Item: {item}")

        app.logger.info(f"Exiting delete_from_supabase_storage for '{filename}'. Overall success: {deletion_successful}")
        return deletion_successful

    except Exception as e:
        # Log detailed error including filename if available
        log_filename = filename if filename else f"unknown (URL: {image_url})"
        app.logger.error(f"Exception in delete_from_supabase_storage for '{log_filename}' from bucket '{bucket_name}'. Type: {type(e).__name__}. Error: {str(e)}. Traceback: {traceback.format_exc()}")
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
    return render_template("home.html", bulletins=bulletins, news=news)


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
    patch_notes_resp = supabase.table("patch_notes").select("*").order("date", desc=True).execute()
    system_maintenance_resp = supabase.table("system_maintenance").select("*").order("start_time", desc=True).execute()

    bulletin_count = bulletin_count_resp.count or 0
    news_count = news_count_resp.count or 0
    patch_notes = patch_notes_resp.data or []
    system_maintenance = system_maintenance_resp.data or []

    return render_template(
        "admin/dashboard.html",
        bulletin_count=bulletin_count,
        news_count=news_count,
        patch_notes=patch_notes,
        system_maintenance=system_maintenance,
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

        if image_file and image_file.filename: # Check if a file was provided
            image_url = upload_to_supabase_storage(image_file, "bulletin-images")
            if image_url is None: # Check if upload failed
                flash("Image upload failed. Please try again.", "danger")
                return render_template("admin/bulletins/create.html")
        else:
            image_url = None


        data = {
            "title": title,
            "content": content,
            "is_active": is_active,
            "created_by": current_user.id,
            "date_posted": get_manila_time().isoformat(),
        }
        if image_url: # Only include image_url if it's not None
            data["image_url"] = image_url

        supabase.table("bulletin_posts").insert(data).execute()

        flash("Bulletin created successfully!", "success")
        return redirect(url_for("admin_bulletins"))

    return render_template("admin/bulletins/create.html")


@app.route("/admin/bulletins/edit/<int:id>", methods=["GET", "POST"])
@login_required
def admin_edit_bulletin(id):
    try:
        resp = supabase.table("bulletin_posts").select("*, image_url").eq("id", id).single().execute()
        bulletin_from_db = resp.data
    except Exception as e:
        app.logger.error(f"Error fetching bulletin id {id} for edit: {type(e).__name__} - {str(e)}")
        flash(f"An error occurred while fetching bulletin details.", "danger")
        return redirect(url_for("admin_bulletins"))

    if not bulletin_from_db:
        flash("Bulletin not found", "danger")
        return redirect(url_for("admin_bulletins"))

    form_data_for_template = bulletin_from_db.copy() # Use a copy for form data

    if request.method == "POST":
        form_data_for_template["title"] = request.form.get("title")
        form_data_for_template["content"] = request.form.get("content")
        form_data_for_template["is_active"] = bool(request.form.get("is_active"))

        image_file = request.files.get("image")
        remove_image = request.form.get("remove_image") == "true"

        current_db_image_url = bulletin_from_db.get("image_url")
        new_image_url_to_set = current_db_image_url

        # Image handling logic
        if remove_image:
            if current_db_image_url:
                if not delete_from_supabase_storage(current_db_image_url, "bulletin-images"):
                    flash("Failed to delete the current image. Item not updated.", "danger")
                    # form_data_for_template['image_url'] is already current_db_image_url
                    return render_template("admin/bulletins/edit.html", bulletin=form_data_for_template)
                new_image_url_to_set = None
            else: # No image to remove
                new_image_url_to_set = None
        elif image_file and image_file.filename: # Check filename to ensure a file was actually uploaded
            if current_db_image_url:
                if not delete_from_supabase_storage(current_db_image_url, "bulletin-images"):
                    flash("Failed to delete old image before uploading new. Item not updated.", "danger")
                    # form_data_for_template['image_url'] is already current_db_image_url
                    return render_template("admin/bulletins/edit.html", bulletin=form_data_for_template)

            uploaded_image_url = upload_to_supabase_storage(image_file, "bulletin-images")
            if not uploaded_image_url:
                flash("New image upload failed. Item not updated.", "danger")
                # form_data_for_template['image_url'] is already current_db_image_url
                return render_template("admin/bulletins/edit.html", bulletin=form_data_for_template)
            new_image_url_to_set = uploaded_image_url

        form_data_for_template["image_url"] = new_image_url_to_set

        # Prepare data for DB update
        update_data_for_db = {
            "title": form_data_for_template["title"],
            "content": form_data_for_template["content"],
            "is_active": form_data_for_template["is_active"],
        }

        if new_image_url_to_set != current_db_image_url:
            update_data_for_db["image_url"] = new_image_url_to_set

        # Database operation
        if not update_data_for_db and new_image_url_to_set == current_db_image_url : # Check if there's anything to update
             flash("No changes detected.", "info")
             return redirect(url_for("admin_bulletins"))

        try:
            response = supabase.table("bulletin_posts").update(update_data_for_db).eq("id", id).execute()
            # Supabase Python client typically raises an exception for HTTP errors (4xx, 5xx)
            # but we can add a check for data if needed, though often an empty response.data on UPDATE is normal.
            if hasattr(response, 'error') and response.error:
                 app.logger.error(f"Supabase API error updating bulletin {id}: {response.error}")
                 flash(f"Database update failed: {response.error.message}", "danger")
                 return render_template("admin/bulletins/edit.html", bulletin=form_data_for_template)

            flash("Bulletin updated successfully!", "success")
            return redirect(url_for("admin_bulletins"))
        except Exception as e:
            app.logger.error(f"Error updating bulletin {id} in DB: {type(e).__name__} - {str(e)}")
            flash(f"An unexpected error occurred while updating the bulletin: {str(e)}", "danger")
            return render_template("admin/bulletins/edit.html", bulletin=form_data_for_template)

    # For GET request
    return render_template("admin/bulletins/edit.html", bulletin=form_data_for_template)


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

        if image_file and image_file.filename: # Check if a file was provided
            image_url = upload_to_supabase_storage(image_file, "news-and-events-images")
            if image_url is None: # Check if upload failed
                flash("Image upload failed. Please try again.", "danger")
                return render_template("admin/news/create.html")
        else:
            image_url = None

        data = {
            "title": title,
            "content": content,
            "is_active": is_active,
            "created_by": current_user.id,
            "date_posted": get_manila_time().isoformat(),
        }
        if image_url: # Only include image_url if it's not None
            data["image_url"] = image_url

        supabase.table("news_posts").insert(data).execute()

        flash("News item created successfully!", "success")
        return redirect(url_for("admin_news"))

    return render_template("admin/news/create.html")


@app.route("/admin/news/edit/<int:id>", methods=["GET", "POST"])
@login_required
def admin_edit_news(id):
    try:
        resp = supabase.table("news_posts").select("*, image_url").eq("id", id).single().execute()
        news_from_db = resp.data
    except Exception as e:
        app.logger.error(f"Error fetching news item id {id} for edit: {type(e).__name__} - {str(e)}")
        flash(f"An error occurred while fetching news item details.", "danger")
        return redirect(url_for("admin_news"))

    if not news_from_db:
        flash("News item not found", "danger")
        return redirect(url_for("admin_news"))

    form_data_for_template = news_from_db.copy() # Use a copy for form data

    if request.method == "POST":
        form_data_for_template["title"] = request.form.get("title")
        form_data_for_template["content"] = request.form.get("content")
        form_data_for_template["is_active"] = bool(request.form.get("is_active"))

        image_file = request.files.get("image")
        remove_image = request.form.get("remove_image") == "true"

        current_db_image_url = news_from_db.get("image_url")
        new_image_url_to_set = current_db_image_url

        # Image handling logic
        if remove_image:
            if current_db_image_url:
                if not delete_from_supabase_storage(current_db_image_url, "news-and-events-images"):
                    flash("Failed to delete the current image. Item not updated.", "danger")
                    return render_template("admin/news/edit.html", news=form_data_for_template)
                new_image_url_to_set = None
            else: # No image to remove
                new_image_url_to_set = None
        elif image_file and image_file.filename: # Check filename to ensure a file was actually uploaded
            if current_db_image_url:
                if not delete_from_supabase_storage(current_db_image_url, "news-and-events-images"):
                    flash("Failed to delete old image before uploading new. Item not updated.", "danger")
                    return render_template("admin/news/edit.html", news=form_data_for_template)

            uploaded_image_url = upload_to_supabase_storage(image_file, "news-and-events-images")
            if not uploaded_image_url:
                flash("New image upload failed. Item not updated.", "danger")
                return render_template("admin/news/edit.html", news=form_data_for_template)
            new_image_url_to_set = uploaded_image_url

        form_data_for_template["image_url"] = new_image_url_to_set

        # Prepare data for DB update
        update_data_for_db = {
            "title": form_data_for_template["title"],
            "content": form_data_for_template["content"],
            "is_active": form_data_for_template["is_active"],
        }

        if new_image_url_to_set != current_db_image_url:
            update_data_for_db["image_url"] = new_image_url_to_set

        # Database operation
        if not update_data_for_db and new_image_url_to_set == current_db_image_url: # Check if there's anything to update
             flash("No changes detected.", "info")
             return redirect(url_for("admin_news"))

        try:
            response = supabase.table("news_posts").update(update_data_for_db).eq("id", id).execute()
            if hasattr(response, 'error') and response.error:
                 app.logger.error(f"Supabase API error updating news item {id}: {response.error}")
                 flash(f"Database update failed: {response.error.message}", "danger")
                 return render_template("admin/news/edit.html", news=form_data_for_template)

            flash("News & Events updated successfully!", "success")
            return redirect(url_for("admin_news"))
        except Exception as e:
            app.logger.error(f"Error updating news item {id} in DB: {type(e).__name__} - {str(e)}")
            flash(f"An unexpected error occurred while updating the news item: {str(e)}", "danger")
            return render_template("admin/news/edit.html", news=form_data_for_template)

    # For GET request
    return render_template("admin/news/edit.html", news=form_data_for_template)


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


# Patch Notes API Endpoints
@app.route("/api/patch-notes", methods=["GET"])
@login_required # Assuming only logged-in admins should access this, adjust if needed
def get_all_patch_notes():
    try:
        response = supabase.table("patch_notes").select("*").order("date", desc=True).execute()
        if response.data:
            return jsonify(response.data)
        else:
            app.logger.error(f"Error fetching all patch notes: {getattr(response, 'error', 'Unknown error')}")
            return jsonify({"error": "Failed to fetch patch notes", "details": str(getattr(response, 'error', '')) }), 500
    except Exception as e:
        app.logger.error(f"Exception in get_all_patch_notes: {str(e)}")
        return jsonify({"error": "An unexpected error occurred", "details": str(e)}), 500

@app.route("/api/patch-notes/latest", methods=["GET"])
@login_required # Assuming only logged-in admins should access this
def get_latest_patch_note():
    try:
        response = supabase.table("patch_notes").select("*").order("date", desc=True).limit(1).single().execute()
        if response.data:
            return jsonify(response.data)
        else:
            # .single() returns an error in response.error if not found or multiple found
            # However, PostgrestAPIError might be raised before this for other issues.
            # If data is empty and no error object, it means no records found which is not an "error" state.
            if getattr(response, 'error', None):
                 app.logger.error(f"Error fetching latest patch note: {response.error}")
                 return jsonify({"error": "Failed to fetch latest patch note", "details": str(response.error)}), 500
            return jsonify(None), 200 # Return 200 with null body if no patch note exists
    except Exception as e:
        # Handle cases where .single() might raise an exception if data is not a single row (though limit(1) should prevent this)
        # or other unexpected errors.
        app.logger.error(f"Exception in get_latest_patch_note: {str(e)}")
        return jsonify({"error": "An unexpected error occurred", "details": str(e)}), 500

# System Maintenance API Endpoints
@app.route("/api/system-maintenance", methods=["GET"])
@login_required # Assuming only logged-in admins should access this
def get_all_system_maintenance():
    try:
        response = supabase.table("system_maintenance").select("*").order("start_time", desc=True).execute()
        if response.data:
            return jsonify(response.data)
        else:
            app.logger.error(f"Error fetching all system maintenance: {getattr(response, 'error', 'Unknown error')}")
            return jsonify({"error": "Failed to fetch system maintenance messages", "details": str(getattr(response, 'error', '')) }), 500
    except Exception as e:
        app.logger.error(f"Exception in get_all_system_maintenance: {str(e)}")
        return jsonify({"error": "An unexpected error occurred", "details": str(e)}), 500

@app.route("/api/system-maintenance/latest", methods=["GET"])
@login_required # Assuming only logged-in admins should access this
def get_latest_system_maintenance():
    try:
        now = datetime.now(pytz.utc).isoformat() # Ensure timezone aware comparison
        # Fetches the most recent maintenance message that is currently active or upcoming.
        # Orders by start_time descending to get the latest if multiple fit criteria.
        response = (
            supabase.table("system_maintenance")
            .select("*")
            .lte("start_time", now) # Maintenance should have started
            .gte("end_time", now)   # And not yet ended
            .order("start_time", desc=True)
            .limit(1)
            .single()
            .execute()
        )

        # If no active maintenance, try to find the next upcoming one
        if not response.data and not getattr(response, 'error', None):
            response = (
                supabase.table("system_maintenance")
                .select("*")
                .gt("start_time", now) # Future start time
                .order("start_time", desc=False) # Ascending to get the soonest
                .limit(1)
                .single()
                .execute()
            )

        if response.data:
            return jsonify(response.data)
        else:
            if getattr(response, 'error', None):
                app.logger.error(f"Error fetching latest system maintenance: {response.error}")
                # Distinguish between "not found" and actual errors if possible,
                # but .single() can make this tricky. For now, a generic error.
                return jsonify({"error": "Failed to fetch latest system maintenance", "details": str(response.error)}), 500
            return jsonify(None), 200 # Return 200 with null body if no relevant maintenance message exists

    except Exception as e:
        app.logger.error(f"Exception in get_latest_system_maintenance: {str(e)}")
        return jsonify({"error": "An unexpected error occurred", "details": str(e)}), 500

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
#@app.route("/credits/alden_richards")
#def alden_richards():
    #return  render_template("alden.html")

@app.route("/coming_soon")
def coming_soon():
    return render_template("coming_soon.html")

@app.route("/admin/brgy_certificate_requests")
@login_required
def brgy_certificate_requests():
    return render_template("admin/brgy_certificate_requests.html")

@app.route("/admin/business_permit_requests")
@login_required
def business_permit_requests():
    return render_template("admin/business_permit_requests.html")

@app.route("/admin/reports_and_concerns")
@login_required
def reports_and_concerns():
    return render_template("admin/reports_and_concerns.html")

if __name__ == "__main__":
    # Use 0.0.0.0 to be reachable in local network, change debug to False in production
    app.run(host="0.0.0.0", debug=True)#
